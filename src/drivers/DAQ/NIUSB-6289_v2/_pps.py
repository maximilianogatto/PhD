"""
The 1 pps counter of the NI USB-6289, as a QCoDeS instrument module.

WHAT IT MEASURES. The FS725 rubidium emits one pulse per ATOMIC second. The
board's own clock does not know it is wrong - it divides its 20 MHz timebase
and reports a sample rate with great confidence, and over a day that rate is
off by seconds. Comparing the two is the whole point of this module: it
records, for every atomic second, how many scans the board had taken by then.
Those numbers ARE the true sample rate, in scans per atomic second, and they
are what makes a multi-day time axis mean anything.

    daq.pps.terminal("/Dev1/PFI9")     # 1 pps -> screw terminal 83
    daq.pps.counter("ctr1")            # leaves ctr0 for the markers
    data, edges = daq.acquire_with_pps()
    t = times_from_edges(np.arange(len(data["ai0"])), edges)

THE MECHANISM IS IN _stamper.py, because it is not specific to the rubidium: a
counter that records the scan index of every pulse on a PFI line. Everything
here is what makes those pulses ATOMIC SECONDS rather than just pulses -
namely that consecutive values must be one second apart, which is a check no
other signal allows and which is how the artefacts get found.

MULTICHANNEL CHANGES NOTHING HERE. ai/SampleClock ticks once per SCAN, not
once per conversion, so an edge index means the same thing whether one channel
or six are enabled. (ai/ConvertClock is the per-channel one. Never count
that - the numbers would be meaningless the moment a channel is added.)
"""

import numpy as np

from _stamper import ScanStamper


class PPSCounter(ScanStamper):
    """One counter, dedicated to timestamping atomic seconds.

    No ChannelList: this is a subsystem with no repeated parts. It uses ONE of
    the board's counters - the last one by default, leaving ctr0 free for the
    markers - and claims it from the root instrument so nothing else can take
    it while a run is going.
    """

    WHAT = "1 pps pulse"

    def __init__(self, parent, name="pps"):
        # default_counter=-1: the LAST counter, leaving the first for markers
        super().__init__(parent, name, default_counter=-1)

        self._last_edges = None      # edges of the last completed record
        self._last_edges_raw = None  # ...before clean_edges dropped any

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

    @property
    def last_edges_raw(self):
        """The last edge table before clean_edges, for forensics."""
        return self._last_edges_raw

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

    def set_last_edges(self, edges):
        """Record the edge table of a completed record, artefacts removed.

        Called by whoever coordinated the acquisition (the root's
        acquire_with_pps, or long_run). Kept explicit rather than done inside
        collect(), because a streaming run collects many times per record and
        only the coordinator knows where a record ends.

        Every acquisition ends with one latched value that is NOT an atomic
        second: when the AI task stops the sample clock stops, the count
        freezes, and one more value comes back carrying it. A triggered
        acquisition adds several at the START, for the same reason - the clock
        does not run while the task waits for its edge. Left in, they destroy
        the rate estimate: on a 60 s record a 16 ms straggler turned +14 ppm
        into +16,403 ppm. clean_edges finds them by the one test only atomic
        seconds pass - consecutive values must be a whole number of seconds
        apart. See postprocess.

        The raw table is kept in last_edges_raw. Nothing is thrown away, and
        edges.i64 is still written raw by long_run.
        """
        from postprocess import clean_edges

        self._last_edges_raw = np.asarray(edges, dtype=np.int64)
        try:
            scans_per_second = self.root_instrument.ai.actual_rate()
        except Exception:
            # clean_edges falls back to the median gap, so a failure to read
            # the rate no longer means no filtering at all.
            scans_per_second = None

        self._last_edges, dropped = clean_edges(self._last_edges_raw, scans_per_second)
        if len(dropped):
            self.log.info(
                "dropped %d latched value(s) that are not whole atomic "
                "seconds (%s) - the counter's start value, the "
                "end-of-acquisition one, and any caught while the task was "
                "waiting for a trigger", len(dropped), dropped.tolist()[:5])
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
