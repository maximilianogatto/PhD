"""
Continuous recording to disk, for runs of hours or weeks.

Separated from the instrument because it is a different job: the submodules
control hardware, this manages files. Its counterpart is postprocess.py, which
reads back exactly what SegmentWriter writes - keeping the two next to each
other is the point.

ON-DISK LAYOUT

    run.json          daq.describe() at the start of the run
    manifest.jsonl    one JSON object per line:
                        the first line records the run (channels, rates)
                        one line per closed FILE
                        one line per GAP, where an error interrupted the run
    edges.i64         1 pps positions, int64, as global SCAN indices
    seg_00000_ai0_20260811T101500Z.f32     signal, float32

ONE FILE PER CHANNEL PER SEGMENT. The alternative - interleaving the channels
scan by scan into one file - costs one write per chunk instead of N, which is
nothing next to the USB transfer, and makes reading a single channel require
striding the whole file. A week of ai0 should be readable without touching ai3.

SCAN INDICES, NOT SAMPLE INDICES. Every position written here counts sample
CLOCK ticks, which advance once per scan of all channels. So the edge table is
shared by every channel and lives in one file, and start_sample in the manifest
means the same thing regardless of how many channels are enabled.

THE 1 PPS IS NOT STORED. Its edges are extracted while streaming and only the
scan indices kept: 0.7 MB/day instead of 8.6 GB/day for the raw channel. Each
edge is an exact atomic second, so the edge table IS the time axis (see
postprocess.times_from_edges). No sample rate is assumed anywhere, which is
what absorbs the oscillator drift - uncorrected it is about 4 s/day.

GAPS ARE RECORDED, NEVER SPLICED. On a DAQmx error the run restarts and writes
a gap line to the manifest, and the next segment starts a new file. Two
segments either side of a gap are NOT contiguous in time, and nothing in the
data itself would tell you - only the manifest does.
"""

import json
import time
from pathlib import Path

import numpy as np


class SegmentWriter:
    """Rotating per-channel .f32 files, plus the manifest and edge table.

    Owns every file the run writes. long_run() below does the acquisition and
    hands chunks over; all bookkeeping - which segment, which byte, what to
    log - lives here.
    """

    def __init__(self, outdir, channels, rate, rotate_scans,
                 convert_q=None, verbose=True):
        self.outdir = Path(outdir)
        self.channels = list(channels)
        self.rate = rate
        self.rotate_scans = rotate_scans
        self.convert_q = convert_q
        self.verbose = verbose

        self.index = -1              # incremented by every rotate()
        self.files = {}              # {channel: open handle}
        self.names = {}              # {channel: filename}
        self.start_scan = 0          # global scan index this segment began at
        self.n = 0                   # scans written to the current segment

        # A second run in the same directory would append to the manifest
        # while restarting BOTH the segment index and the scan numbering at 0.
        # The result reads back silently wrong: load_segment(dir, 0) finds the
        # first run's entry, start_sample values collide, and edges.i64 becomes
        # two runs' atomic seconds concatenated into one table. Refuse instead:
        # a new directory per run costs nothing, and there is no merge of two
        # recordings that would be correct.
        manifest_path = self.outdir / "manifest.jsonl"
        if manifest_path.exists():
            raise FileExistsError(
                f"{manifest_path} already exists - {self.outdir} holds a "
                f"previous run. Appending would restart segment indices and "
                f"scan numbering at 0 on top of the old ones, so the manifest "
                f"and edges.i64 would silently describe two runs at once. Use "
                f"a new directory.")

        self._manifest = open(manifest_path, "x")
        self._edges = open(self.outdir / "edges.i64", "xb")
        self.rotate(0)               # open the first segment

    # ---------------------------------------------------------------- log
    def log(self, record):
        """Append one line to the manifest and flush it.

        Flushed every time on purpose: the manifest is what makes the data
        interpretable, and a run that dies must leave a usable one behind.
        """
        self._manifest.write(json.dumps(record) + "\n")
        self._manifest.flush()

    # ------------------------------------------------------------ rotation
    def _close_segment(self):
        """Close the open files and record them. Safe before the first one."""
        if not self.files:
            return
        closed_wall = time.time()
        for channel in self.channels:
            self.files[channel].close()
            name = self.names[channel]
            self.log({"file": name, "channel": channel, "index": self.index,
                      "start_sample": self.start_scan, "n": self.n,
                      "nominal_rate": self.rate, "closed_wall": closed_wall})
            if self.convert_q is not None:
                self.convert_q.put((self.outdir / name, self.start_scan,
                                    self.rate, channel))
        if self.verbose:
            mb = self.n * 4 * len(self.channels) / 1e6
            print(f"  [{self.index:05d}] {self.n:,} scans x "
                  f"{len(self.channels)} ch  {mb:,.0f} MB")
        self.files.clear()

    def rotate(self, scan):
        """Close the current segment and open the next one at `scan`.

        The index increments on EVERY rotation, unconditionally. (v1 only
        incremented it when HDF5 conversion was enabled, so with the default
        settings every manifest entry claimed to be segment 0 and
        load_segment() could only ever find the first.)
        """
        self._close_segment()
        self.index += 1
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        for channel in self.channels:
            name = f"seg_{self.index:05d}_{channel}_{stamp}.f32"
            self.names[channel] = name
            self.files[channel] = open(self.outdir / name, "wb")
        self.start_scan = scan
        self.n = 0

    # ------------------------------------------------------------- writing
    def write(self, data, scan):
        """Write one chunk - {channel: array} - and rotate if the segment is
        full. `scan` is the global index of the chunk's first sample.

        Flushed per chunk (about 5 per second) so the tail of a file can be
        read while it is still being written.
        """
        length = 0
        for channel in self.channels:
            values = data[channel]
            values.astype(np.float32, copy=False).tofile(self.files[channel])
            self.files[channel].flush()
            length = len(values)
        self.n += length

        if self.n >= self.rotate_scans:
            self.rotate(scan + length)
        return length

    def write_edges(self, edges):
        """Append 1 pps positions. Shared by every channel: they are scan
        indices, and all channels are on the same scan clock."""
        if len(edges):
            edges.astype(np.int64, copy=False).tofile(self._edges)
            self._edges.flush()

    def close(self, scan):
        """Close the final segment and every file this writer owns."""
        self._close_segment()
        if self.convert_q is not None:
            self.convert_q.put(None)      # exactly once, at the very end
        self._edges.close()
        self._manifest.close()


class LongRun:
    """Handle for a long_run() started with background=True."""

    def __init__(self, daq, thread, outdir):
        self._daq, self._thread, self.outdir = daq, thread, Path(outdir)

    @property
    def alive(self):
        return self._thread.is_alive()

    def status(self):
        """Live progress: scans, seconds, current segment."""
        return dict(self._daq._run_state, alive=self.alive,
                    outdir=str(self.outdir))

    def stop(self, timeout=60):
        """Ask the run to finish; the current segment is closed cleanly."""
        self._daq._stop.set()
        self._thread.join(timeout)
        return not self.alive

    def join(self, timeout=None):
        self._thread.join(timeout)
        return not self.alive

    def __repr__(self):
        state = "running" if self.alive else "finished"
        return f"<LongRun {state} {self.outdir}>"
