"""
Look at a long run while it is still running, and navigate it.

Reads the .f32 files DIRECTLY, not the driver's live buffer. That buffer is a
rolling 60 s of one channel - a health check, deliberately small. The file has
the whole run, it is flushed about five times a second, and reading the part
of it you are looking at costs nothing. So you can scroll back to something
that happened an hour ago without interrupting the acquisition.

    from livescope import LiveScope
    scope = LiveScope(outdir, channel="ai0")     # follows the end of the file
    scope.show()

MIN/MAX DECIMATION, NOT STRIDING. This is the part that matters if you are
looking for particles. A 300 s window at 25 kS/s is 7.5 million samples and
your screen has ~1500 pixels, so something has to go. Taking every 5000th
sample is the obvious thing and it is WRONG: a 200 us spike is 5 samples wide,
so striding misses it about 999 times in 1000, and your rare events quietly
do not exist.

Instead each pixel column shows the MIN and MAX of the samples behind it,
drawn as a vertical extent. Every spike survives, whatever the zoom, because a
spike changes the max of its column. The trace looks like an oscilloscope's,
which is not a coincidence - this is what a digital scope does for the same
reason.

    zoomed out    each column spans 5000 samples -> a filled band, and any
                  spike sticks out of it
    zoomed in     fewer samples than pixels -> exact samples, no decimation

WHICH TIME AXIS. While a run is going this uses the board's own clock: scan
index / nominal rate. That is off by ~1 s/day, which does not matter for
LOOKING at data. The atomic axis needs the edge table, which is still being
written; apply it offline with postprocess.times_from_edges when you analyse.
The scan index shown alongside is the same number in either axis, so anything
you spot here can be found exactly later.
"""

import json
import time
from pathlib import Path

import numpy as np

BYTES = 4          # float32


class RunFiles:
    """The .f32 files of a run, as they grow.

    Deliberately re-reads the manifest and the file sizes on demand rather
    than caching: the run is still writing, so anything cached is a lie a few
    hundred milliseconds later.
    """

    def __init__(self, outdir, channel=None):
        self.outdir = Path(outdir)
        manifest_path = self.outdir / "manifest.jsonl"
        if not manifest_path.exists():
            raise FileNotFoundError(f"no run in {self.outdir}")

        header = {}
        for line in manifest_path.read_text().splitlines():
            if line.strip():
                entry = json.loads(line)
                if entry.get("event") == "start":
                    header = entry
                    break
        self.channels = header.get("channels", [])
        self.rate = header.get("nominal_rate", 25_000.0)
        self.channel = channel or (self.channels[0] if self.channels else None)
        if self.channel is None:
            raise ValueError(f"no channels recorded in {manifest_path}")

    def segments(self):
        """[(start_scan, path)] for this channel, in order.

        Includes the segment currently being written, which has no manifest
        entry yet - it is found by name. Without that you could not watch a
        run that has not rotated once, which is the normal case.
        """
        found = {}
        for line in (self.outdir / "manifest.jsonl").read_text().splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("channel") == self.channel and "file" in entry:
                found[entry["index"]] = (entry["start_sample"],
                                         self.outdir / entry["file"])

        for path in sorted(self.outdir.glob(f"seg_*_{self.channel}_*.f32")):
            index = int(path.name.split("_")[1])
            if index not in found:
                # Open segment: its start is where the previous one ended.
                previous = found.get(index - 1)
                start = 0
                if previous is not None:
                    start = previous[0] + previous[1].stat().st_size // BYTES
                found[index] = (start, path)
        return [found[i] for i in sorted(found)]

    def n_scans(self):
        """Scans written so far, across every segment."""
        segments = self.segments()
        if not segments:
            return 0
        start, path = segments[-1]
        return start + path.stat().st_size // BYTES

    def read(self, start_scan, n_scans):
        """Samples [start_scan, start_scan + n_scans), across segment joins.

        Reads only the bytes asked for - np.fromfile with an offset - so
        showing a 10 s window of a week-long file touches 1 MB, not 60 GB.
        """
        out = []
        want_from, want_to = start_scan, start_scan + n_scans
        for seg_start, path in self.segments():
            seg_n = path.stat().st_size // BYTES
            seg_end = seg_start + seg_n
            if seg_end <= want_from or seg_start >= want_to:
                continue
            lo = max(want_from, seg_start) - seg_start
            hi = min(want_to, seg_end) - seg_start
            out.append(np.fromfile(path, dtype=np.float32,
                                   count=hi - lo, offset=lo * BYTES))
        if not out:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(out)


def minmax_decimate(y, n_columns):
    """(index, low, high) - the min and max of each pixel column.

    Returns exact samples when there are fewer than n_columns of them, so
    zooming in stops decimating without a special case.

    This is what keeps a narrow spike visible at any zoom: it changes the max
    of whatever column it lands in. Striding would drop it entirely.
    """
    y = np.asarray(y)
    if len(y) <= n_columns:
        idx = np.arange(len(y), dtype=np.float64)
        return idx, y, y

    per = len(y) // n_columns
    usable = per * n_columns
    block = y[:usable].reshape(n_columns, per)
    low, high = block.min(axis=1), block.max(axis=1)
    idx = (np.arange(n_columns) + 0.5) * per
    return idx, low, high


def window(outdir, channel=None, start=0.0, width=10.0, columns=1500):
    """One window of a run, ready to plot, in seconds of BOARD time.

    Returns (t, low, high, info). Plot it with fill_between(t, low, high) -
    that is the oscilloscope look, and it is honest about what is inside each
    column. When the window is narrow enough that no decimation happened,
    low == high == the samples themselves, so the same call also gives you an
    exact trace when you zoom in.
    """
    files = RunFiles(outdir, channel)
    rate = files.rate
    total = files.n_scans()

    start_scan = max(0, int(round(start * rate)))
    n_scans = max(1, int(round(width * rate)))
    start_scan = min(start_scan, max(0, total - 1))
    n_scans = min(n_scans, total - start_scan)

    y = files.read(start_scan, n_scans)
    idx, low, high = minmax_decimate(y, columns)
    t = (start_scan + idx) / rate
    info = {"channel": files.channel, "rate": rate, "total_scans": total,
            "total_seconds": total / rate, "start_scan": start_scan,
            "n_scans": len(y), "samples_per_column": max(1, len(y) // columns)}
    return t, low, high, info


class LiveScope:
    """A scrolling, zoomable view of a run that is still being written.

    Works in a Jupyter notebook with `%matplotlib widget` (pip install ipympl)
    for real pan and zoom; falls back to redrawing a static figure otherwise.
    ipywidgets adds the follow/position/width controls if it is installed.

        scope = LiveScope(outdir, channel="ai0", width=5.0)
        scope.show()                     # follows the end of the file
        scope.goto(120.0)                # jump to t = 120 s, stop following
        scope.follow()                   # back to live

    Nothing here talks to the DAQ, so it cannot disturb the acquisition. You
    can even run it in a different process, or on a different machine over a
    network share.
    """

    def __init__(self, outdir, channel=None, width=5.0, columns=1500,
                 threshold=None, refresh=0.5):
        self.outdir = Path(outdir)
        self.files = RunFiles(outdir, channel)
        self.width = width
        self.columns = columns
        self.threshold = threshold
        self.refresh = refresh
        self.start = None            # None = follow the end of the file
        self._fig = self._ax = None

    # ------------------------------------------------------------ position
    def follow(self):
        """Track the end of the file as it grows."""
        self.start = None
        return self

    def goto(self, seconds):
        """Show a fixed window starting at `seconds` of board time."""
        self.start = max(0.0, float(seconds))
        return self

    def pan(self, fraction=1.0):
        """Move by a fraction of the window width. Negative goes back."""
        current = self._current_start()
        return self.goto(current + fraction * self.width)

    def zoom(self, factor=2.0):
        """Widen (>1) or narrow (<1) the window, keeping the centre."""
        centre = self._current_start() + self.width / 2
        self.width = max(1e-3, self.width * factor)
        return self.goto(max(0.0, centre - self.width / 2))

    def _current_start(self):
        if self.start is not None:
            return self.start
        total = self.files.n_scans() / self.files.rate
        return max(0.0, total - self.width)

    # ---------------------------------------------------------------- data
    def data(self):
        """(t, low, high, info) for what is on screen right now."""
        return window(self.outdir, self.files.channel, self._current_start(),
                      self.width, self.columns)

    def events(self, threshold=None, rising=True):
        """Scan indices crossing a threshold in the CURRENT window.

        Uses the decimated maxima to find candidates and then re-reads the
        raw samples around each one, so a spike narrower than a pixel column
        is still located exactly. Returns global scan indices - the same
        numbers the 1 pps edge table uses, so an event and an atomic second
        can be compared directly.
        """
        threshold = self.threshold if threshold is None else threshold
        if threshold is None:
            raise ValueError("no threshold set")
        start_scan = int(round(self._current_start() * self.files.rate))
        n_scans = int(round(self.width * self.files.rate))
        y = self.files.read(start_scan, n_scans)
        above = y >= threshold
        if rising:
            hits = np.flatnonzero(~above[:-1] & above[1:]) + 1
        else:
            hits = np.flatnonzero(above[:-1] & ~above[1:]) + 1
        return hits + start_scan

    # ---------------------------------------------------------------- draw
    def _draw(self):
        """Redraw the window into the SAME axes.

        ax.clear() every time, on purpose: without it each frame would add
        another Line2D to the axes and the figure would get slower and
        heavier for the length of the run - a very common way to make a live
        plot grind to a halt after a few minutes. clear() is cheap here
        because there are only a few artists.
        """
        t, low, high, info = self.data()
        ax = self._ax
        ax.clear()
        ax.fill_between(t, low, high, step="mid", linewidth=0,
                        color="tab:blue")
        if self.threshold is not None:
            ax.axhline(self.threshold, color="tab:red", lw=1, ls="--")
            n_events = len(self.events())
            ax.set_title(f"{info['channel']}  |  {n_events} crossing(s) in "
                         f"view  |  {info['total_seconds']:,.1f} s recorded")
        else:
            ax.set_title(f"{info['channel']}  |  "
                         f"{info['samples_per_column']} samples/column  |  "
                         f"{info['total_seconds']:,.1f} s recorded"
                         f"{'  [following]' if self.start is None else ''}")
        ax.set_xlabel("board time [s]   (atomic correction applied offline)")
        ax.set_ylabel("V")
        ax.set_xlim(t[0], t[-1] if len(t) > 1 else t[0] + self.width)
        self._fig.canvas.draw_idle()

    def _open(self):
        import matplotlib.pyplot as plt
        if self._fig is None:
            self._fig, self._ax = plt.subplots(figsize=(12, 4))
        return self._fig

    def step(self):
        """Draw the current window ONCE. For driving from your own loop."""
        self._open()
        self._draw()
        self._flush(pause=False)
        return self

    def _flush(self, pause=True):
        """Get the figure onto the screen, whichever backend is in use.

        This is the part that decides whether "live" works at all:

          inline (the Jupyter DEFAULT) has no GUI event loop, so plt.pause()
          redraws nothing - the cell just blocks and you see one figure when
          it finishes. The figure has to be re-DISPLAYED each time, with the
          previous output cleared.

          widget (ipympl) / qt / tk have an event loop, and plt.pause() both
          runs it and sleeps. This is the one worth installing: it gives real
          pan and zoom, and it does not flicker.
        """
        import matplotlib
        import matplotlib.pyplot as plt

        if "inline" in matplotlib.get_backend().lower():
            from IPython.display import clear_output, display
            clear_output(wait=True)          # wait=True: no flicker
            display(self._fig)
            if pause:
                time.sleep(self.refresh)
        else:
            self._fig.canvas.draw_idle()
            plt.pause(self.refresh if pause else 0.001)

    def show(self, seconds=None):
        """Open the figure and keep it updating until interrupted.

        seconds=None follows until you interrupt the kernel. Works on the
        inline backend, but `%matplotlib widget` is much better - see _flush.

        If you would rather drive the loop yourself, use step():

            while run.alive:
                scope.step()
        """
        self._open()
        try:
            self._controls()
        except ImportError:
            pass

        started = time.time()
        try:
            while seconds is None or time.time() - started < seconds:
                self._draw()
                self._flush()
        except KeyboardInterrupt:
            pass
        self._draw()
        self._flush(pause=False)
        return self

    def _controls(self):
        """ipywidgets controls, if ipywidgets is installed."""
        import ipywidgets
        from IPython.display import display

        total = max(self.files.n_scans() / self.files.rate, self.width)
        position = ipywidgets.FloatSlider(value=0.0, min=0.0, max=total,
                                          step=self.width / 4,
                                          description="t [s]",
                                          continuous_update=False,
                                          layout={"width": "60%"})
        width = ipywidgets.FloatLogSlider(value=self.width, base=10,
                                          min=-3, max=3, description="width")
        following = ipywidgets.ToggleButton(value=True, description="follow")

        def on_position(change):
            if not following.value:
                self.goto(change["new"])

        def on_width(change):
            self.width = change["new"]

        def on_follow(change):
            self.follow() if change["new"] else self.goto(position.value)

        position.observe(on_position, names="value")
        width.observe(on_width, names="value")
        following.observe(on_follow, names="value")
        display(ipywidgets.HBox([following, position, width]))
