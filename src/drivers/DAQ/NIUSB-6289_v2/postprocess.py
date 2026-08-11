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

def times_from_edges(sample_idx, edges):
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
    """
    edges = np.asarray(edges, dtype=np.float64)
    if len(edges) < 2:
        raise ValueError("need at least two 1 pps edges to build a time axis")

    idx = np.asarray(sample_idx, dtype=np.float64)
    atom_sec = np.arange(len(edges), dtype=np.float64)
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
