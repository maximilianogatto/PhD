"""
Read-back and analysis for records written by the USB-6289 driver.

Free functions only - nothing here touches the hardware or needs an
instrument, so a recording can be analysed on a machine that has never seen
the DAQ. Split out of the driver for exactly that reason.

The on-disk layout written by long_run() is multichannel from v2 on:

    run.json          describe() at the start of the run
    manifest.jsonl    one line per file, plus a line for every gap
    edges.i64         1 pps edge positions, as global SCAN indices
    seg_00000_ai0_20260811T101500Z.f32     one file per channel per segment

A scan index counts sample clock ticks, not conversions, so it means the same
thing however many channels are enabled - which is why the edge table is
shared by all of them and lives in one file.
"""

import json
from pathlib import Path

import numpy as np


# =============== Time to atomic seconds ==========================

def clean_edges(edges, scans_per_second, tol=0.05):
    """Drop latched counter values that cannot be atomic seconds.

    Returns (kept, dropped). An atomic second is one second: the gap between
    consecutive edges must be a whole number of seconds' worth of scans. A gap
    of 2 or 3 seconds is fine - that is a MISSED edge, and times_from_edges
    handles it, because edge k is still k atomic seconds from edge 0 only if
    no edge was missed... which is why a missed edge must be kept as a gap and
    never silently closed up. What is rejected is a gap that is not near ANY
    whole second.

    In practice there is one such value per acquisition, and it is always the
    last. The counter latches the AI scan count on each 1 pps edge; when the
    AI task stops, the sample clock stops and the count freezes, and one more
    value comes back carrying that frozen count. It is not an atomic second -
    it is where the acquisition ended, typically a few tens of milliseconds
    after the last real edge. Left in, it drags the mean rate down brutally:
    on a 60 s record a 16 ms straggler turned +14 ppm into +16,403 ppm.

    tol=0.05 accepts anything within 5% of a whole second. That is 50,000 ppm
    - enormous next to the +/-50 ppm a board like this can drift, and far
    tighter than the artefact it rejects.
    """
    edges = np.asarray(edges, dtype=np.int64)
    if len(edges) < 2 or not scans_per_second:
        return edges, np.zeros(0, dtype=np.int64)

    keep, dropped = [0], []
    for i in range(1, len(edges)):
        seconds = (edges[i] - edges[keep[-1]]) / scans_per_second
        if seconds < 0.5 or abs(seconds - round(seconds)) > tol:
            dropped.append(i)
        else:
            keep.append(i)
    return edges[keep], edges[dropped]


def _atomic_seconds(edges, scans_per_second=None):
    """How many atomic seconds after the first edge each edge is.

    NOT simply arange(len(edges)). That assumes edge k is k seconds after
    edge 0, which is false the moment ONE edge is missed: the following edges
    would each be numbered one second too early, silently compressing the time
    axis by a second and every later measurement with it.

    Instead each gap is rounded to a whole number of seconds - which it must
    be, the pulses being one atomic second apart - so a missed edge shows up
    as a step of 2 and the numbering stays true. With nothing missed this is
    identical to arange.

    scans_per_second defaults to the MEDIAN gap, which is robust: it is the
    right answer as long as fewer than half the edges are missing.

    LIMIT: this cannot see seconds that passed while the AI clock was stopped
    (a long_run gap). The counter counts sample clock ticks, and there are
    none during a gap, so every edge in that window latches the same frozen
    count. clean_edges discards those, but the atomic seconds they represent
    are simply not recoverable from the counts - which is why gaps go in the
    manifest, and why two segments either side of one are not continuous.
    """
    edges = np.asarray(edges, dtype=np.float64)
    gaps = np.diff(edges)
    if scans_per_second is None:
        scans_per_second = np.median(gaps)
    steps = np.rint(gaps / scans_per_second)
    steps[steps < 1] = 1.0
    return np.concatenate([[0.0], np.cumsum(steps)])


def times_from_edges(sample_idx, edges, scans_per_second=None):
    """Seconds since the first 1 pps edge, for global scan indices.

    Edge k is exactly k atomic seconds after edge 0, so this interpolates
    between the surrounding edges rather than assuming a sample rate - which
    is what absorbs oscillator drift over a multi-day run (uncorrected, ~4 s
    per day). Samples outside the edge range are extrapolated with the nearest
    interval, so a sample before the first edge comes out negative.

    DIFFERS FROM v1's live body, which did `edges - edges[0]` and then
    np.interp against the raw sample indices. That put t=0 at sample 0 rather
    than at the first edge, and np.interp clamps instead of extrapolating, so
    everything before the first edge collapsed to 0. This is the version that
    was commented out below it, and it matches what the docstrings claim.

    Pass scans_per_second (daq.ai.actual_rate(), or the nominal_rate in the
    manifest) to run clean_edges first. edges.i64 is written raw and lossless,
    so the end-of-acquisition artefact is in the file and has to be filtered
    here rather than at write time.
    """
    if scans_per_second:
        edges, _ = clean_edges(edges, scans_per_second)

    edges = np.asarray(edges, dtype=np.float64)
    if len(edges) < 2:
        raise ValueError("need at least two 1 pps edges to build a time axis")

    idx = np.asarray(sample_idx, dtype=np.float64)
    atom_sec = _atomic_seconds(edges, scans_per_second)
    t = np.interp(idx, edges, atom_sec)      # exact between edges, clamped outside

    before, after = idx < edges[0], idx > edges[-1]
    if before.any():
        t[before] = (idx[before] - edges[0]) / (edges[1] - edges[0])
    if after.any():
        t[after] = (len(edges) - 1) + \
                   (idx[after] - edges[-1]) / (edges[-1] - edges[-2])
    return t

# ======================================================= long-run read-back
def load_long_run(outdir):
    """(manifest, edges) for a directory written by long_run()."""
    outdir = Path(outdir)
    lines = (outdir / "manifest.jsonl").read_text().splitlines()
    manifest = [json.loads(x) for x in lines if x.strip()]
    edges = np.fromfile(outdir / "edges.i64", dtype=np.int64)
    return manifest, edges


def channels_of(outdir):
    """Channel names recorded in a long_run directory, in task order."""
    manifest, _ = load_long_run(outdir)
    for entry in manifest:
        if "channels" in entry:
            return list(entry["channels"])
    return []


def load_segment(outdir, index, channel=None, mmap=False):
    """(global scan indices, signal) for one channel of one segment.

    channel=None picks the first channel of the run, which is the only one
    that exists for a single-channel recording.
    """
    outdir = Path(outdir)
    manifest, _ = load_long_run(outdir)
    if channel is None:
        names = channels_of(outdir)
        channel = names[0] if names else None

    entry = next(e for e in manifest
                 if e.get("index") == index and "file" in e
                 and (channel is None or e.get("channel") == channel))
    path = outdir / entry["file"]
    # mmap=True leaves the samples on disk and pages in only what you touch,
    # so an 8 GB segment plots without 8 GB of RAM. Slice it like an array.
    y = (np.memmap(path, dtype=np.float32, mode="r") if mmap
         else np.fromfile(path, dtype=np.float32))
    i0 = entry["start_sample"]
    return np.arange(i0, i0 + len(y), dtype=np.int64), y


# ========================================================= HDF5 archiving
def segment_to_hdf5(f32_path, start_sample=0, rate=None, channel=None,
                    delete_raw=False, compression="gzip", compression_opts=4):
    """Convert one raw .f32 segment to a self-describing .h5.

    Raw is what gets written during acquisition - it is the fastest format, it
    truncates cleanly if the run dies, and its tail can be read while it is
    still being written. HDF5 is the better archive: metadata travels with the
    data and reads are chunked. Converting only CLOSED segments gets both.
    """
    import h5py

    f32_path = Path(f32_path)
    y = np.fromfile(f32_path, dtype=np.float32)
    h5_path = f32_path.with_suffix(".h5")
    with h5py.File(h5_path, "w") as f:
        d = f.create_dataset("signal", data=y, chunks=True,
                             compression=compression,
                             compression_opts=compression_opts)
        d.attrs["units"] = "V"
        d.attrs["start_sample"] = int(start_sample)
        d.attrs["n"] = int(len(y))
        if rate:
            d.attrs["nominal_rate"] = float(rate)
        if channel:
            d.attrs["channel"] = str(channel)
        d.attrs["source"] = f32_path.name
    if delete_raw:
        f32_path.unlink()
    return h5_path


def to_hdf5(outdir, delete_raw=False):
    """Convert every finished segment in a long_run directory. Run it after
    the fact, or in a thread while the run continues."""
    manifest, _ = load_long_run(outdir)
    return [segment_to_hdf5(Path(outdir) / e["file"], e["start_sample"],
                            e.get("nominal_rate"), e.get("channel"),
                            delete_raw=delete_raw)
            for e in manifest if "file" in e]


def _hdf5_worker(q, verbose=True):
    """Background converter: drains closed segments, writes .h5, never blocks
    the acquisition. A failure here is logged, not raised - losing the archive
    copy must not stop a week-long run."""
    while True:
        item = q.get()
        if item is None:
            return
        path, start_sample, rate, channel = item
        try:
            out = segment_to_hdf5(path, start_sample, rate, channel)
            if verbose:
                print(f"  -> {out.name} ({out.stat().st_size / 1e6:,.0f} MB)")
        except ImportError:
            if verbose:
                print("  h5py not installed - keeping raw .f32 only")
            return
        except Exception as e:
            if verbose:
                print(f"  HDF5 conversion failed for {path.name}: {e}")
