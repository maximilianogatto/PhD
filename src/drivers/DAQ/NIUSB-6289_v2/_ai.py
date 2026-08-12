"""
Analog input subsystem of the NI USB-6289, as a QCoDeS instrument module.

Two levels:

    AnalogInput   the subsystem - one sample clock, one task, one trigger.
                  Owns rate, duration, trigger and the time axes, because
                  every channel in a task shares them. There is no per-channel
                  rate: the board has ONE ADC.

    AIChannel     one physical input, 32 copies in a ChannelList. Owns what
                  the hardware really lets you set per channel: enabled,
                  v_min/v_max (each channel has its own programmable gain) and
                  terminal_config.

        daq.ai.ai0.enabled(True)
        daq.ai.ai3.enabled(True)
        daq.ai.ai3.v_max(10.0)          # different range from ai0 - allowed
        daq.ai.rate(25_000)
        data = daq.ai.acquire()         # {'ai0': array, 'ai3': array}

MULTIPLEXED, NOT SIMULTANEOUS. One ADC behind a multiplexer, so the enabled
channels are not sampled at the same instant: within every scan the board
steps through them at the convert clock, and channel i in the task is sampled
i/conv_rate after channel 0. Read it off the channel:

        t = daq.ai.time_axis() + daq.ai.ai3.time_offset()

conv_rate is read back from the hardware, never assumed. At the default it is
of order a microsecond per step - irrelevant against the +/-40 us quantisation
of a 1 pps edge, and not irrelevant at all if you are comparing a fast
transient across two channels.

Two consequences of the mux that no parameter can fix:

  * Ghosting. The mux has to settle between channels. A high source impedance,
    or a large range difference between neighbours in the task, leaves part of
    the previous channel in the reading. Order the channels so the big steps
    are rare, or drop ai_rate.
  * Aggregate rate. The device's multi-channel limit is a TOTAL across the
    task, so the per-channel maximum is that limit divided by the number of
    enabled channels. Checked in _configure, where the channel set is known -
    a `vals=` validator is fixed at construction and cannot see it.

THE 1 PPS IS UNAFFECTED. The counter counts ai/SampleClock, which ticks once
per SCAN, not once per conversion, so an edge index is a scan index whatever
the channel count and times_from_edges keeps its meaning. (ai/ConvertClock is
the per-channel one. Never point the counter at that.)

ONE ACQUISITION FEEDS EVERY CHANNEL. Two facts that do not fit together:

    the hardware   reads ALL enabled channels at once. One task, one clock,
                   one block of data. There is no "read ai0 now, ai3 later".
    QCoDeS         asks for one parameter at a time. It calls ai0.trace(),
                   then ai3.trace(), and never says which loop iteration it
                   is on.

acquire() runs the hardware once and splits the block into one array per
channel, kept in self._last:

    data = ...                      # 2-D, (n_channels, n_samples)
    self._last = {"ai0": row 0, "ai3": row 1}

trace() then only PICKS a row out of that. It never measures. So there is
exactly one line in your code where the hardware runs, and it is one you
wrote:

    with meas.run() as datasaver:
        for x in sweep:
            daq.ai.acquire()                     # <- the hardware runs HERE
            datasaver.add_result(
                (daq.ai.time_axis, daq.ai.time_axis()),
                *[(ch.trace, ch.trace()) for ch in daq.ai.active])

Every channel in one iteration therefore comes from the same scan, by
construction rather than by inference, and reading a channel twice, in a
different order, or not at all changes nothing.

An earlier version inferred when to re-measure, by tracking which channels
had already been handed the current scan. It worked, but reading one channel
twice inside an iteration silently desynchronised the others, and any
mechanism that measures as a SIDE EFFECT of a get is a mechanism that can
measure when you did not mean it to - a station snapshot with update=True
would have done exactly that. Explicit is worth the extra line.

Consequently trace() RAISES rather than measuring if there is no scan in
hand, or if the channel was not in it. Silently returning a stale array is
the one failure that would not be noticed until the run was over.
"""

import sys
from pathlib import Path

import numpy as np
import nidaqmx
from nidaqmx.constants import (
    AcquisitionType, Edge, TaskMode, TerminalConfiguration,
)
from nidaqmx.stream_readers import AnalogMultiChannelReader

from qcodes.instrument import ChannelList, InstrumentChannel, InstrumentModule
from qcodes.parameters import Parameter, ParameterWithSetpoints
from qcodes.validators import Arrays, Bool, Enum, Numbers

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    from pinout_6289 import DIFF_POSITIVE
except ImportError:
    DIFF_POSITIVE = list(range(0, 8)) + list(range(16, 24))

OVERFLOW = -200279          # PC not reading the driver buffer fast enough
FIFO_OVERFLOW = -200361     # USB not draining the onboard FIFO fast enough

_TERM_CFG = {"RSE": TerminalConfiguration.RSE,
             "NRSE": TerminalConfiguration.NRSE,
             "DIFF": TerminalConfiguration.DIFF}


# ============================================================== parameters
class AITimeAxis(Parameter):
    """Sample index / sample rate, common to every channel in the scan.

    Add the channel's time_offset() for the mux delay of a particular input.
    """

    def get_raw(self):
        ai = self.instrument                 # the AnalogInput MODULE
        return np.arange(ai.n_samples()) / ai.actual_rate()


class AIAtomTimeAxis(Parameter):
    """Time axis in ATOMIC seconds, from the 1 pps edges of the last record.

    t = 0 is the FIRST 1 pps edge and every later edge is exactly +1 s - the
    definition, not an approximation, because each edge is one atomic second
    from the rubidium. Samples between edges are linearly interpolated.

    Linear on purpose: the relation is piecewise linear by construction and
    the board is stable to 0.05 ppm between edges, so a straight line is exact
    far below the +/-40 us quantisation of an edge position. A spline would fit
    that quantisation noise and overshoot between points.

    Requires an acquisition that ran the counter (daq.acquire_with_pps, or
    long_run) - a plain acquire() collects no edges.
    """

    def get_raw(self):
        from postprocess import times_from_edges

        ai = self.instrument
        pps = getattr(ai.root_instrument, "pps", None)
        if pps is None:
            raise RuntimeError(
                "no pps submodule on this instrument - the atomic time axis "
                "needs the 1 pps counter")
        edges = pps.last_edges
        if edges is None or len(edges) < 2:
            raise RuntimeError(
                "no 1 pps edges from the last acquisition - use "
                "daq.acquire_with_pps() and check pps.terminal is set")
        # Pass the rate so the edge table is filtered here too, not only
        # when pps.set_last_edges happened to do it.
        return times_from_edges(np.arange(ai.n_samples()), edges,
                                scans_per_second=ai.actual_rate())


class AITrace(ParameterWithSetpoints):
    """One channel's samples, from the scan currently in hand.

    Reading this NEVER measures. The hardware produces every enabled channel
    in one block, so this just picks its own row out of the block that
    daq.ai.acquire() put there. Call acquire() yourself, once per measurement
    point; this raises if you have not. See the module docstring.
    """

    def get_raw(self):
        channel = self.instrument            # the AIChannel
        return channel.parent.read_for(channel)


# ================================================================= channel
class AIChannel(InstrumentChannel):
    """One analog input pin.

    Everything here is genuinely per-channel in the hardware. Anything shared
    by the task - rate, duration, trigger - lives on the parent, because the
    board cannot give two inputs different values for it.
    """

    def __init__(self, parent, name, index):
        super().__init__(parent, name)
        self.index = index

        self.add_parameter(
            "enabled", label=f"{name} in the task",
            get_cmd=None, set_cmd=self._invalidate, initial_value=False,
            vals=Bool(),
            docstring="whether this input is part of the acquisition. Every "
                      "enabled channel divides the device's aggregate rate, "
                      "so enable only what you read.")

        limit = max(parent.ranges)
        self.add_parameter(
            "v_min", label=f"{name} range minimum", unit="V",
            get_cmd=None, set_cmd=self._invalidate, initial_value=-1.5,
            vals=Numbers(-limit, limit))

        self.add_parameter(
            "v_max", label=f"{name} range maximum", unit="V",
            get_cmd=None, set_cmd=self._invalidate, initial_value=1.5,
            vals=Numbers(-limit, limit),
            docstring=f"DAQmx snaps this up to the nearest hardware range: "
                      f"{parent.ranges} V. Per channel - the gain is set per "
                      f"input, not per task. Neighbouring channels with very "
                      f"different ranges make the mux settle worse; see the "
                      f"ghosting note in this module's docstring.")

        self.add_parameter(
            "terminal_config", label=f"{name} terminal configuration",
            get_cmd=None, set_cmd=self._invalidate, initial_value="RSE",
            vals=Enum("RSE", "NRSE", "DIFF"),
            docstring="DIFF is valid only on ai0-7 and ai16-23, and consumes "
                      "ai(n+8) as its negative input - that channel then "
                      "cannot be enabled in its own right.")

        self.add_parameter(
            "time_offset", label=f"{name} mux delay", unit="s", set_cmd=False,
            get_cmd=lambda: self.parent.offset_of(self),
            docstring="when this channel is sampled within a scan, relative "
                      "to the first channel in the task. One ADC behind a "
                      "mux: position_in_task / conv_rate.")

        self.add_parameter(
            "trace", parameter_class=AITrace,
            label=f"Voltage {name}", unit="V",
            setpoints=(parent.time_axis,),
            vals=Arrays(shape=(parent.n_samples,)),
            # A snapshot must never trigger an acquisition. QCoDeS snapshots
            # the station at the start of every Measurement, and a snapshot
            # with update=True gets every gettable parameter - which here
            # would measure AND consume the ledger before the run began.
            snapshot_value=False)

    @property
    def physical(self):
        """Fully qualified DAQmx name, e.g. 'Dev1/ai3'."""
        return f"{self.root_instrument.device}/{self.short_name}"

    def _invalidate(self, _value):
        """Anything feeding the task invalidates the verified rates."""
        self.parent.invalidate()

    def __repr__(self):
        state = "on " if self.enabled() else "off"
        return (f"<AIChannel {self.short_name} {state} "
                f"{self.v_min():+g}..{self.v_max():+g} V "
                f"{self.terminal_config()}>")


# =============================================================== subsystem
class AnalogInput(InstrumentModule):
    """The AI task: one clock, one trigger, N channels."""

    def __init__(self, parent, name="ai"):
        super().__init__(parent, name)

        caps = parent.caps
        self.ranges = caps["ranges"]
        self._max_single_rate = caps["ai_max_rate"]
        self._max_multi_rate = caps["ai_max_multi_rate"]

        # --- verified hardware values, cleared by any change to the task
        self._actual_rate = None
        self._conv_rate = None

        # --- the scan currently in hand, produced only by acquire()
        self._last = None            # {channel name: 1-D array}
        self._generation = 0         # bumped per acquisition; for debugging

        self._on_armed = None

        # ------------------------------------------------ shared settings
        self.add_parameter(
            "rate", label="AI sample rate", unit="S/s",
            get_cmd=None, set_cmd=self._set_invalidating, initial_value=25_000,
            vals=Numbers(1, self._max_single_rate),
            docstring=f"per channel. Up to {self._max_single_rate:,.0f} S/s "
                      f"with one channel enabled; with N the device's "
                      f"{self._max_multi_rate:,.0f} S/s aggregate limit means "
                      f"at most that divided by N. The N-dependent half is "
                      f"checked at configure time, not by this validator.")

        self.add_parameter(
            "duration", label="AI acquisition length", unit="s",
            get_cmd=None, set_cmd=None, initial_value=0.5,
            vals=Numbers(1e-4, 3600))

        self.add_parameter(
            "trigger", label="AI start trigger terminal",
            get_cmd=None, set_cmd=self._set_invalidating, initial_value=None,
            vals=Enum(*caps["terminals"], None),
            docstring=f"None, or a routable terminal - e.g. "
                      f"'/{parent.device}/PFI8' (terminal 81) for the OPX "
                      f"marker. Applies to the whole scan; there is one start "
                      f"trigger for the task, not one per channel.")

        self.add_parameter(
            "actual_rate", label="AI programmed rate", unit="S/s",
            get_cmd=lambda: self._verify()[0], set_cmd=False,
            docstring="rate the hardware programmed, read back from the "
                      "device. The sample clock divides the 20 MHz timebase, "
                      "so it is rarely exactly what you asked for.")

        self.add_parameter(
            "conv_rate", label="AI convert (mux) clock", unit="Hz",
            get_cmd=lambda: self._verify()[1], set_cmd=False,
            docstring="how fast the multiplexer steps between channels within "
                      "one scan, read back from the device. Sets the skew "
                      "between channels - see time_offset.")

        self.add_parameter(
            "n_samples", label="AI samples per record per channel",
            set_cmd=False,
            get_cmd=lambda: int(round(self.duration() * self._verify()[0])))

        self.add_parameter("time_axis", parameter_class=AITimeAxis,
                           label="Time", unit="s",
                           vals=Arrays(shape=(self.n_samples,)))

        self.add_parameter("atom_time_axis", parameter_class=AIAtomTimeAxis,
                           label="Time (atomic)", unit="s",
                           vals=Arrays(shape=(self.n_samples,)))

        # ------------------------------------------------------- channels
        # After time_axis and n_samples: every channel's `trace` refers to
        # them as setpoints and validator.
        channels = ChannelList(self, "channels", AIChannel)
        
        for i, ch_name in enumerate(caps["ai"]):
            channel = AIChannel(self, ch_name, i)
            channels.append(channel)
            self.add_submodule(ch_name, channel)      # daq.ai.ai0
        channels.lock()
        self.add_submodule("channels", channels)      # daq.ai.channels[0]

        if len(self.channels):
            self.channels[0].enabled(True)            # a usable default

    # =================================================== channel bookkeeping
    @property
    def active(self):
        """Enabled channels, in the order they are added to the task.

        DAQmx returns rows in exactly this order, so it is also the row order
        of every array this module produces. Sorted by hardware index, so it
        does not depend on the order you happened to enable things in.
        """
        return [c for c in self.channels if c.enabled()]

    def enable(self, *names, exclusive=True):
        """Convenience: enable these channels, by default disabling the rest.

            daq.ai.enable("ai0", "ai3")
        """
        wanted = set(names)
        unknown = wanted - {c.short_name for c in self.channels}
        if unknown:
            raise ValueError(f"no such input(s): {sorted(unknown)}")
        for channel in self.channels:
            if channel.short_name in wanted:
                channel.enabled(True)
            elif exclusive:
                channel.enabled(False)
        return self.active

    def setup(self, channels=None, rate=None, duration=None, trigger=None,
              terminal_config=None):
        """Configure the subsystem in one call. Returns the enabled channels.

        `channels` says WHICH inputs are on and, if you want, what range each
        one uses - one place, not one call per channel per setting:

            daq.ai.setup(channels={"ai0": 2.0,           # +/- 2 V
                                   "ai3": (-0.5, 1.5)},  # explicit min, max
                         rate=25_000, duration=0.1)

        A range is either a single number, meaning +/- that, or a (min, max)
        pair when it is not symmetric. Both snap up to the nearest hardware
        range - each input has its own programmable gain, so mixing them is
        free. (Neighbours in the task with very different ranges make the mux
        settle worse; see the ghosting note at the top of this module.)

        When you do not care about the range, name the channels and nothing
        else - they keep whatever they had:

            daq.ai.setup("ai0", rate=25_000, duration=0.1)
            daq.ai.setup(["ai0", "ai3"], rate=25_000)

        Only what you pass is changed; anything left as None keeps its value.
        Enabling is exclusive, as in enable(): channels you do not name are
        switched off.

        Returns the channel objects, so you can keep handles in one line:

            ai0, ai3 = daq.ai.setup({"ai0": 2.0, "ai3": 10.0}, rate=25_000)
        """
        if channels is not None:
            if isinstance(channels, str):
                ranges = {channels: None}
            elif isinstance(channels, dict):
                ranges = dict(channels)
            else:
                ranges = {name: None for name in channels}

            self.enable(*ranges)
            for name, limits in ranges.items():
                if limits is None:
                    continue
                channel = getattr(self, name)
                try:
                    v_min, v_max = limits          # a (min, max) pair
                except TypeError:
                    v_min, v_max = -abs(limits), abs(limits)   # symmetric
                # Widen first, so the intermediate state is never inverted:
                # setting v_min above the current v_max would fail validation
                # in _configure even though the final pair is fine.
                channel.v_max(max(v_max, channel.v_max()))
                channel.v_min(v_min)
                channel.v_max(v_max)

        if rate is not None:
            self.rate(rate)
        if duration is not None:
            self.duration(duration)
        if trigger is not None:
            self.trigger(trigger)
        if terminal_config is not None:
            for channel in self.active:
                channel.terminal_config(terminal_config)
        return self.active

    def invalidate(self):
        """Drop everything verified against the hardware.

        Called whenever a parameter that feeds _configure changes. The rates
        are re-verified lazily on the next read, so a burst of settings costs
        one verification, not one each.
        """
        self._actual_rate = None
        self._conv_rate = None
        # The held scan was produced under the OLD configuration, so it no
        # longer matches what describe() would say about it. Drop it: trace()
        # then raises instead of handing back an array that quietly belongs to
        # a different setup.
        self._last = None

    def _set_invalidating(self, _value):
        self.invalidate()

    def offset_of(self, channel):
        """Mux delay of `channel` within a scan, in seconds."""
        active = self.active
        if channel not in active:
            return float("nan")            # not in the task: no position
        conv = self._verify()[1]
        if not conv or not np.isfinite(conv):
            return float("nan")
        return active.index(channel) / conv

    def set_on_armed(self, callback):
        """Run `callback()` once the AI task is armed and waiting.

        This is where the QM job is launched: the DAQ must already be waiting
        before the OPX emits its marker, or the trigger is missed. The window
        between task.start() and the callback is the only moment at which the
        board is armed but nothing has been asked to fire yet.

        CALLED ONCE PER TASK, NOT ONCE PER RUN. That is the same thing for
        acquire(), which builds one task. It is NOT the same thing for
        long_run(): a DaqError there restarts the acquisition, which builds a
        NEW task, which arms, which calls this again. So over a week-long run
        the callback fires once per restart.

        Whether that is right depends on what the callback does, and only you
        know:

            relaunching is CORRECT if the callback starts something that must
            be running for the DAQ to see anything - with ai.trigger set, a
            restarted task waits for a marker that will never come again
            unless something re-emits it;

            relaunching is WRONG if the callback starts a job that is still
            running from the first time, and you would end up with two.

        If you need it once and only once, say so in the callback - it is one
        line, and it is clearer there than as a flag here:

            launched = False
            def arm():
                nonlocal launched
                if not launched:
                    qm.execute(prog)
                    launched = True
            daq.ai.set_on_armed(arm)
        """
        self._on_armed = callback

    # ======================================================== task building
    def _check_channel_set(self, active):
        """Everything that depends on more than one parameter at a time, so
        cannot live in a `vals=` validator."""
        if not active:
            raise ValueError(
                "no analog input is enabled - the task would have no "
                "channels. Try daq.ai.enable('ai0').")

        enabled_indices = {c.index for c in active}
        for channel in active:
            if channel.v_min() >= channel.v_max():
                raise ValueError(
                    f"{channel.short_name}: v_min ({channel.v_min()}) must be "
                    f"below v_max ({channel.v_max()})")

            if channel.terminal_config() != "DIFF":
                continue

            n = channel.index
            if n not in DIFF_POSITIVE:
                raise ValueError(
                    f"'{channel.short_name}' cannot be a DIFF channel - it is "
                    f"the negative terminal of ai{n - 8}. Enable ai{n - 8} in "
                    f"DIFF mode (which uses {channel.short_name} as its "
                    f"negative input), or set its terminal_config to 'RSE' or "
                    f"'NRSE'.")
            if n + 8 in enabled_indices:
                raise ValueError(
                    f"'{channel.short_name}' is DIFF, so the board wires ai"
                    f"{n + 8} as its negative input - ai{n + 8} cannot also be "
                    f"enabled as a channel of its own. Disable it, or take "
                    f"{channel.short_name} out of DIFF.")

    def _check_rate(self, active):
        """The aggregate limit, which only the channel set can decide."""
        n = len(active)
        rate = self.rate()
        if n == 1:
            if rate > self._max_single_rate:
                raise ValueError(
                    f"rate {rate:,.0f} S/s is over this device's "
                    f"{self._max_single_rate:,.0f} S/s single-channel limit")
            return
        aggregate = rate * n
        if aggregate > self._max_multi_rate:
            per_channel = self._max_multi_rate / n
            raise ValueError(
                f"rate {rate:,.0f} S/s x {n} channels = {aggregate:,.0f} S/s "
                f"aggregate, over this device's {self._max_multi_rate:,.0f} "
                f"S/s multi-channel limit (max {per_channel:,.0f} S/s per "
                f"channel with {n} enabled). One ADC serves them all - "
                f"enabling a channel costs rate, it does not add capacity.")

    def _configure(self, task, chunk) -> list[AIChannel]:
        """Build the AI task. The ONLY place this happens, so acquire() and
        the verified rates can never disagree about what the hardware does.

        Order: validate -> channels -> clock -> trigger.
        """
        active = self.active
        self._check_channel_set(active)
        self._check_rate(active)

        for channel in active:
            task.ai_channels.add_ai_voltage_chan(channel.physical, terminal_config=_TERM_CFG[channel.terminal_config()],min_val=channel.v_min(), max_val=channel.v_max())

        task.timing.cfg_samp_clk_timing(rate=self.rate(), sample_mode=AcquisitionType.CONTINUOUS, samps_per_chan=chunk * 10)

        if self.trigger():
            task.triggers.start_trigger.cfg_dig_edge_start_trig(self.trigger(), trigger_edge=Edge.RISING)
        return active

    def _verify(self) -> tuple[float, float]:
        """(sample rate, convert rate) the hardware would really use.

        Verified, not acquired: the task is built and handed to DAQmx for
        checking, which programs the clocks without ever starting them. Both
        numbers come from the same verification, so one task build answers
        both questions.
        """
        if self._actual_rate is None or self._conv_rate is None:
            with nidaqmx.Task() as task:
                self._configure(task, chunk=1000)
                task.control(TaskMode.TASK_VERIFY)
                self._actual_rate = float(task.timing.samp_clk_rate)
                try:
                    self._conv_rate = float(task.timing.ai_conv_rate)
                except nidaqmx.errors.DaqError:
                    # Some configurations do not expose it; the skew is then
                    # simply unknown rather than zero, hence nan.
                    self._conv_rate = float("nan")
        return self._actual_rate, self._conv_rate

    def _overflow_message(self, error, rate, n_chan, n_done):
        """Name which of the two buffers overflowed - they need different
        fixes, and with several channels the aggregate is what matters."""
        aggregate = rate * n_chan
        if error.error_code == OVERFLOW:
            return (f"AI driver-buffer overflow after {n_done:,} samples per "
                    f"channel: the PC is not reading fast enough at "
                    f"{rate:,.0f} S/s on {n_chan} channel(s) "
                    f"({aggregate:,.0f} S/s aggregate). Read bigger chunks, do "
                    f"less work per chunk, or lower ai.rate.")
        if error.error_code == FIFO_OVERFLOW:
            return (f"AI onboard-FIFO overflow after {n_done:,} samples per "
                    f"channel: USB could not drain the board at {rate:,.0f} "
                    f"S/s on {n_chan} channel(s) ({aggregate:,.0f} S/s "
                    f"aggregate). Lower ai.rate, disable a channel, or stop "
                    f"competing for the bus - an AO waveform regenerated over "
                    f"USB will do this.")
        raise error

    #========================================================== acquisition
    # def acquire(self):
    #     """Acquire one record on every enabled channel.

    #     Returns {channel name: 1-D array}, and stores it as the current scan
    #     for the per-channel `trace` parameters. The arrays are all the same
    #     length and share time_axis; they are NOT simultaneous - add each
    #     channel's time_offset() if the skew matters.

    #     WHY THIS DOES NOT CALL acquire_chunks(). The two look like the same
    #     loop, and the duplication is real - about fifteen lines. It is kept on
    #     purpose, for three reasons, in order of how much they matter:

    #     1. WHEN _on_armed FIRES. Here it fires immediately after task.start(),
    #        on the line below it, always, in this thread. acquire_chunks is a
    #        GENERATOR: its body does not run at all until the caller asks for
    #        the first chunk, so the task would be built and armed - and the
    #        callback fired - at whatever moment the consumer happened to start
    #        iterating. For a record that just returns an array that is
    #        harmless; for the OPX handshake, where the callback launches the
    #        job that emits the trigger, "whenever the caller gets round to it"
    #        is not a specification.

    #     2. WHEN THE TASK IS CLOSED. `with nidaqmx.Task()` inside a generator
    #        only unwinds when the generator is exhausted, closed, or collected.
    #        A caller who abandons it half way leaves the task open until the
    #        garbage collector notices - and an open AI task holds the device.
    #        Here the with-block is in a plain function, so it unwinds when the
    #        function returns, exception or not.

    #     3. SHAPE. This builds ONE 2-D array and slices it into rows, so the
    #        whole record is two allocations. Going through acquire_chunks would
    #        produce a dict of fresh arrays per chunk - five dicts a second for
    #        the length of the record - and then need concatenating per channel.
    #        Fine for streaming, where the point is that you never hold it all;
    #        wasteful for a record you are going to hold anyway.

    #     Use acquire_chunks when the record is too long for RAM, or when you
    #     want to see it as it arrives. Use acquire() when you want the record.
    #     """
    #     n_target = self.n_samples()
    #     rate = self._verify()[0]
    #     chunk = max(100, min(n_target, int(rate // 5)))

    #     with nidaqmx.Task() as task:
    #         active = self._configure(task, chunk)
    #         rate = float(task.timing.samp_clk_rate)
    #         self._actual_rate = rate

    #         reader = AnalogMultiChannelReader(task.in_stream)
    #         # float64 is not a choice: DAQmxReadAnalogF64 writes doubles, and
    #         # nidaqmx type-checks the array it is handed. The downcast to
    #         # float32 happens on the copy below - 18 ADC bits sit inside
    #         # float32's 24-bit mantissa with ~6 bits to spare, at any range.
    #         buf = np.zeros((len(active), chunk), dtype=np.float64)

    #         blocks = []
    #         task.start()                       # arms; waits if triggered
    #         if self._on_armed is not None:  #TODO: this is where the OPX job should be launched, but it is not yet implemented. We might run it and it acquisition in a thread.
    #             self._on_armed()               # e.g. qm.execute(prog)
    #         try:
    #             while len(blocks) * chunk < n_target:
    #                 reader.read_many_sample(buf, number_of_samples_per_channel=chunk, timeout=60.0)
    #                 blocks.append(buf.astype(np.float32))   # copies, and halves the record
    #         except nidaqmx.errors.DaqError as e:
    #             raise RuntimeError(self._overflow_message(e, rate, len(active), len(blocks) * chunk)) from e
    #         finally:
    #             task.stop()
    #     # Concatenate the blocks into one array, and trim to the requested length.
    #     data = np.concatenate(blocks, axis=1)[:, :n_target]
    #     self._last = {ch.short_name: data[i] for i, ch in enumerate(active)}
    #     self._generation += 1  
    #     return self._last
    

    def acquire(self):
        """Acquire one record on every enabled channel and store all in the current scan, and trace of each channel can pick its own row out of that.

        Returns {channel name: 1-D array}, and stores it as the current scan
        for the per-channel `trace` parameters. The arrays are all the same
        length and share time_axis; they are NOT simultaneous - add each
        channel's time_offset() if the skew matters.

        Use acquire_chunks() when the record is too long for RAM, or when you
        want to see it as it arrives. Use acquire() when you want the record.
        """
        n_target = self.n_samples()
        chunks = []

        # start_armed=True. This is ONE task, so the callback fires once, on
        # the line after task.start() - exactly where it fired when this loop
        # was written out here. Leaving it at the default would mean _on_armed
        # never fires from acquire() at all, and the OPX would emit its marker
        # into a board that was never told to wait for it.
        #
        # And no `break`: acquire_chunks already stops once it has `duration`
        # worth, so letting it END is not laziness - a generator abandoned
        # mid-yield keeps its task open until the garbage collector notices,
        # whereas one that finishes runs its own finally here and now.
        for _, chunk in self.acquire_chunks(duration=self.duration(),
                                            start_armed=True):
            chunks.append(chunk)

        if not chunks:
            raise RuntimeError(
                f"no data: {self.duration():g} s at "
                f"{self._verify()[0]:,.0f} S/s produced no chunks")

        # Trim: the last read is a whole chunk, so it can overshoot n_target
        # by up to chunk-1 scans. Every channel is trimmed to the same length,
        # which is what the trace parameters' Arrays(shape=(n_samples,))
        # validator expects.
        self._last = {name: np.concatenate([c[name] for c in chunks])[:n_target] for name in chunks[0]}
        self._generation += 1
        return self._last

    def acquire_chunks(self, duration=None, start_armed=False):
        """Yield (i0, {channel: array}) as each chunk arrives, constant memory.

        i0    scan index of the chunk's first sample, common to every channel,
              so t = (i0 + arange(len)) / actual_rate()
        dict  a fresh copy per chunk - safe to keep or write out.

        duration=None runs until KeyboardInterrupt (the stop button in
        Jupyter). Use this instead of acquire() when the record is too long to
        hold in RAM, or when you want to see it as it arrives.

        Does NOT feed the `trace` parameters: a streaming read has no single
        record for them to return.
        """
        rate = self._verify()[0]
        n_target = None if duration is None else int(round(duration * rate))
        chunk = max(100, int(rate // 5))
        if n_target is not None:
            chunk = min(chunk, n_target)

        with nidaqmx.Task() as task:
            active = self._configure(task, chunk)
            rate = float(task.timing.samp_clk_rate)
            self._actual_rate = rate
            names = [c.short_name for c in active]

            reader = AnalogMultiChannelReader(task.in_stream)
            buf = np.zeros((len(active), chunk), dtype=np.float64)  # DAQmx writes doubles

            n_done = 0  # number of samples done so far, per channel
            task.start()                       # arms; waits if triggered
            if self._on_armed is not None:
                if start_armed: self._on_armed();

            try:
                while n_target is None or n_done < n_target:
                    reader.read_many_sample(buf, number_of_samples_per_channel=chunk, timeout=60.0)
                    
                    # return the number of samples done so far, and a dict of channel names to arrays. Ready for the next chunk. The arrays are copies, so the caller can keep them.
                    yield n_done, {nm: buf[i].astype(np.float32)for i, nm in enumerate(names)}
                    n_done += chunk     # increment the number of samples done so far, per channel
            except KeyboardInterrupt:
                self.log.info("stopped by user after %d samples (%.3f s)",
                              n_done, n_done / rate)
            except nidaqmx.errors.DaqError as e:
                raise RuntimeError(self._overflow_message(
                    e, rate, len(active), n_done)) from e
            finally:
                task.stop()

    def read_for(self, channel):
        """Pick one channel's array out of the scan currently in hand.

        Never measures. acquire() is the ONLY thing that touches the hardware,
        and you call it - see the module docstring. Raises rather than
        returning anything if there is no scan to pick from, because silently
        handing back a stale array is the one failure that would not be
        noticed until the run was over.
        """
        name = channel.short_name

        if self._last is None:
            raise RuntimeError(
                f"no acquisition to read {name} from. Call daq.ai.acquire() "
                f"first - it measures every enabled channel at once, and "
                f"trace() only picks one channel out of the result.")

        if name not in self._last:
            raise RuntimeError(
                f"{name} was not in the last acquisition, which holds "
                f"{sorted(self._last)}. Either it was not enabled when "
                f"acquire() ran, or the configuration changed since. Enable "
                f"it and call daq.ai.acquire() again.")

        return self._last[name]

    @property
    def last(self):
        """The scan currently in hand, as {channel: array}. None if acquire()
        has not run, or if the configuration changed since it did."""
        return self._last

    # =============================================================== checks
    def check(self):
        """Problems with the current AI configuration, without acquiring.

        Returns a list of (level, where, message). See USB6289.check().
        """
        problems = []
        active = self.active

        for test in (self._check_channel_set, self._check_rate):
            try:
                test(active)
            except ValueError as e:
                problems.append(("error", "ai", str(e)))

        if problems:
            return problems              # _verify would only re-raise these

        try:
            # Builds the task and hands it to DAQmx for verification, which
            # programs the clocks without starting them. This is what catches
            # a trigger terminal that cannot be routed - the failure that
            # otherwise appears only when the run starts.
            rate, _ = self._verify()
        except Exception as e:
            problems.append(("error", "ai",
                             f"DAQmx rejected the AI task: {e}"))
            return problems

        megabytes = self.n_samples() * len(active) * 4 / 1e6
        if megabytes > 1000:
            problems.append((
                "warning", "ai",
                f"one acquire() would allocate {megabytes / 1000:.1f} GB "
                f"({self.duration():g} s x {rate:,.0f} S/s x {len(active)} "
                f"channels, float32). Use acquire_chunks() or long_run() "
                f"instead of holding it in RAM."))
        return problems

    # ============================================================ metadata
    def describe(self):
        """Everything needed to interpret a record from this subsystem.

        Deliberately includes the requested AND achieved rates: the sample
        clock divides the 20 MHz timebase, so what you asked for and what the
        hardware did are usually different numbers, and only the second gives
        a correct time axis.

        Named describe(), not metadata(): QCoDeS sets a `metadata` DICT on
        every Metadatable in __init__, which as an instance attribute shadows
        any method of that name. Confirm on the lab machine before assuming v1
        behaved - see the note in usb6289.describe().
        """
        try:
            rate, conv = self._verify()
        except Exception:
            rate = conv = None
        return {
            "rate_requested": self.rate(),
            "rate_actual": rate,
            "conv_rate": conv,
            "duration": self.duration(),
            "trigger": self.trigger(),
            "n_samples": self.n_samples() if rate else None,
            "channels": [
                {"name": c.short_name,
                 "index": c.index,
                 "position_in_task": i,
                 "time_offset": (i / conv) if conv and np.isfinite(conv)
                                else None,
                 "v_min": c.v_min(), "v_max": c.v_max(),
                 "terminal_config": c.terminal_config()}
                for i, c in enumerate(self.active)],
            "limits": {"max_single_rate": self._max_single_rate,
                       "max_multi_rate": self._max_multi_rate,
                       "ranges": list(self.ranges)},
        }
