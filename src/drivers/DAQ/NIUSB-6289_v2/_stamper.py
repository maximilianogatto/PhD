"""
Timestamping a signal against the AI sample clock, as scan indices.

The mechanism the 1 pps counter uses, with the 1 pps taken out of it. Both
subsystems that need it inherit from here:

    PPSCounter     the rubidium's 1 pps  -> the RULER, one atomic second apart
    MarkerCounter  the OPX's markers     -> EVENTS, whenever they happen

It is one trick, and it looks backwards until you see why:

    what is counted    ai/SampleClock, the board's own AI sample clock. The
                       counter's value is "how many scans have been taken".
    what latches it    the signal on a PFI line, used as the counter task's
                       SAMPLE CLOCK.

So every value the counter returns is *the scan index that was current when
the wire pulsed*. The wire does not care what it carries and neither does this
class: point it at the rubidium and you get atomic seconds, point it at a
marker and you get events. Either way the answer is in scan indices - the same
units as the data, the same units as everything else in this driver - so
relating an event to a sample is subtraction, not conversion.

WHY NOT SAMPLE THE SIGNAL ON AN ANALOG CHANNEL. The ADC samples
instantaneously, so a 10 us pulse at 25 kS/s is caught about a quarter of the
time. A counter is edge-triggered: it cannot miss one.

WHY A THREAD. This board hands counter values over only to a BLOCKING read; a
poll returns nothing however long you wait, and avail_samp_per_chan sits at 0.
Reading from the acquisition loop would mean paying a timeout on every chunk
with no pulse in it. So the read lives in its own thread: it blocks (costing no
CPU), wakes the instant a pulse arrives, and puts the value in a queue. The
acquisition loop then takes from the queue without ever blocking - the same
split as the AI stream, each side draining its own buffer.

A TRIGGER DOES NOT NEED ONE OF THESE. The board has two counters and it can
look like three things want one - the 1 pps, the markers, and the trigger. It
is two: a start trigger is not timestamped, it DEFINES scan 0, so there is
nothing to record and the timing engine handles it directly
(cfg_dig_edge_start_trig). Only the two signals that must be placed IN the
data consume a counter.

ORDER MATTERS. Start the counter BEFORE the AI task. It counts the AI sample
clock, so if the clock is already running when the counter starts, the first
latched value is measured from a start you never observed.

AND THE CLOCK CAN BE STOPPED. Whenever the AI task is not clocking - waiting
for a start trigger, or restarting after a long_run gap - the count freezes
and every pulse in that window latches the SAME value. Those repeats are not
timestamps; they are the counter saying "nothing has happened since". Callers
that can validate them (PPSCounter, via whole seconds) throw them out; callers
that cannot (MarkerCounter) keep them and say so.
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
READ_TIMEOUT = 1.5          # s; longer than the 1 s between 1 pps pulses


class ScanStamper(InstrumentModule):
    """A counter that records the scan index of every pulse on a PFI line.

    Subclasses add meaning: what the pulses ARE, and what can be checked about
    them. This class only knows how to catch them.
    """

    #: what subclasses call the thing they timestamp, for error messages
    WHAT = "pulse"

    def __init__(self, parent, name, default_counter=-1):
        super().__init__(parent, name)

        caps = parent.caps
        self._counters = caps["ctrs"] or ("ctr0", "ctr1")

        # --- live task state
        self._task = None            # nidaqmx.Task while running
        self._thread = None          # the blocking reader
        self._q = queue.Queue()      # values waiting to be collected
        self._halt = threading.Event()

        # --- 32-bit unwrap state
        self._raw_last = None        # last raw counter value seen
        self._offset = 0             # 2**32 added per rollover

        self.add_parameter(
            "terminal", label=f"{name} input terminal",
            get_cmd=None, set_cmd=None, initial_value=None,
            vals=Enum(*caps["terminals"], None),
            docstring=f"PFI terminal carrying the {self.WHAT}s, e.g. "
                      f"'/Dev1/PFI9' (screw terminal 83). None disables this "
                      f"counter. It is the counter task's SAMPLE CLOCK, not "
                      f"the signal being counted - see the module docstring. "
                      f"Must be a PHYSICAL terminal: the pulse comes from "
                      f"outside the board. daq.terminals() lists them, and "
                      f"PFI 8-15 each sit next to a D GND pin.")

        self.add_parameter(
            "counter", label=f"counter used by {name}",
            get_cmd=None, set_cmd=None,
            initial_value=self._counters[default_counter],
            vals=Enum(*self._counters),
            docstring=f"which counter latches the scan count. This board "
                      f"reports {list(self._counters)}. There are only two, "
                      f"and the root arbitrates them - see claim_counter.")

        self.add_parameter(
            "running", label=f"{name} counter running", set_cmd=False,
            get_cmd=lambda: self._task is not None)

    # ========================================================= task building
    def _build_task(self):
        """Count ai/SampleClock, latched by whatever is on `terminal`."""
        if not self.terminal():
            raise ValueError(
                f"{self.short_name}.terminal is not set, so there is nothing "
                f"to latch on. Set daq.{self.short_name}.terminal('/Dev1/PFI9')"
                f" - daq.terminals() lists the ones a cable can reach.")

        device = self.root_instrument.device
        task = nidaqmx.Task()
        try:
            channel = task.ci_channels.add_ci_count_edges_chan(f"{device}/{self.counter()}")
            channel.ci_count_edges_term = f"/{device}/ai/SampleClock"

            # Ask the device to hand each latched value over as soon as it
            # exists. The default waits for the onboard buffer to be half
            # full, which at one pulse a second is minutes away - which is why
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
                channel.ci_data_xfer_req_cond = (InputDataTransferCondition.ON_BOARD_MEMORY_NOT_EMPTY)
            except nidaqmx.errors.DaqError as e:
                self.log.info("device declined eager counter transfer (%s); "
                              "using blocking reads", e.error_code)

            task.timing.cfg_samp_clk_timing(
                rate=1.0, source=self.terminal(), active_edge=Edge.RISING,
                sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=16)      # small: these arrive rarely
        except Exception:
            task.close()
            raise
        return task

    # =============================================================== control
    def start(self):
        """Start the counter and the thread that drains it.

        Call this BEFORE starting the AI task: it counts the AI sample clock,
        so it has to be watching before that clock runs.
        """
        if self._task is not None:
            raise RuntimeError(
                f"the {self.short_name} counter is already running")

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
                    value = task.read(number_of_samples_per_channel=1,
                                      timeout=READ_TIMEOUT)
                except nidaqmx.errors.DaqError as e:
                    # A read timeout is normal and costs READ_TIMEOUT, so
                    # looping straight back is right for it. Anything that
                    # fails IMMEDIATELY - a disconnected card, an aborted
                    # task - would spin this thread at 100% CPU for the rest
                    # of a week-long run, so back off and eventually give up.
                    # The run itself continues; it just stops getting values,
                    # which the manifest will show.
                    consecutive_errors += 1
                    if consecutive_errors >= MAX_DRAIN_ERRORS:
                        self.log.error(
                            "%s counter failed %d times in a row (%s); giving "
                            "up for this run", self.short_name,
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
        """Values accumulated since the last call, as global scan indices.

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
