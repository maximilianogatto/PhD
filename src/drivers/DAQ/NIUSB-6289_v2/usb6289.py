"""
QCoDeS driver for the NI USB-6289 - v2, split into instrument submodules.

STATUS. Everything from v1 is ported: the three subsystems, long_run() and
wiring(). Exercised on the real board: DC levels, waveform generation,
clock_check against the FS725 rubidium, and a 300 s triggered acquisition
with the 1 pps, the AI trigger and the AO trigger all on PFI8.

The organisation of the whole driver - what each class is and why - is
documented on the USB6289 class below. The short version: the v1 driver
spelled the subsystem tree with an underscore convention (ai_rate, ao_freq,
pps_terminal), and submodules make it structural. Parameter full_names are
unchanged - daq.ai.rate is still `daq_ai_rate` - so existing databases keep
working.

Not importable as a package: the directory name has a hyphen. Same bootstrap
as v1:

    import sys; sys.path.insert(0, ".../NIUSB-6289_v2")
    from usb6289 import USB6289

    daq = USB6289("daq", device="Dev1")
    daq.ai.enable("ai0", "ai3")
    daq.ai.rate(25_000)
    daq.ai.duration(10)
    data = daq.ai.acquire()           # {'ai0': ..., 'ai3': ...}
"""

import json
import queue
import sys
import textwrap
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import nidaqmx
from qcodes.instrument import Instrument

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _ai import AnalogInput      # noqa: E402  (after the path bootstrap)
from _pps import PPSCounter      # noqa: E402
from _ao import AnalogOutput     # noqa: E402
from _longrun import LongRun, SegmentWriter    # noqa: E402
from postprocess import _hdf5_worker           # noqa: E402

try:
    from pinout_6289 import nearest_ground, pin_of
except ImportError:                            # the driver still works
    pin_of = nearest_ground = None

# Fallbacks only - the real numbers come from the device. Measured on a
# USB-6289: single 666,666.67 S/s (20 MHz / 30), multi-channel 500,000 S/s
# AGGREGATE. The two limits are different, so both are queried.
AI_MAX_SINGLE_FALLBACK = 666_666.67
AI_MAX_MULTI_FALLBACK = 500_000.0
AI_CHANNELS = tuple(f"ai{i}" for i in range(32))
AO_CHANNELS = tuple(f"ao{i}" for i in range(4))    # terminals 15 / 31 / 47 / 63
COUNTERS = ("ctr0", "ctr1")
PFI_LINES = tuple(f"PFI{i}" for i in range(16))
AI_RANGES = (0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)   # M Series programmable gains


class USB6289(Instrument):
    """The NI USB-6289, as a tree of QCoDeS submodules.

    ================================================================= THE TREE

        USB6289                       Instrument
        |-- device, caps              the DAQmx handle + what the board reports
        |-- _counter_owners           arbitration for the 2 counters
        |
        |-- ai                        InstrumentModule     <- the AI task
        |   |-- rate, duration, trigger, conv_rate, actual_rate, n_samples
        |   |-- time_axis, atom_time_axis          (custom Parameter classes)
        |   |-- ai0 ... ai31          InstrumentChannel x32
        |   |   `-- enabled, v_min, v_max, terminal_config, time_offset, trace
        |   `-- channels              ChannelList - the same 32, as a group
        |
        |-- pps                       InstrumentModule     <- the 1 pps counter
        |   `-- terminal, counter, running, n_edges, measured_rate, ppm
        |                             (no channels: one counter, one job)
        |
        `-- ao                        InstrumentModule     <- the AO task
            |-- shape, freq, amp, offset, rate, duty, trigger, wave_channel
            |-- actual_freq, running, dc_levels, fifo_samples, buffer_len
            |-- ao0 ... ao3           InstrumentChannel x4
            |   `-- role, dc
            `-- channels              ChannelList - the same 4, as a group

    36 channel objects, 3 modules, 1 instrument.

    =============================================== WHAT EACH LEVEL MAY OWN

    USB6289 - Instrument. The physical board. The only object you construct,
    the only one with a name in QCoDeS's global registry, and the only one
    with a close(). It owns three kinds of thing:

      * what belongs to the BOARD: device, caps (queried once, before any
        submodule, because their validators are built from it), and the
        counter claims;
      * anything needing TWO SUBSYSTEMS AT ONCE: acquire_with_pps (the counter
        must start before the AI clock), clock_check (ai + pps + optionally
        ao), long_run (ai + pps);
      * lifecycle. close() has to tear down ao and pps by hand, because
        submodules have no close hook of their own.

    ai / pps / ao - InstrumentModule. One of each. A module is a SUBSYSTEM: a
    task, a clock, a trigger. It owns every setting the hardware decides once
    for the whole task - you cannot give two AI pins different sample rates,
    so `rate` lives here and not on the channel. `pps` shows that a module is
    not about size or repetition: it has no channels at all, because there is
    one counter doing one job.

    AIChannel / AOChannel - InstrumentChannel. 32 and 4 identical copies. A
    channel owns only what the HARDWARE lets each pin decide for itself: an AI
    pin has its own programmable gain (v_min/v_max) and terminal
    configuration; an AO pin has a role and a level. Nothing about time lives
    here. They must be InstrumentChannel rather than InstrumentModule for one
    mechanical reason: ChannelList type-checks its members against it.

    channels - ChannelList. Not new objects: daq.ai.ai0 and daq.ai.channels[0]
    are the same instance, reachable two ways. The list exists for broadcast -
    daq.ao.channels.dc() returns all four levels in one call.

    The ai/ao asymmetry is the hardware's, not a design choice: an AI pin is
    either in the task or not (`enabled`), while an AO pin has three states
    (`role`), because the board can drive it either from the clocked buffer or
    from an untimed write.

    ========================================= THE RULE THAT DECIDES PLACEMENT

    Every placement above answers one question - WHO DOES THE HARDWARE LET
    DECIDE THIS?

        decided per pin ................ the CHANNEL   v_max, role, dc
        decided once per task/clock .... the MODULE    rate, duration, freq
        decided for the whole board,
          or needs two subsystems ...... the ROOT      caps, clock_check
        no hardware involved at all .... NOT IN THE TREE

    ================================================= FILES ARE NOT CLASSES

        usb6289.py      USB6289(Instrument)                    in the tree
        _ai.py          AnalogInput, AIChannel, 3 Parameters   in the tree
        _pps.py         PPSCounter                             in the tree
        _ao.py          AnalogOutput, AOChannel                in the tree
        _longrun.py     SegmentWriter, LongRun                 NOT in the tree
        postprocess.py  free functions                         NOT in the tree
        pinout_6289.py  the screw-terminal table               NOT in the tree

    The last three import numpy and nothing else - no qcodes, no nidaqmx. That
    is the point of the last row of the placement rule: SegmentWriter manages
    files, and a .f32 file is not a property of the hardware. It has no
    parameters, no snapshot and no parent, so it is a plain class. The payoff
    is that a week-long recording can be analysed on a laptop that has never
    had NI-DAQmx installed. long_run() itself stays a method here, because it
    drives hardware; it just hands all the bookkeeping to SegmentWriter.

    ======================================= WHY NOT THREE SEPARATE INSTRUMENTS

    ai, pps and ao could each have been an Instrument. They are not, because
    they share one board and one 20 MHz timebase - and this driver depends on
    that. The counter counts the AI subsystem's own sample clock, and the
    whole point of clock_check is that the AO and AI clocks are wrong
    TOGETHER, so the board cannot detect its own error. Three registry entries
    with no parent would express none of that, the snapshot would not nest,
    and close() on one would not touch the others.
    """

    def __init__(self, name, device="Dev1", **kwargs):
        super().__init__(name, **kwargs)
        self.device = device

        # Before any submodule: their validators are built from this.
        self.caps = self._read_capabilities()

        # Which counter is in use, and by whom. The board has two; nothing
        # else arbitrates them, so a second counter user would silently
        # collide with the 1 pps. See claim_counter().
        self._counter_owners = {}

        # --- long_run state, read from other threads while a run is going
        self._stop = threading.Event()
        self._run_state = {}
        self._live = deque()
        self._live_decimate = 100
        self._live_rate = None

        self.add_submodule("ai", AnalogInput(self))
        self.add_submodule("pps", PPSCounter(self))
        self.add_submodule("ao", AnalogOutput(self))

        self.connect_message()

    # ==================================================== capabilities
    def _read_capabilities(self):
        """What this board actually has. Falls back to the data sheet when the
        device is unreachable, so the module still imports."""
        try:
            dev = nidaqmx.system.Device(self.device)
            ai = tuple(c.name.split("/")[-1] for c in dev.ai_physical_chans)
            ao = tuple(c.name.split("/")[-1] for c in dev.ao_physical_chans)
            # ci_ not co_: counter INPUTS. co_physical_chans also lists
            # freqout, which cannot count edges.
            ctr = tuple(c.name.split("/")[-1] for c in dev.ci_physical_chans)
            terminals = tuple(dev.terminals)
            ai_max_rate = float(dev.ai_max_single_chan_rate)
            ai_max_multi_rate = float(dev.ai_max_multi_chan_rate)
            ao_max_rate = float(dev.ao_max_rate)
            ranges = tuple(sorted({abs(v) for v in dev.ai_voltage_rngs if v}))
        except Exception:
            ai, ao, ctr = AI_CHANNELS, AO_CHANNELS, COUNTERS
            terminals = tuple(f"/{self.device}/{p}" for p in PFI_LINES)
            ai_max_rate = AI_MAX_SINGLE_FALLBACK
            ai_max_multi_rate = AI_MAX_MULTI_FALLBACK
            ao_max_rate = 1_000_000.0
            ranges = AI_RANGES
        return {"ai": ai, "ao": ao, "ctrs": ctr, "terminals": terminals,
                "ai_max_rate": ai_max_rate,
                "ai_max_multi_rate": ai_max_multi_rate,
                "ao_max_rate": ao_max_rate, "ranges": ranges}

    # ================================================ counter arbitration
    def claim_counter(self, counter, owner):
        """Reserve one of the board's counters.

        There are two, and DAQmx would fail with a resource error deep inside
        a run if two subsystems took the same one. This turns that into a
        readable error before any hardware is touched. It is deliberately the
        root's job: the counters belong to the board, not to whoever happens
        to be using one today.
        """
        held_by = self._counter_owners.get(counter)
        if held_by is not None and held_by != owner:
            free = [c for c in self.caps["ctrs"]
                    if c not in self._counter_owners]
            raise RuntimeError(
                f"counter {counter} is already in use by {held_by}. This "
                f"board has {list(self.caps['ctrs'])}; "
                f"{'free: ' + str(free) if free else 'none are free'}.")
        self._counter_owners[counter] = owner
        return counter

    def release_counter(self, counter):
        self._counter_owners.pop(counter, None)

    # ============================================= cross-subsystem measures
    def acquire_with_pps(self):
        """One record on every enabled AI channel, plus the atomic seconds
        that fell inside it.

        Returns ({channel: array}, edges). `edges` are scan indices, so an
        event at scan k and an atomic second at scan e are on the same ruler -
        no clock conversion, no assumption about the rate.

        Scan 0 is whatever ai.trigger points at. Point it at pps.terminal and
        scan 0 IS an atomic second (the first edge comes back as 0); point it
        at the QM marker and the first edge tells you how far the next atomic
        second was from the start of the shot.

        Lives on the instrument, not on either submodule, because it drives
        both: the counter has to be started before the AI clock runs, and
        stopped and joined before the queue is drained.
        """
        self.pps.start()               # before the AI task: it counts ai/SampleClock
        try:
            data = self.ai.acquire()
        finally:
            # Collect inside the finally, not after it: stop() joins the
            # reader, so the queue is complete either way, and on a failed
            # acquisition the edges that WERE caught still say when the
            # failure happened. Draining also leaves nothing behind.
            self.pps.stop()
            self.pps.set_last_edges(self.pps.collect())
        return data, self.pps.last_edges

    def clock_check(self, seconds=None, verbose=True):
        """Measure the board's clock against the rubidium 1 pps.

        The counter counts ai/SampleClock - scans - and the 1 pps is its
        sample clock, so each value it returns is "how many scans had been
        taken when this atomic second arrived". The differences between those
        values ARE the true sample rate, in scans per atomic second.

        Returns a dict; also prints a table unless verbose=False.

        If a channel has role 'wave', the waveform is generated during the
        measurement and looked at in the data, giving a second reading of the
        same numbers. It can only ever CONFIRM, never detect: the DAC and the
        ADC divide the same 20 MHz timebase, so the square always measures
        exactly ai.rate/ao.freq samples per period however wrong the clock is -
        they are wrong together. The board cannot see its own error. Only the
        rubidium can.

        Wiring:
            1 pps -> pps.terminal, e.g. /Dev1/PFI9 (terminal 83, D GND 82)
            AO 0  -> AI 0          (terminal 15 -> 1, grounds 16 -> 3)

        Point ai.trigger AND ao.trigger at pps.terminal as well and both start
        on the same atomic second, so the square begins in a known phase.
        """
        previous = self.ai.duration()
        if seconds is not None:
            self.ai.duration(seconds)
        # Capture it NOW: the finally below puts the old duration back, so
        # reading it afterwards would describe the wrong acquisition. (v1 read
        # it afterwards, so clock_check(seconds=5) called with a 0.1 s
        # duration reported "expected ~0 edges" and never warned about a
        # counter that was catching none.)
        measured_for = self.ai.duration()
        try:
            if self.ao.wave_channel():
                with self.ao.generating():      # runs during the read
                    data, counts = self.acquire_with_pps()
            else:
                data, counts = self.acquire_with_pps()
        finally:
            self.ai.duration(previous)

        nominal = self.ai.actual_rate()
        expected = int(measured_for)
        raw = self.pps.last_edges_raw
        result = {"counts": counts, "n_edges": len(counts),
                  "n_edges_expected": expected, "nominal_rate": nominal,
                  "true_rate": self.pps.measured_rate(),
                  "ppm": self.pps.ppm(),
                  "n_dropped": (0 if raw is None else len(raw) - len(counts))}

        if len(counts) < expected - 1:
            self.log.warning(
                "caught %d of ~%d expected 1 pps edges - the reader may not "
                "be keeping up, or the pulse is not reaching %s",
                len(counts), expected, self.pps.terminal())

        # Square-wave cross-check: rising crossings of the mid level, on
        # whichever AI channel the loopback is wired to (the first enabled).
        result["square_channel"] = None
        result["samples_per_period"] = None
        result["true_freq"] = None
        if self.ao.wave_channel() and data:
            name = next(iter(data))
            trace = data[name]
            if trace.max() - trace.min() > 0.5:
                mid = (trace.max() + trace.min()) / 2
                hi = trace >= mid
                crossings = np.flatnonzero(~hi[:-1] & hi[1:]) + 1
                if len(crossings) >= 2:
                    result["square_channel"] = name
                    result["n_periods"] = len(crossings)
                    result["samples_per_period"] = float(
                        np.diff(crossings).mean())
                    if result["true_rate"] and np.isfinite(result["true_rate"]):
                        result["true_freq"] = (self.ao.freq()
                                               * result["true_rate"] / nominal)

        if verbose:
            self._print_clock_check(result)
        return result

    @staticmethod
    def _print_clock_check(r):
        counts = r["counts"]
        print(f"1 pps edges caught: {r['n_edges']} "
              f"(expected ~{r.get('n_edges_expected', '?')})")
        if r.get("n_dropped"):
            print(f"  {r['n_dropped']} latched value(s) discarded - not whole "
                  f"atomic seconds (normally the end-of-acquisition one)")
        if r["n_edges"] < 2:
            print("  too few - is the 1 pps on pps.terminal, and did the "
                  "acquisition run long enough to span a second?")
            return
        print("\n  edge   scan index   scans since previous")
        for i, c in enumerate(counts[:12]):
            step = "" if i == 0 else f"{counts[i] - counts[i - 1]:,}"
            print(f"  {i:4d}   {int(c):10,}   {step:>10}")
        if len(counts) > 12:
            print(f"   ... {len(counts) - 12} more")

        print(f"\n  nominal rate           {r['nominal_rate']:12,.3f} S/s")
        print(f"  true rate vs rubidium  {r['true_rate']:12,.3f} "
              f"scans/atomic second")
        print(f"  board clock error      {r['ppm']:+12.1f} ppm "
              f"({'fast' if r['ppm'] < 0 else 'slow'})")

        if r.get("samples_per_period"):
            print(f"\n  square on {r['square_channel']}: {r['n_periods']} "
                  f"periods, {r['samples_per_period']:.3f} samples each")
            if r["true_freq"]:
                print(f"  its real frequency is {r['true_freq']:.4f} Hz")

        print(f"\n  over a day that error is "
              f"{abs(r['ppm']) * 86400 / 1e6:.2f} s")

    # ================================================================= check
    def check(self, verbose=True, strict=False):
        """Validate the whole configuration WITHOUT acquiring or generating.

        Put it at the top of a script. The alternative is finding out hours
        in, when start() or long_run() finally builds the task and DAQmx
        objects to something you set before lunch.

        It exists because the interesting constraints span parameters -
        amp + |offset| against the output range, rate x channels against the
        aggregate limit, a trigger terminal that must actually be routable -
        and a `vals=` validator sees one parameter at a time. Checking those
        at set time would also make legal transitions impossible: going from
        (amp 1, offset 0) to (amp 8, offset 4) passes through an invalid state
        whichever order you assign them in.

        Returns a list of (level, where, message), empty if all is well.
        level is 'error' (this configuration cannot run) or 'warning' (it can
        run, but probably not the way you intended). strict=True raises on any
        error instead of returning.

            daq.check()                 # prints a report
            if daq.check(verbose=False):
                ...                     # something to say
            daq.check(strict=True)      # refuse to continue
        """
        problems = []
        for name in ("ai", "pps", "ao"):
            module = getattr(self, name, None)
            if module is not None and hasattr(module, "check"):
                problems.extend(module.check())

        # --- cross-subsystem: only the root can see these
        if self.ai.trigger() and self.ao.wave_channel() \
                and not self.ao.trigger():
            problems.append((
                "warning", "daq",
                f"ai.trigger is {self.ai.trigger()} but ao.trigger is None, "
                f"so the waveform free-runs and its phase at scan 0 is "
                f"arbitrary. Point both at the same terminal to fix the phase."))

        if self.pps.terminal() and self.ai.trigger() == self.pps.terminal():
            problems.append((
                "warning", "daq",
                "ai.trigger and pps.terminal are the same line, so scan 0 IS "
                "an atomic second and the first edge comes back as 0. "
                "Deliberate and useful - flagged only so it is not a "
                "surprise."))

        if verbose:
            self._print_check(problems)
        if strict:
            errors = [p for p in problems if p[0] == "error"]
            if errors:
                raise RuntimeError(
                    "configuration cannot run:\n" +
                    "\n".join(f"  [{where}] {message}"
                              for _, where, message in errors))
        return problems

    def _print_check(self, problems):
        if not problems:
            print(f"{self.name}: configuration OK")
            return
        errors = sum(1 for level, _, _ in problems if level == "error")
        warnings = len(problems) - errors
        print(f"{self.name}: {errors} error(s), {warnings} warning(s)")
        for level, where, message in problems:
            tag = "ERROR  " if level == "error" else "warning"
            first, *rest = textwrap.wrap(message, 68)
            print(f"  {tag} [{where:3}] {first}")
            for line in rest:
                print(f"{'':16}{line}")      # aligns under the message

    # ================================================================ wiring
    def wiring(self, verbose=True):
        """Every screw terminal to connect, for the CURRENT settings.

        Returns the table as a list of row dicts - role, channel, connection,
        terminal (the screw-terminal NUMBER), signal (its name on the pinout
        sheet) and note - and prints it unless verbose=False. The rows are
        the deliverable: they go straight into a DataFrame, a lab notebook, or
        a JSON file taped to the breakout box.

            rows = daq.wiring()
            pandas.DataFrame(rows)                     # a real table
            json.dump(rows, open("wiring.json", "w"))  # what was plugged in

        It reads the live configuration, so it changes as you enable channels
        or move the waveform - it describes what the driver is about to ask
        the hardware for, not what a data sheet says in general. Every
        connection has a return: on a multiplexed board a floating input does
        not read zero, it reads whatever the mux left behind.
        """
        if pin_of is None:
            if verbose:
                print("pinout_6289.py not importable - cannot resolve terminals")
            return []

        rows, notes = [], []

        def add(role, channel, connection, signal, note="", pin=None):
            if pin is None:
                try:
                    pin = pin_of(signal)
                except KeyError:
                    pin = None
                if isinstance(pin, list):       # grounds live on many pins
                    pin = pin[0]
            rows.append({"role": role, "channel": channel,
                         "connection": connection, "terminal": pin,
                         "signal": signal, "note": note})

        # ------------------------------------------------------ analog input
        try:
            self.ai._check_channel_set(self.ai.active)
        except ValueError as e:
            notes.append(f"INVALID: {e}")

        for channel in self.ai.active:
            n = channel.index
            cfg = channel.terminal_config()
            name = channel.short_name
            signal_pin = pin_of(f"AI {n}")
            add("ai", name, "signal (+)", f"AI {n}",
                f"{channel.v_min():+g}..{channel.v_max():+g} V {cfg}",
                pin=signal_pin)

            if cfg == "DIFF":
                add("ai", name, "signal (-)", f"AI {n + 8}",
                    f"negative half of {name}; ai{n + 8} cannot be used alone")
            elif cfg == "NRSE":
                sense = "AI SENSE" if n < 16 else "AI SENSE 2"
                add("ai", name, "reference", sense)
            else:
                add("ai", name, "return", "AI GND",
                    pin=nearest_ground(signal_pin, "AI GND"))

        # ------------------------------------------------------------- 1 pps
        if self.pps.terminal():
            line = self.pps.terminal().split("/")[-1]
            try:
                signal_pin = pin_of(line)
                add("1 pps", line, "signal", line,
                    f"latches scans on counter {self.pps.counter()}",
                    pin=signal_pin)
                add("1 pps", line, "return", "D GND",
                    pin=nearest_ground(signal_pin, "D GND"))
            except KeyError:
                add("1 pps", line, "signal", line, "internal route, no pin")
        else:
            notes.append("1 pps    not set - no atomic time axis. "
                         "daq.pps.terminal('/Dev1/PFI9') (terminal 83)")

        # ----------------------------------------------------- analog output
        for channel in self.ao.active:
            n = channel.index
            name = channel.short_name
            if channel.role() == "wave":
                state = "running" if self.ao.running() else "stopped"
                note = (f"{self.ao.shape()} {self.ao.freq():g} Hz, "
                        f"{self.ao.amp():g} V  [{state}]")
                role = "ao wave"
            else:
                note = f"held at {channel.dc():+g} V"
                role = "ao dc"
            signal_pin = pin_of(f"AO {n}")
            add(role, name, "signal", f"AO {n}", note, pin=signal_pin)
            add(role, name, "return", "AO GND",
                pin=nearest_ground(signal_pin, "AO GND"))
        if not self.ao.active:
            notes.append("outputs  none configured")

        # ---------------------------------------------------------- triggers
        for role, terminal in (("ai trigger", self.ai.trigger()),
                               ("ao trigger", self.ao.trigger())):
            if not terminal:
                continue
            line = terminal.split("/")[-1]
            try:
                signal_pin = pin_of(line)
                add(role, line, "signal", line, terminal, pin=signal_pin)
                add(role, line, "return", "D GND",
                    pin=nearest_ground(signal_pin, "D GND"))
            except KeyError:
                add(role, line, "signal", line, "internal route, no pin")
        if not self.ai.trigger():
            notes.append("ai trig  none - acquisition starts on task.start()")

        if verbose:
            self._print_wiring(rows, notes)
        return rows

    def _print_wiring(self, rows, notes):
        columns = ["role", "channel", "connection", "terminal", "signal",
                   "note"]
        header = {c: c for c in columns}
        width = {c: max(len(header[c]),
                        max((len(str(r[c])) for r in rows), default=0))
                 for c in columns}

        print(f"{self.name}: {self.device}")
        print("  " + "  ".join(f"{header[c]:<{width[c]}}" for c in columns)
              .rstrip())
        print("  " + "  ".join("-" * width[c] for c in columns).rstrip())
        for row in rows:
            cells = []
            for c in columns:
                value = "" if row[c] is None else str(row[c])
                cells.append(f"{value:>{width[c]}}" if c == "terminal"
                             else f"{value:<{width[c]}}")
            print("  " + "  ".join(cells).rstrip())
        for note in notes:
            print(f"  {note}")
            
    def board_pinnout(self, verbose=True):
        """Print the board's pinout table, from pinout_6289.py.

        The table is a dict of {signal: [pin, ...]}, so a signal that lives on
        multiple pins (grounds) is listed once with all its pins. The table is
        not part of the driver, because it is not needed to run the board.
        """
        if pin_of is None:
            print("pinout_6289.py not importable - cannot show pinout")
            return
        if verbose:
            print(f"{self.name}: {self.device} pinout")
            for signal, pins in sorted(pin_of._table.items()):
                print(f"  {signal:<12} -> {', '.join(str(p) for p in pins)}")
        return pin_of._table
        

    # ============================================================ long runs
    def long_run(self, outdir, hours=None, rotate_minutes=None, restart=True,
                 background=False, live_decimate=100, live_seconds=60,
                 to_hdf5=False, require_pps=True, verbose=True):
        """Record every enabled AI channel continuously, for hours or weeks.

        See _longrun.py for the on-disk layout and why it is that way. Stop
        any time with the interrupt button - the current segment is closed and
        recorded. On a DAQmx error the run restarts and the gap goes in the
        manifest rather than being silently spliced.

        background=True runs it in a thread and returns a LongRun handle, so
        this process can also drive the OPX. The DAQ and QM each drain their
        own buffer; neither waits on the other.
        """
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)

        use_pps = bool(self.pps.terminal())
        if not use_pps and require_pps:
            raise ValueError(
                "pps.terminal is not set. Over a day the board's clock drifts "
                "~4 s; the 1 pps edges are what make the time axis meaningful. "
                "Set daq.pps.terminal('/Dev1/PFI9') (screw terminal 83), or "
                "pass require_pps=False if you are testing on the bench and "
                "do not care about the time axis.")
        if not self.ai.active:
            raise ValueError(
                "no analog input is enabled - there is nothing to record. "
                "Try daq.ai.enable('ai0').")

        if background:
            self._stop.clear()
            thread = threading.Thread(target=self.long_run, daemon=True,
                            kwargs=dict(outdir=outdir, hours=hours,
                            rotate_minutes=rotate_minutes, restart=restart,
                            background=False, live_decimate=live_decimate,
                            live_seconds=live_seconds, to_hdf5=to_hdf5,
                            require_pps=require_pps, verbose=verbose))
            thread.start()
            return LongRun(self, thread, outdir)

        channels = [c.short_name for c in self.ai.active]
        rate, conv_rate = self.ai.actual_rate(), self.ai.conv_rate()
        # None = never rotate: ONE file per channel for the whole run.
        # Rotation exists so a closed segment can be archived or compressed
        # while the run continues, and so a filesystem problem costs one
        # segment instead of everything. Neither matters for a short run.
        rotate_scans = (None if rotate_minutes is None
                        else int(round(rotate_minutes * 60 * rate)))
        stop_scans = None if hours is None else int(round(hours * 3600 * rate))

        (outdir / "run.json").write_text(json.dumps(self.describe(), indent=2, default=str))

        if verbose:
            gb_day = rate * 4 * len(channels) * 86400 / 1e9
            print(f"long run -> {outdir}")
            rotation = ("one file per channel" if rotate_minutes is None
                        else f"rotating every {rotate_minutes} min")
            print(f"  {rate:,.3f} S/s x {len(channels)} channel(s) "
                  f"{channels}, float32, {rotation}")
            print(f"  {gb_day:.1f} GB/day, {gb_day * 7:.0f} GB/week")
            print("  interrupt to stop cleanly")

        # Rolling decimated view for live plotting; one entry per chunk.
        chunk_seconds = max(0.001, (rate // 5) / rate)
        self._live = deque(maxlen=max(1, int(live_seconds / chunk_seconds)))
        self._live_decimate = live_decimate
        self._live_rate = rate

        # Convert to HDF5 in a background thread, so the acquisition is not slowed
        convert_q = queue.Queue() if to_hdf5 else None
        if convert_q is not None:
            threading.Thread(target=_hdf5_worker, args=(convert_q, verbose),daemon=True).start()

        writer = SegmentWriter(outdir, channels, rate, rotate_scans,convert_q=convert_q, verbose=verbose)
        writer.log({"event": "start", "channels": channels, "nominal_rate": rate, "conv_rate": conv_rate, "wall": time.time()})

        n = 0
        if use_pps:
            self.pps.start()      # before the AI task: it counts ai/SampleClock
        elif verbose:
            print("  NO 1 pps - the time axis will be the board's own clock")
        try:
            while stop_scans is None or n < stop_scans:
                try:
                    # A restart after a gap re-enters acquire_chunks, which
                    # builds a NEW task, which arms, which calls _on_armed
                    # again. See AnalogInput.set_on_armed: whether that is
                    # what you want depends on what the callback does, and the
                    # driver cannot know.
                    for _, chunk in self.ai.acquire_chunks(duration=None):
                        n += writer.write(chunk, n)
                        if use_pps:
                            writer.write_edges(self.pps.collect())

                        if live_decimate:
                            self._append_live(chunk, channels[0], n, live_decimate)

                        self._run_state = {"scans": n, "seconds": n / rate, "segment": writer.index, "channels": channels,"wall": time.time()}

                        if self._stop.is_set():
                            if verbose:
                                print(f"\n  stopped at {n / rate:,.1f} s")
                            break
                        if stop_scans is not None and n >= stop_scans:
                            break
                except KeyboardInterrupt:
                    if verbose:
                        print(f"\n  stopped by user at {n / rate:,.1f} s")
                except (nidaqmx.errors.DaqError, RuntimeError, OSError) as e:
                    writer.log({"event": "gap", "sample": n, "wall": time.time(),"error": str(e).splitlines()[0]})
                    if not restart:
                        raise
                    if verbose:
                        print(f"\n  ERROR at {n / rate:,.1f} s: "
                              f"{str(e).splitlines()[0]}")
                        print("  restarting in 5 s - THERE IS A GAP HERE")
                    if self._stop.wait(5.0):
                        break
                    writer.rotate(n)   # a gap is always a file boundary
                    continue           # back into the acquisition loop
                break                  # the chunk loop ended on its own terms
        finally:
            if use_pps:
                self.pps.stop()
                # Whatever was still queued when the loop exited. stop() joins
                # the reader, so this is complete. Without it the last few
                # atomic seconds of every run were dropped on the floor - the
                # edges were latched, they just never reached the file.
                writer.write_edges(self.pps.collect())
            writer.close(n)

        if verbose:
            n_edges = (outdir / "edges.i64").stat().st_size // 8
            print(f"\ndone: {n:,} scans ({n / rate / 3600:.2f} h) on "
                  f"{len(channels)} channel(s), {n_edges:,} pps edges")
        return outdir

    def _append_live(self, chunk, channel, n_after, decimate):
        """Keep a decimated slice of one channel for live_preview()."""
        n_before = n_after - len(chunk[channel])
        offset = (-n_before) % decimate       # keep a global decimation grid
        self._live.append((n_before + offset, chunk[channel][offset::decimate].copy()))

    def live_preview(self, channel=None):
        """(seconds, volts) for the recent past of a running long_run().

        WHERE THE LIVE DATA IS. There are two answers, and picking the wrong
        one is the usual confusion:

          THIS - a deque in RAM (self._live), holding the last `live_seconds`
          (default 60) of the FIRST enabled channel, decimated by
          `live_decimate` (default 100, so 250 points/s instead of 25,000).
          It is a health check: is there a signal, is it the right size, is it
          still going. Nothing more. It is bounded on purpose - an unbounded
          live buffer is just a slow memory leak with a week to work in.

          THE FILE - everything. Every channel, every sample, flushed about
          five times a second, so the bytes are on disk within 200 ms of being
          measured. To LOOK at a run - scroll back an hour, zoom, hunt for
          events - read the file, do not read this. That is what livescope.py
          does, and it can do it from another process or another machine
          because it never touches the DAQ:

              from livescope import LiveScope
              LiveScope(outdir, channel="ai0", width=5.0).show()

        So: this for "is it alive", the file for "what did it measure".

            run = daq.long_run(outdir, background=True)
            while run.alive:
                t, y = daq.live_preview()
                ax.clear(); ax.plot(t, y); display(fig)
                time.sleep(1)

        Safe to call from another thread while the acquisition runs - it only
        copies the deque, and the writer only appends to it.
        """
        items = list(self._live)
        if not items:
            return np.array([]), np.array([])
        decimate, rate = self._live_decimate, self._live_rate
        index = np.concatenate([i0 + np.arange(len(a)) * decimate
                                for i0, a in items])
        values = np.concatenate([a for _, a in items])
        return index / rate, values

    # ========================================================== metadata
    def describe(self):
        """Everything needed to reconstruct and interpret a recording.

        NOT called metadata(): QCoDeS gives every Metadatable a `metadata`
        dict attribute in __init__, which shadows a method of the same name.
        (v1 defines USB6289.metadata() and long_run() calls it - worth
        checking, it should raise TypeError: 'dict' object is not callable.)
        """
        try:
            dev = nidaqmx.system.Device(self.device)
            hw = {"product_type": dev.product_type,
                  "serial": hex(dev.serial_num),
                  "simulated": bool(dev.is_simulated),
                  "driver_version": str(
                      nidaqmx.system.System.local().driver_version)}
        except Exception:
            hw = {"product_type": None, "serial": None,
                  "simulated": None, "driver_version": None}

        out = {"device": self.device, "hardware": hw}
        for key in ("ai", "pps", "ao"):
            module = getattr(self, key, None)
            if module is not None and hasattr(module, "describe"):
                out[key] = module.describe()
        return out

    # ============================================================== infra
    def close(self):
        """Submodules have no close() hook - tear them down from here, or a
        DAQmx task is left holding a pin at its last voltage and the pps
        reader thread outlives the instrument."""
        for teardown in ("ao.stop", "pps.stop"):
            module, method = teardown.split(".")
            target = getattr(self, module, None)
            if target is not None:
                try:
                    getattr(target, method)()
                except Exception:
                    pass
        super().close()

    def get_idn(self):
        try:
            dev = nidaqmx.system.Device(self.device)
            return {"vendor": "National Instruments",
                    "model": dev.product_type,
                    "serial": hex(dev.serial_num),
                    "firmware": str(
                        nidaqmx.system.System.local().driver_version)}
        except Exception:
            return {"vendor": "National Instruments", "model": "USB-6289",
                    "serial": None, "firmware": None}
