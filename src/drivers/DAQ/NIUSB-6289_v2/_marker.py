"""
Marking WHEN things happened, on the same ruler as the data.

The 1 pps gives you a ruler: every edge is one atomic second, and you know the
scan index of each. What it does not give you is where the interesting moments
are. This module is the other half - a counter on the second PFI line that
records the scan index of every pulse the OPX (or anything else) sends:

    daq.marker.terminal("/Dev1/PFI10")     # markers -> screw terminal 85
    run = daq.long_run(outdir, hours=8, background=True)
    ...
    marks = np.fromfile(outdir / "markers.i64", dtype=np.int64)
    t = times_from_edges(marks, edges, scans_per_second=rate)  # atomic seconds

Same mechanism as the 1 pps, in _stamper.py. Same units, because that is the
point: markers, 1 pps edges, samples and segment boundaries are ALL scan
indices, so relating an event to the data is `y[mark]` and relating it to the
rubidium is one call. Nothing is converted, so nothing can be converted wrong.

WHY THE SCAN INDEX AND NOT A TIME. A time would have to be derived, by
dividing by a rate that is itself wrong by ~12 ppm, and the derived value
would be frozen into the file at the moment the driver's clock correction
happened to be whatever it was. Scan indices are what the hardware measures,
they are exact integers, and they can be re-timed by re-running
times_from_edges when the correction improves - which it has, twice.

WHAT THE OPX SHOULD SEND. One line, one kind of pulse, one pulse per event
worth marking: a run starting, a run ending, a buffer drain beginning. Only
ONE counter is free - pps holds the other - so the hardware cannot tell one
kind of marker from another. Let ORDER carry the meaning instead: the OPX
program defines the sequence, so mark 0 is whatever it emits first. The DAQ
only has to say when.

THE TIDIEST ARRANGEMENT: THE SAME LINE AS THE TRIGGER.

    daq.ai.setup(..., trigger="/Dev1/PFI8")
    daq.marker.terminal("/Dev1/PFI8")      # the SAME line

A PFI input fans out inside the board, so one wire can feed the start trigger
and a counter at once. Then the pulse that starts the acquisition is also its
first marker, and it lands at scan 0 - which is where the trigger is BY
DEFINITION, so the leading 0 in markers.i64 is not an artefact, it is the
answer. Every later pulse on the same line is an ordinary marker. One cable,
one PFI line, and the trigger arrives already timestamped on the same ruler as
everything else.

    marks[0] == 0        the trigger
    marks[1:]            whatever the OPX did next

The one thing this arrangement demands: whatever emits the pulse must be
launched from ai.set_on_armed(). A pulse sent before the task has armed
triggers nothing - the task is not listening yet - and still latches a 0 into
the marker table, so you get a mark with no acquisition behind it. The
callback fires in the only moment when the board is armed and nothing has been
asked to fire yet, which is exactly the window this needs.

WHAT THIS BUYS. The DAQ records continuously; the OPX does not. So the markers
say which stretches of an unbroken recording correspond to the OPX doing
something, and which are the gaps while it drained a buffer or reset. The DAQ
becomes a witness for an instrument that stops - it never does, so it can
testify about the moments when the other one did.

REPEATED VALUES ARE REAL HERE. Unlike the 1 pps, there is no way to validate a
marker: events are irregular by nature, so any interval is plausible. Two
marks at the same scan index mean two pulses arrived within one scan period
(40 us at 25 kS/s) - or that the AI clock was stopped, in which case they mean
"while nothing was being recorded". Both are reported rather than filtered.
"""

import numpy as np

from _stamper import ScanStamper


class MarkerCounter(ScanStamper):
    """A counter that records the scan index of every marker pulse."""

    WHAT = "marker"

    def __init__(self, parent, name="marker"):
        # default_counter=0: the FIRST counter. pps takes the last, so the two
        # do not collide, and claim_counter says so loudly if they ever would.
        super().__init__(parent, name, default_counter=0)

        self._last_marks = None

        self.add_parameter(
            "n_marks", label="markers in the last record", unit="marks",
            set_cmd=False,
            get_cmd=lambda: 0 if self._last_marks is None
            else int(len(self._last_marks)))

        self.add_parameter(
            "enabled", label="marker counter in use", set_cmd=False,
            get_cmd=lambda: bool(self.terminal()),
            docstring="markers are recorded only when terminal is set. "
                      "Unlike the 1 pps, nothing requires them: a run with no "
                      "marker line is complete, it just has no annotations.")

    @property
    def last_marks(self):
        """Scan indices of the markers in the last completed record."""
        return self._last_marks

    def set_last_marks(self, marks):
        """Record the marks of a completed record.

        NOT filtered, unlike the 1 pps. There is no test a marker can fail:
        events are irregular, so every interval is plausible and any filter
        would be guessing. Repeats are kept and reported - see the module
        docstring for what they mean.
        """
        self._last_marks = np.asarray(marks, dtype=np.int64)
        repeats = int((np.diff(self._last_marks) == 0).sum()) \
            if len(self._last_marks) > 1 else 0
        if repeats:
            self.log.info(
                "%d marker(s) share a scan index with the previous one: two "
                "pulses inside one scan period, or the AI clock was stopped",
                repeats)
        return self._last_marks

    # ================================================================ checks
    def check(self):
        """Problems with the current marker configuration."""
        problems = []
        if not self.terminal():
            return problems                    # not using markers is fine

        trigger = self.root_instrument.ai.trigger()
        if trigger and trigger == self.terminal():
            problems.append((
                "note", "marker",
                f"marker.terminal and ai.trigger are both {trigger}, so the "
                f"pulse that STARTS the acquisition is also its first marker "
                f"- and it lands at scan 0, which is where the trigger is by "
                f"definition. Deliberate and one cable fewer; flagged so the "
                f"leading 0 in markers.i64 is not mistaken for an artefact. "
                f"Launch whatever emits the pulse from ai.set_on_armed(), or "
                f"a pulse sent before the task arms is lost AND leaves a "
                f"spurious 0."))

        if self.terminal() == self.root_instrument.pps.terminal():
            problems.append((
                "error", "marker",
                f"marker.terminal and pps.terminal are both "
                f"{self.terminal()} - one line cannot carry both the "
                f"rubidium and the OPX. Move one; daq.terminals() lists the "
                f"free lines."))

        owner = self.root_instrument._counter_owners.get(self.counter())
        if owner is not None and owner != self.full_name:
            problems.append((
                "error", "marker",
                f"counter {self.counter()} is held by {owner}"))

        if self.counter() == self.root_instrument.pps.counter():
            problems.append((
                "error", "marker",
                f"marker and pps are both set to counter {self.counter()}. "
                f"This board has two; give them one each."))
        return problems

    # ============================================================== metadata
    def describe(self):
        return {"terminal": self.terminal(),
                "counter": self.counter(),
                "enabled": bool(self.terminal()),
                "running": self._task is not None,
                "n_marks_last": self.n_marks()}
