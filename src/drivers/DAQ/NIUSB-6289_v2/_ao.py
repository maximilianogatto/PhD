"""
Analog output subsystem of the NI USB-6289, as a QCoDeS instrument module.

Two levels, the same shape as _ai.py:

    AnalogOutput   the subsystem. Owns the ONE task, the ONE sample clock, and
                   therefore the waveform settings - shape, freq, amp, offset,
                   rate, duty, trigger - because they belong to the clock, not
                   to a pin.
    AOChannel      one physical output, 4 copies in a ChannelList. Owns what a
                   pin can decide for itself: what it is for (role) and, if it
                   is a DC level, what voltage (dc).

ONE WAVEFORM, SEVERAL LEVELS. The board has one AO clock and permits one AO
task, so at most one channel can carry a waveform. That is what `role` says:

    "off"    not in the task at all
    "dc"     held at a constant voltage
    "wave"   generating the waveform. At most one channel, enforced -
             setting it here takes it away from whoever had it.

    daq.ao.ao0.role("wave")             # or daq.ao.wave_channel("ao0")
    daq.ao.shape("square"); daq.ao.freq(100); daq.ao.amp(1.0)
    daq.ao.ao1.dc(0.5)                  # ao1 held at +0.5 V
    daq.ao.start()

    daq.ao.channels.dc()                # (0.0, 0.5, 0.0, 0.0) - broadcast get

Pure DC mode is just "no channel has role 'wave'": four independent levels,
applied the moment you set them, with no clock and nothing to start.

    daq.ao.wave_channel(None)
    for pin, volts in zip(daq.ao.channels, (3.3, 2.0, 0.0, 5.0)):
        pin.dc(volts)

TWO WAYS OF HOLDING A VOLTAGE, and the driver switches between them for you:

    waveform running   the DC channels ride in the SAME buffer as the
                       waveform, as constant arrays. They have to: one task,
                       one clock, every channel in it needs samples.
    no waveform        a plain untimed ("on-demand") write. On-demand outputs
                       keep their value after the task closes, which is what
                       lets the driver set a level and walk away.

WHY THE SETTINGS ARE STORED TWICE. Each waveform parameter keeps its value in
self._cfg as well as in the QCoDeS cache, and get_cmd reads from _cfg. QCoDeS
runs set_cmd BEFORE updating its cache, so a hook that re-read the parameter
would see the OLD value - and the live restart would rebuild the waveform with
stale settings. Writing our own store first makes the new value visible
immediately. The same trick is used for role and dc on the channels.

THE FIFO IS THE POINT OF ao_rate. If the whole buffer fits the board's onboard
FIFO, the loop regenerates from onboard memory: no USB traffic, so a saturated
AI stream cannot starve it (DaqError -200621, underflow). If it does not fit,
the waveform is pushed across USB continuously and will eventually break under
heavy AI load. `onboard` answers this for the current settings, before you
start. One FIFO is shared by every channel in the task, so adding a DC channel
shrinks the depth available to the waveform.
"""

from contextlib import contextmanager
from functools import partial

import numpy as np
import nidaqmx
from nidaqmx.constants import (
    AcquisitionType, Edge, RegenerationMode, TaskMode,
)

from qcodes.instrument import ChannelList, InstrumentChannel, InstrumentModule
from qcodes.validators import Enum, Numbers

UNDERFLOW = -200621          # driver not writing fast enough
AO_MAX = 10.0                # +/- volts
AO_FIFO_FALLBACK = 8191      # M Series spec, used only when the device is mute
SHAPES = ("square", "sine", "triangle", "ramp")
ROLES = ("off", "dc", "wave")


def _waveform(shape, phase, duty):
    """phase in [0,1) -> amplitude in [-1,1]."""
    if shape == "square":
        return np.where(phase < duty, 1.0, -1.0)
    if shape == "sine":
        return np.sin(2 * np.pi * phase)
    if shape == "triangle":
        return 4 * np.abs(phase - 0.5) - 1.0
    if shape == "ramp":
        return 2 * phase - 1.0
    raise ValueError(f"shape must be one of {SHAPES}, got {shape!r}")


# ================================================================= channel
class AOChannel(InstrumentChannel):
    """One analog output pin.

    Four copies. What a pin genuinely decides for itself is what it is FOR and,
    if it is a level, which level - everything about the waveform's shape in
    time belongs to the clock, and so to the parent.
    """

    def __init__(self, parent, name, index):
        super().__init__(parent, name)
        self.index = index

        # Backing store, for the same reason as the parent's _cfg: get_cmd
        # must see the new value during set_cmd, before QCoDeS updates its
        # cache. Set directly here, so construction fires no set hooks.
        self._role = "off"
        self._dc = 0.0

        self.add_parameter(
            "role", label=f"{name} role",
            get_cmd=lambda: self._role, set_cmd=self._set_role,
            initial_cache_value="off", vals=Enum(*ROLES),
            docstring="'off' (not in the task), 'dc' (held at a constant "
                      "voltage) or 'wave' (generating the waveform). Only one "
                      "channel may be 'wave' - the board has a single AO "
                      "clock - so setting it takes it away from whichever "
                      "channel had it. Going to 'off' does NOT change the "
                      "pin's voltage: an on-demand output holds its last "
                      "value indefinitely. Set dc(0.0) first if you want it "
                      "at zero.")

        self.add_parameter(
            "dc", label=f"{name} level", unit="V",
            get_cmd=lambda: self._dc, set_cmd=self._set_dc,
            initial_cache_value=0.0, vals=Numbers(-AO_MAX, AO_MAX),
            docstring="constant voltage for this pin. Setting it on a channel "
                      "that is 'off' promotes it to 'dc' - asking for a "
                      "voltage is asking for the pin to be driven. Setting it "
                      "on the 'wave' channel is an error: a pin cannot be "
                      "both. Applied immediately; no clock, nothing to start.")

    @property
    def physical(self):
        """Fully qualified DAQmx name, e.g. 'Dev1/ao1'."""
        return f"{self.root_instrument.device}/{self.short_name}"

    def _set_role(self, value):
        if value == "wave":
            for other in self.parent.channels:
                if other is not self and other._role == "wave":
                    other._role = "off"
        self._role = value
        self.parent.invalidate_fifo()      # depth depends on the channel set
        self.parent.reapply()

    def _set_dc(self, volts):
        if self._role == "wave":
            raise ValueError(
                f"'{self.short_name}' is the waveform channel, so it cannot "
                f"also hold a DC level. Move the waveform "
                f"(daq.ao.wave_channel('aoX')) or switch it off entirely "
                f"(daq.ao.wave_channel(None)), then set dc.")
        self._dc = float(volts)
        if self._role == "off":
            self._role = "dc"              # asking for a voltage = drive it
            self.parent.invalidate_fifo()
        self.parent.reapply()

    def __repr__(self):
        detail = (f"{self._dc:+g} V" if self._role == "dc"
                  else self.parent.shape() if self._role == "wave" else "")
        return f"<AOChannel {self.short_name} {self._role} {detail}>".strip()


# =============================================================== subsystem
class AnalogOutput(InstrumentModule):
    """The AO task: one clock, one waveform, N constant levels."""

    def __init__(self, parent, name="ao"):
        super().__init__(parent, name)

        caps = parent.caps
        self._max_rate = caps["ao_max_rate"]

        # --- live task state, before the channels: their set hooks read it
        self._task = None
        self._generating = False
        self._actual_freq = None
        self._fifo = None

        # See "WHY THE SETTINGS ARE STORED TWICE" in the module docstring.
        self._cfg = {"shape": "square", "freq": 100.0, "amp": 1.0,
                     "offset": 0.0, "rate": 100_000.0, "duty": 0.5,
                     "trigger": None}

        channels = ChannelList(self, "channels", AOChannel)
        for i, ch_name in enumerate(caps["ao"]):
            channel = AOChannel(self, ch_name, i)
            channels.append(channel)
            self.add_submodule(ch_name, channel)      # daq.ao.ao0
        channels.lock()
        self.add_submodule("channels", channels)      # daq.ao.channels[0]

        # ------------------------------------------------ waveform settings
        self.add_parameter(
            "wave_channel", label="AO waveform channel",
            get_cmd=self._get_wave_channel, set_cmd=self._set_wave_channel,
            initial_cache_value=None,
            vals=Enum(*caps["ao"], None),
            docstring="the ONE channel with role 'wave', by name, or None. "
                      "Setting it is the same as setting that channel's role "
                      "- daq.ao.wave_channel('ao0') and "
                      "daq.ao.ao0.role('wave') do the same thing. None puts "
                      "the subsystem in pure DC mode. A channel already "
                      "holding a DC level cannot take it.")

        self.add_parameter(
            "shape", label="AO waveform",
            get_cmd=lambda: self._cfg["shape"],
            set_cmd=partial(self._set, "shape"), initial_cache_value="square",
            vals=Enum(*SHAPES),
            docstring="shape on the wave channel. For a constant voltage do "
                      "not use this - give the pin role 'dc' instead.")

        self.add_parameter(
            "freq", label="AO frequency", unit="Hz",
            get_cmd=lambda: self._cfg["freq"],
            set_cmd=partial(self._set, "freq"), initial_cache_value=100.0,
            vals=Numbers(1e-3, 1e6),
            docstring="snapped so a whole number of samples fits one period, "
                      "which is what makes the loop seamless; the result is "
                      "actual_freq.")

        self.add_parameter(
            "amp", label="AO amplitude", unit="V",
            get_cmd=lambda: self._cfg["amp"],
            set_cmd=partial(self._set, "amp"), initial_cache_value=1.0,
            vals=Numbers(0, AO_MAX))

        self.add_parameter(
            "offset", label="AO offset", unit="V",
            get_cmd=lambda: self._cfg["offset"],
            set_cmd=partial(self._set, "offset"), initial_cache_value=0.0,
            vals=Numbers(-AO_MAX, AO_MAX))

        self.add_parameter(
            "rate", label="AO update rate", unit="S/s",
            get_cmd=lambda: self._cfg["rate"],
            set_cmd=partial(self._set, "rate"), initial_cache_value=100_000.0,
            vals=Numbers(1, self._max_rate),
            docstring="clocks the DAC. Sets edge resolution (1/rate) and, with "
                      "freq, the buffer length - so it decides whether the "
                      "waveform fits the onboard FIFO. Independent of ai.rate: "
                      "the two subsystems have separate clocks (though they "
                      "divide the same 20 MHz timebase, which is why the board "
                      "cannot detect its own clock error).")

        self.add_parameter(
            "duty", label="AO square duty cycle",
            get_cmd=lambda: self._cfg["duty"],
            set_cmd=partial(self._set, "duty"), initial_cache_value=0.5,
            vals=Numbers(0.01, 0.99))

        self.add_parameter(
            "trigger", label="AO start trigger terminal",
            get_cmd=lambda: self._cfg["trigger"],
            set_cmd=partial(self._set, "trigger"), initial_cache_value=None,
            vals=Enum(*caps["terminals"], None),
            docstring="point this and ai.trigger at the same terminal and both "
                      "start on one edge, fixing the waveform phase at t=0.")

        # ------------------------------------------------------- read-only
        self.add_parameter(
            "actual_freq", label="AO frequency (snapped)", unit="Hz",
            get_cmd=lambda: self._actual_freq, set_cmd=False)

        self.add_parameter(
            "running", label="AO generating", set_cmd=False,
            get_cmd=lambda: self._generating)

        self.add_parameter(
            "dc_levels", label="AO static levels", unit="V", set_cmd=False,
            get_cmd=lambda: {c.short_name: c._dc for c in self.channels
                             if c._role == "dc"},
            docstring="{channel: volts} for every pin with role 'dc'. "
                      "Read-only, and a convenience for snapshots - set levels "
                      "on the channels, e.g. daq.ao.ao1.dc(0.5).")

        self.add_parameter(
            "fifo_samples", label="AO onboard FIFO depth", unit="samples",
            get_cmd=self.fifo_samples_now, set_cmd=False,
            docstring="depth per channel, reported by DAQmx "
                      "(Buf.Output.OnbrdBufSize). One FIFO is shared by every "
                      "channel in the task, so this shrinks as outputs are "
                      "added.")

        self.add_parameter(
            "buffer_len", label="AO buffer length", unit="samples",
            get_cmd=self.buffer_len_now, set_cmd=False,
            docstring="whole periods of the waveform; this is what must fit "
                      "the FIFO.")

        self.add_parameter(
            "onboard", label="AO regenerated from FIFO", set_cmd=False,
            get_cmd=lambda: self.buffer_len_now() <= self.fifo_samples_now(),
            docstring="True when the buffer fits the onboard FIFO, so the loop "
                      "needs no USB traffic and cannot be starved by a busy AI "
                      "stream. Answers for the current settings, before "
                      "starting.")

    # ============================================== channels and bookkeeping
    @property
    def active(self):
        """Channels in the task, in the order they are added to it.

        Waveform first, then the DC channels by pin number. That order is also
        the row order of the buffer written to the device.
        """
        wave = [c for c in self.channels if c._role == "wave"]
        dc = sorted((c for c in self.channels if c._role == "dc"),
                    key=lambda c: c.index)
        return wave + dc

    def _get_wave_channel(self):
        for channel in self.channels:
            if channel._role == "wave":
                return channel.short_name
        return None

    def _set_wave_channel(self, name):
        if name is None:
            for channel in self.channels:
                if channel._role == "wave":
                    channel._role = "off"
            self.invalidate_fifo()
            self.reapply()
            return
        target = getattr(self, name, None)
        if target is None:
            raise ValueError(f"'{name}' is not an output; expected one of "
                             f"{[c.short_name for c in self.channels]}")
        if target._role == "dc":
            raise ValueError(
                f"'{name}' is already a DC level ({target._dc:+g} V). A pin "
                f"cannot be both. Free it first - daq.ao.{name}.role('off') - "
                f"then set the waveform.")
        target.role("wave")

    def _set(self, key, value):
        """Store a waveform setting, then rebuild the task if one is running.

        Writing to self._cfg BEFORE re-applying is what makes a change take
        effect live; see the module docstring.
        """
        self._cfg[key] = value
        if key == "rate":
            self.invalidate_fifo()         # depth is quoted per clock config
        self.reapply()

    def invalidate_fifo(self):
        self._fifo = None

    def reapply(self):
        """Rebuild the task, but only if one exists. Every setter calls this;
        when nothing is running it is a no-op, so setting up an output costs
        no hardware traffic until you start it."""
        if self._task is not None:
            self._apply()

    # ================================================= sizes and shape maths
    def fifo_samples_now(self):
        """Onboard AO buffer depth, per channel, as DAQmx reports it.

        Cached, because it costs a task build; falls back to the M Series spec
        when the device cannot be asked.
        """
        if self._fifo is None:
            try:
                with nidaqmx.Task() as task:
                    for channel in self.active:
                        task.ao_channels.add_ao_voltage_chan(
                            channel.physical, min_val=-AO_MAX, max_val=AO_MAX)
                    task.timing.cfg_samp_clk_timing(
                        rate=self.rate(),
                        sample_mode=AcquisitionType.CONTINUOUS,
                        samps_per_chan=1000)
                    task.control(TaskMode.TASK_COMMIT)
                    self._fifo = int(task.out_stream.output_onbrd_buf_size)
            except Exception:
                self._fifo = AO_FIFO_FALLBACK
        return self._fifo

    def _shape_params(self):
        """(samples per period, periods in the buffer, snapped frequency)."""
        rate, freq = self.rate(), self.freq()
        samples_per_period = max(2, int(round(rate / freq)))
        periods = max(1, int(np.ceil(1000 / samples_per_period)))
        return samples_per_period, periods, rate / samples_per_period

    def buffer_len_now(self):
        """Buffer length for the current settings - builds no array."""
        spp, periods, _ = self._shape_params()
        return spp * periods

    def _build(self):
        """(data, n, snapped freq). `data` is (n_channels, n) in `active`
        order - row 0 is the waveform, the rest are constant arrays."""
        spp, periods, f_actual = self._shape_params()
        n = spp * periods
        phase = (np.arange(n) / spp) % 1.0
        rows = []
        for channel in self.active:
            if channel._role == "wave":
                rows.append(self.amp() * _waveform(self.shape(), phase,
                                                   self.duty())
                            + self.offset())
            else:
                rows.append(np.full(n, channel._dc))
        return np.vstack(rows), n, f_actual

    # ======================================================== task lifecycle
    def _write_static(self, zero_wave_channel=True):
        """Hold the DC channels at their levels with an untimed (on-demand)
        write. On-demand outputs keep their value after the task closes, which
        is why this needs no task to stay alive."""
        channels, values = [], []
        wave = self._get_wave_channel()
        if zero_wave_channel and wave is not None:
            channels.append(getattr(self, wave))
            values.append(0.0)
        for channel in self.channels:
            if channel._role == "dc":
                channels.append(channel)
                values.append(channel._dc)
        if not channels:
            return

        with nidaqmx.Task() as task:
            for channel in channels:
                task.ao_channels.add_ao_voltage_chan(
                    channel.physical, min_val=-AO_MAX, max_val=AO_MAX)
            task.write(values if len(values) > 1 else values[0])

    def _apply(self):
        """(Re)build the single AO task from the current state."""
        self._close_task()

        if not self._generating:
            self._write_static()
            return

        peak = self.amp() + abs(self.offset())
        if peak > AO_MAX:
            raise ValueError(
                f"amp ({self.amp()}) + |offset| ({abs(self.offset())}) = "
                f"{peak} V exceeds +/-{AO_MAX} V")

        # One range covers the whole task, so size it for the largest signal
        # on any channel in it.
        levels = [abs(c._dc) for c in self.channels if c._role == "dc"]
        span = min(AO_MAX, 1.2 * max([1.0, peak] + levels))

        data, n, f_actual = self._build()
        task = nidaqmx.Task()
        try:
            for channel in self.active:
                task.ao_channels.add_ao_voltage_chan(
                    channel.physical, min_val=-span, max_val=span)

            task.timing.cfg_samp_clk_timing(
                rate=self.rate(), sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=n)
            task.out_stream.regen_mode = RegenerationMode.ALLOW_REGENERATION

            fifo = self.fifo_samples_now()
            if n <= fifo:
                # Loop from onboard memory: no USB traffic, so a saturated AI
                # stream cannot starve the output (DaqError -200621).
                for ao_channel in task.ao_channels:
                    ao_channel.ao_use_only_on_brd_mem = True
            else:
                self.log.warning(
                    "AO buffer is %d samples, over the %d-sample FIFO: the "
                    "waveform will be regenerated across USB and may underflow "
                    "under heavy AI load. Lower ao.rate (or raise ao.freq) to "
                    "get under %d.", n, fifo, fifo)

            if self.trigger():
                task.triggers.start_trigger.cfg_dig_edge_start_trig(
                    self.trigger(), trigger_edge=Edge.RISING)

            task.write(data if data.shape[0] > 1 else data[0], auto_start=False)
            task.start()
        except Exception:
            task.close()
            raise

        self._task = task
        self._actual_freq = f_actual

    def _close_task(self):
        if self._task is None:
            return
        try:
            self._task.stop()
        except nidaqmx.errors.DaqError as e:
            if e.error_code != UNDERFLOW:
                raise
            self.log.warning("AO underflow reported at stop (%d)", UNDERFLOW)
        self._task.close()
        self._task = None

    # ============================================================== public
    def start(self):
        """Start generating on the wave channel. DC channels keep their
        levels. Returns the snapped frequency. Safe to call while running."""
        if self._get_wave_channel() is None:
            raise ValueError(
                "no channel has role 'wave' - the subsystem is in pure DC "
                "mode. daq.ao.wave_channel('ao0') to enable the generator, or "
                "just set levels with daq.ao.ao1.dc(...), which apply "
                "immediately with nothing to start.")
        self._generating = True
        self._apply()
        return self._actual_freq

    def stop(self, zero=True):
        """Stop the waveform. DC channels keep their levels; the wave channel
        goes to 0 V unless zero=False (a buffered output otherwise holds its
        last value indefinitely). Safe to call when nothing is running - the
        instrument's close() does."""
        self._generating = False
        self._close_task()
        self._actual_freq = None
        self._write_static(zero_wave_channel=zero)

    @contextmanager
    def generating(self, **overrides):
        """Generate for the duration of a `with` block, then stop and zero.

        Any parameter of this subsystem can be overridden for the block and is
        restored afterwards, so a sweep does not leave the instrument
        reconfigured:

            with daq.ao.generating(freq=200, amp=0.5):
                data = daq.ai.acquire()

        Equivalent to start() ... stop(), but the stop is guaranteed even if
        the measurement raises - which matters, because a buffered output
        otherwise holds its last value forever.
        """
        previous = {}
        try:
            for key, value in overrides.items():
                name = key[3:] if key.startswith("ao_") else key   # v1 spelling
                if name not in self.parameters:
                    raise ValueError(
                        f"no parameter '{name}' on {self.full_name}; expected "
                        f"one of {sorted(self.parameters)}")
                previous[name] = self.parameters[name]()
                self.parameters[name](value)
            self.start()
            yield self
        finally:
            try:
                self.stop()
            finally:
                for name, value in previous.items():
                    self.parameters[name](value)

    # ============================================================ metadata
    def describe(self):
        wave = self._get_wave_channel()
        return {
            "wave_channel": wave,
            "shape": self.shape() if wave else None,
            "freq_requested": self.freq() if wave else None,
            "freq_actual": self._actual_freq,
            "amp": self.amp() if wave else None,
            "offset": self.offset() if wave else None,
            "rate": self.rate() if wave else None,
            "duty": self.duty() if wave else None,
            "trigger": self.trigger(),
            "running": self._generating,
            "buffer_len": self.buffer_len_now() if wave else None,
            "onboard": (self.buffer_len_now() <= self.fifo_samples_now()
                        if wave else None),
            "dc_levels": self.dc_levels(),
            "channels": [{"name": c.short_name, "index": c.index,
                          "role": c._role, "dc": c._dc}
                         for c in self.channels],
            "limits": {"max_rate": self._max_rate, "v_max": AO_MAX},
        }
