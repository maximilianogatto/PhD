"""
The 1 pps counter of the NI USB-6289, as a QCoDeS instrument module.

WHAT IT MEASURES. The FS725 rubidium emits one pulse per ATOMIC second. The
board's own clock does not know it is wrong - it divides its 20 MHz timebase
and reports a sample rate with great confidence, and over a day that rate is
off by seconds. Comparing the two is the whole point of this module: it
records, for every atomic second, how many samples the board had taken by
then. Those numbers ARE the true sample rate, in samples per atomic second,
and they are what makes a multi-day time axis mean anything.

HOW, AND WHY IT LOOKS BACKWARDS. The obvious approach is to feed the 1 pps
into an analog channel and look for the pulse in the data. That fails: the ADC
samples instantaneously, so a 10 us pulse at 25 kS/s is caught only about 25%
of the time, and the edge table comes out full of random holes.

So the pulse goes to a PFI terminal and a counter is used instead - but with
the roles inverted from what you would expect:

    what is counted    ai/SampleClock, i.e. the board's own AI sample clock.
                       The counter's value is "how many scans have been taken
                       so far".
    what latches it    the 1 pps on the PFI line, used as the counter task's
                       SAMPLE CLOCK.

So each value the counter returns is the scan index that was current when an
atomic second arrived. That is the edge table - complete, edge-triggered, with
nothing missed, and expressed in the same units as the data itself.

    daq.pps.terminal("/Dev1/PFI9")     # 1 pps -> screw terminal 83
    daq.pps.counter("ctr1")            # leaves ctr0 free
    data, edges = daq.acquire_with_pps()
    t = times_from_edges(np.arange(len(data["ai0"])), edges)

MULTICHANNEL CHANGES NOTHING HERE. ai/SampleClock ticks once per SCAN, not
once per conversion, so an edge index means the same thing whether one channel
or six are enabled. (ai/ConvertClock is the per-channel one. Never count
that - the numbers would be meaningless the moment a channel is added.)

WHY A THREAD. This board hands counter values over only to a BLOCKING read; a
poll returns nothing however long you wait, and avail_samp_per_chan sits at 0.
Reading from the acquisition loop would mean paying a timeout on every chunk
that contains no edge. So the read lives in its own thread: it blocks (costing
no CPU), wakes the instant an edge arrives, and puts the value in a queue. The
acquisition loop then takes from the queue without ever blocking - the same
split as the AI stream, each side draining its own buffer.

ORDER MATTERS. Start the counter BEFORE the AI task. It counts the AI sample
clock, so if the clock is already running when the counter starts, the first
latched value is measured from a start you never observed.
"""

import queue
import threading

import numpy as np
import nidaqmx
from nidaqmx.constants import (
    AcquisitionType, Edge, InputDataTransferCondition,
)

from qcodes.instrument import InstrumentModule
from qcodes.validators import Enum

COUNTER_BITS = 32           # rolls over every 47.7 h at 25 kS/s
DRAIN_ERROR_BACKOFF = 1.0   # s, after a counter read that failed immediately
MAX_DRAIN_ERRORS = 30       # consecutive failures before the reader gives up


class PPSCounter(InstrumentModule):
    """One counter, dedicated to timestamping atomic seconds.

    No ChannelList: this is a subsystem with no repeated parts. It uses ONE of
    the board's counters, named by the `counter` parameter, and claims it from
    the root instrument so nothing else can take it while a run is going.
    """

    def __init__(self, parent, name="pps"):
        super().__init__(parent, name)

        caps = parent.caps
        self._counters = caps["ctrs"] or ("ctr0", "ctr1")

        # --- live task state
        self._task = None            # nidaqmx.Task while running
        self._thread = None          # the blocking reader
        self._q = queue.Queue()      # edges waiting to be collected
        self._halt = threading.Event()

        # --- 32-bit unwrap state
        self._raw_last = None        # last raw counter value seen
        self._offset = 0             # 2**32 added per rollover

        self._last_edges = None      # edges of the last completed record

        self.add_parameter(
            "terminal", label="1 pps terminal",
            get_cmd=None, set_cmd=None, initial_value=None,
            vals=Enum(*caps["terminals"], None),
            docstring="PFI terminal carrying the FS725 1 pps, e.g. "
                      "'/Dev1/PFI9' (screw terminal 83). None disables the "
                      "counter entirely. This is the counter task's SAMPLE "
                      "CLOCK, not the signal being counted - see the module "
                      "docstring. A PFI input is edge-triggered, so a 10 us "
                      "pulse is caught every time; an analog channel would "
                      "miss ~75% of them at 25 kS/s.")

        self.add_parameter(
            "counter", label="counter used for the 1 pps",
            get_cmd=None, set_cmd=None, initial_value=self._counters[-1],
            vals=Enum(*self._counters),
            docstring=f"which counter latches the scan count. This board "
                      f"reports {list(self._counters)}. Defaults to the last "
                      f"one, leaving {self._counters[0]} free for other work.")

        self.add_parameter(
            "running", label="1 pps counter running", set_cmd=False,
            get_cmd=lambda: self._task is not None)

        self.add_parameter(
            "n_edges", label="atomic seconds in the last record",
            unit="edges", set_cmd=False,
            get_cmd=lambda: 0 if self._last_edges is None
            else int(len(self._last_edges)))

        self.add_parameter(
            "measured_rate", label="true sample rate", unit="scans/atomic s",
            set_cmd=False, get_cmd=self._measured_rate,
            docstring="mean scans between consecutive 1 pps edges of the last "
                      "record - the board's real rate, measured against the "
                      "rubidium. nan until a record with two or more edges "
                      "has been taken. Register it in a long measurement and "
                      "you get the drift curve for free.")

        self.add_parameter(
            "ppm", label="board clock error", unit="ppm",
            set_cmd=False, get_cmd=self._ppm,
            docstring="(nominal - true) / true, in parts per million. "
                      "Positive means the board is SLOW: it takes fewer "
                      "samples per atomic second than it thinks. Multiply by "
                      "0.0864 for seconds of drift per day.")

    # ================================================================ state
    @property
    def last_edges(self):
        """Edges of the last completed record, as global scan indices.

        None if no record has been taken with the counter running. This is
        what the atomic time axis reads.
        """
        return self._last_edges

    def _measured_rate(self):
        if self._last_edges is None or len(self._last_edges) < 2:
            return float("nan")
        return float(np.diff(self._last_edges).mean())

    def _ppm(self):
        true_rate = self._measured_rate()
        if not np.isfinite(true_rate) or true_rate == 0:
            return float("nan")
        nominal = self.root_instrument.ai.actual_rate()
        return (nominal - true_rate) / true_rate * 1e6

    # ========================================================= task building
    def _build_task(self):
        """The counter task: counts ai/SampleClock, clocked by the 1 pps."""
        if not self.terminal():
            raise ValueError(
                "pps.terminal is not set, so there is no 1 pps to latch on. "
                "Set daq.pps.terminal('/Dev1/PFI9') (screw terminal 83).")

        device = self.root_instrument.device
        task = nidaqmx.Task()
        try:
            channel = task.ci_channels.add_ci_count_edges_chan(f"{device}/{self.counter()}")
            channel.ci_count_edges_term = f"/{device}/ai/SampleClock"

            # Ask the device to hand each latched value over as soon as it
            # exists. The default waits for the onboard buffer to be half
            # full, which at 1 sample/second is minutes away - which is why
            # avail_samp_per_chan reads 0 and only a blocking read returns
            # anything.
            #
            # This USB board rejects the setting on a counter task, so a
            # DaqError here is expected and harmless: the reader thread uses
            # blocking reads and works either way. An AttributeError is NOT
            # caught - that would mean the constant name is wrong, which is a
            # bug in this file, not a device limitation. (Conflating the two
            # is what made this take several rounds to find.)
            try:
                channel.ci_data_xfer_req_cond = (
                    InputDataTransferCondition.ON_BOARD_MEMORY_NOT_EMPTY)
            except nidaqmx.errors.DaqError as e:
                self.log.info("device declined eager counter transfer (%s); "
                              "using blocking reads", e.error_code)

            task.timing.cfg_samp_clk_timing(
                rate=1.0, source=self.terminal(), active_edge=Edge.RISING,
                sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=16)      # small: 1 pps is 1 sample/second
        except Exception:
            task.close()
            raise
        return task

    # =============================================================== control
    def start(self):
        """Start the counter and the thread that drains it.

        Call this BEFORE starting the AI task: the counter counts the AI
        sample clock, so it has to be watching before that clock runs.
        """
        if self._task is not None:
            raise RuntimeError("the 1 pps counter is already running")

        self.root_instrument.claim_counter(self.counter(), self.full_name)
        try:
            self._task = self._build_task()
        except Exception:
            self.root_instrument.release_counter(self.counter())
            raise

        self._q = queue.Queue()     # fresh: no leftovers from a previous run
        self._halt.clear()
        # A new counter task starts counting from 0, so the unwrap state has
        # to start over too. Without this, the first value of a SECOND run
        # looks smaller than the last value of the first one, a rollover is
        # falsely detected, and every index gains a spurious 2**32.
        self._raw_last = None
        self._offset = 0

        task = self._task

        def drain():
            consecutive_errors = 0
            while not self._halt.is_set():
                try:
                    # 1.5 s > the 1 s between edges, so a timeout is rare and
                    # only serves to re-check the halt flag.
                    value = task.read(number_of_samples_per_channel=1, timeout=1.5)
                except nidaqmx.errors.DaqError as e:
                    # A read timeout is normal and costs 1.5 s, so looping
                    # straight back is right for it. Anything that fails
                    # IMMEDIATELY - a disconnected card, an aborted task -
                    # would spin this thread at 100% CPU for the rest of a
                    # week-long run, so back off and eventually give up. The
                    # run itself continues; it just stops getting edges, which
                    # the manifest and n_edges will show.
                    consecutive_errors += 1
                    if consecutive_errors >= MAX_DRAIN_ERRORS:
                        self.log.error(
                            "1 pps counter failed %d times in a row (%s); "
                            "giving up on edges for this run",
                            consecutive_errors, e)
                        return
                    self._halt.wait(DRAIN_ERROR_BACKOFF)
                    continue
                except Exception:              # task closed under us
                    return
                consecutive_errors = 0
                self._q.put(int(np.asarray(value).ravel()[0]))

        # Everything from here can fail with the task built and the counter
        # claimed, so unwind both rather than leaving the module wedged in a
        # state where start() says "already running" and only stop() clears it.
        try:
            task.start()
            self._thread = threading.Thread(target=drain, daemon=True,
                                            name=f"{self.full_name}-drain")
            self._thread.start()
        except Exception:
            self._halt.set()
            self._thread = None
            try:
                task.close()
            except Exception:
                pass
            self._task = None
            self.root_instrument.release_counter(self.counter())
            raise

    def stop(self):
        """Stop the reader thread and close the counter task.

        Joins the thread, so any read still in flight has finished and its
        value is already in the queue - collect() after this is race-free.
        Safe to call when nothing is running; the root's close() does.
        """
        self._halt.set()
        if self._thread is not None:
            self._thread.join(3.0)
            self._thread = None
        if self._task is not None:
            try:
                self._task.stop()
                self._task.close()
            except Exception:
                pass
            self._task = None
            self.root_instrument.release_counter(self.counter())

    def collect(self):
        """Edges accumulated since the last call, as global scan indices.

        Never blocks - it only drains the queue the reader thread fills.

        Also unwraps the counter, which is 32-bit: at 25 kS/s it rolls over
        every 47.7 hours, three times in a week-long run. Unwrapping is
        stateful, so call this in order and never on a stale queue.
        """
        raw = []
        while True:
            try:
                raw.append(self._q.get_nowait())
            except queue.Empty:
                break

        out = np.empty(len(raw), dtype=np.int64)
        for i, value in enumerate(raw):
            if self._raw_last is not None and value < self._raw_last:
                self._offset += 1 << COUNTER_BITS        # rolled over
            self._raw_last = value
            out[i] = value + self._offset
        return out

    def set_last_edges(self, edges):
        """Record the edge table of a completed record.

        Called by whoever coordinated the acquisition (the root's
        acquire_with_pps, or long_run). Kept explicit rather than done inside
        collect(), because a streaming run collects many times per record and
        only the coordinator knows where a record ends.
        """
        self._last_edges = np.asarray(edges, dtype=np.int64)
        return self._last_edges

    # ================================================================ checks
    def check(self):
        """Problems with the current 1 pps configuration.

        Returns a list of (level, where, message). See USB6289.check().
        """
        problems = []
        if not self.terminal():
            problems.append((
                "warning", "pps",
                "terminal is not set, so no atomic seconds are recorded. "
                "acquire() still works, but the time axis is the board's own "
                "clock - ~4 s/day of drift - and long_run() will refuse to "
                "start. Set daq.pps.terminal('/Dev1/PFI9') (terminal 83)."))
            return problems

        owner = self.root_instrument._counter_owners.get(self.counter())
        if owner is not None and owner != self.full_name:
            problems.append((
                "error", "pps",
                f"counter {self.counter()} is held by {owner}"))
        return problems

    # ============================================================== metadata
    def describe(self):
        return {"terminal": self.terminal(),
                "counter": self.counter(),
                "counted_signal": f"/{self.root_instrument.device}/ai/SampleClock",
                "running": self._task is not None,
                "n_edges_last": self.n_edges(),
                "measured_rate": self._measured_rate(),
                "ppm": self._ppm()}
