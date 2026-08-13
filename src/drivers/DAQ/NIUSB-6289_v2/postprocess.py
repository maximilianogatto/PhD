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

RAW ON DISK, INTERPRETED ON READ. Nothing written by long_run has been
filtered, corrected or cleaned. The .f32 is what DAQmx returned; edges.i64
holds every value the counter produced, including the duplicate it emits at
task start and the frozen count it emits when the AI clock stops. A recording
that cannot be re-taken must not be stored already interpreted - if the
interpretation turns out to be wrong, and it did, the raw file is what lets
you fix it after the fact rather than repeat a week of measurement.

So the artefacts live in the file and are removed HERE, by default:
times_from_edges cleans unless you pass clean=False, and edge_report says
what was discarded. That is the right way round - a mistake in this file is
one you can correct tomorrow.
"""

import json
from pathlib import Path

import numpy as np


# =============== Time to atomic seconds ==========================

def clean_edges(edges, scans_per_second=None, tol=0.05):
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
    if len(edges) < 3:
        return edges, np.zeros(0, dtype=np.int64)

    if not scans_per_second:
        # The MEDIAN gap is one second's worth of scans, and it stays that
        # even with artefacts at both ends - which is the point. Defaulting to
        # this rather than skipping the filter matters: an earlier version
        # returned the table unfiltered when the rate could not be read, so a
        # failure to look up the rate turned silently into no cleaning at all.
        scans_per_second = float(np.median(np.diff(edges)))
        if scans_per_second <= 0:
            return edges, np.zeros(0, dtype=np.int64)

    def sweep(anchor):
        """Greedy pass keeping edges a whole number of seconds apart."""
        keep = [anchor]
        for i in range(anchor + 1, len(edges)):
            seconds = (edges[i] - edges[keep[-1]]) / scans_per_second
            if seconds >= 0.5 and abs(seconds - round(seconds)) <= tol:
                keep.append(i)
        return keep

    # Anchoring on edge 0 assumes edge 0 is real. It usually is - but the
    # counter can emit an initial value at task start, before the AI clock has
    # produced a single scan, and then the FIRST edge is the artefact. Sweeping
    # from both candidates and keeping the better result costs one extra pass
    # and removes the assumption. (An acquisition triggered off the same line
    # as the 1 pps produces exactly this: two values at scan 0.)
    best = max((sweep(0), sweep(1)), key=len)
    kept = set(best)
    dropped = [i for i in range(len(edges)) if i not in kept]
    return edges[best], edges[dropped]


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
    if np.any(gaps <= 0):
        # Two latched values at the same scan cannot both be atomic seconds,
        # and numbering them 0 and 1 shifts the whole axis by a second - which
        # is exactly what a duplicate at scan 0 did before clean_edges ran.
        # Raise rather than guess: np.interp with repeated x is ambiguous too.
        raise ValueError(
            f"edge table is not strictly increasing ({int((gaps <= 0).sum())} "
            f"repeated or decreasing value(s)) - run clean_edges first")
    if scans_per_second is None:
        scans_per_second = np.median(gaps)
    steps = np.rint(gaps / scans_per_second)
    steps[steps < 1] = 1.0
    return np.concatenate([[0.0], np.cumsum(steps)])


def times_from_edges(sample_idx, edges, scans_per_second=None, clean=True):
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

    RAW ON DISK, FILTERED ON READ. edges.i64 holds every value the counter
    ever produced - the duplicate at task start, the straggler at the end, all
    of it - because a recording you cannot re-take must never be stored
    already interpreted. The filtering therefore has to happen here, and it is
    the DEFAULT rather than something you remember to ask for: an earlier
    version only cleaned when scans_per_second was passed, and forgetting it
    silently shifted a 300 s record to 1.000 .. 301.427 s.

    clean=False returns the unfiltered interpretation, for when you are
    investigating the edge table itself rather than using it. scans_per_second
    (daq.ai.actual_rate(), or nominal_rate from the manifest) makes the filter
    exact; without it the median gap is used, which is the same answer unless
    more than half the edges are artefacts.
    """
    if clean:
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

def edge_report(edges, nominal_rate=None, verbose=True):
    """Is the 1 pps table of this run sound? Returns a dict, prints a summary.

    Run it before trusting a time axis. It says what was discarded and why,
    how many atomic seconds were MISSED (a gap of 2 s or more, which is real
    and kept), and what the board's clock did against the rubidium.

        manifest, edges = load_long_run(outdir)
        edge_report(edges, manifest[0]["nominal_rate"])
    """
    edges = np.asarray(edges, dtype=np.int64)
    kept, dropped = clean_edges(edges)
    report = {"n_raw": len(edges), "n_kept": len(kept),
              "n_dropped": len(dropped), "dropped": dropped,
              "measured_rate": None, "ppm": None, "seconds_per_day": None,
              "atomic_seconds": None, "n_missed": 0}

    if len(kept) >= 2:
        steps = _atomic_seconds(kept)
        report["atomic_seconds"] = float(steps[-1])
        report["n_missed"] = int((np.diff(steps) > 1).sum())
        # Scans per atomic second across the WHOLE run, not the mean of the
        # gaps: the endpoints are each quantised to one scan, so spanning the
        # run divides that error by the number of seconds in it.
        report["measured_rate"] = float((kept[-1] - kept[0]) / steps[-1])
        if nominal_rate:
            ppm = (nominal_rate - report["measured_rate"]) \
                / report["measured_rate"] * 1e6
            report["ppm"] = ppm
            report["seconds_per_day"] = abs(ppm) * 0.0864

    if verbose:
        print(f"1 pps: {report['n_kept']:,} usable of {report['n_raw']:,} "
              f"latched values")
        if report["n_dropped"]:
            shown = dropped[:6].tolist()
            print(f"  discarded {report['n_dropped']}: {shown}"
                  f"{' ...' if report['n_dropped'] > 6 else ''}")
            print(f"  (not whole atomic seconds - normally the counter's "
                  f"start value and the end-of-run one)")
        if report["atomic_seconds"] is not None:
            print(f"  spans {report['atomic_seconds']:,.0f} atomic seconds")
            if report["n_missed"]:
                print(f"  {report['n_missed']} MISSED edge(s) - kept as gaps, "
                      f"the numbering accounts for them")
            print(f"  true rate {report['measured_rate']:,.3f} scans/atomic s")
            if report["ppm"] is not None:
                print(f"  clock error {report['ppm']:+.2f} ppm "
                      f"-> {report['seconds_per_day']:.2f} s/day")
    return report


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

    entry = next(e for e in manifest if e.get("index") == index and "file" in e and (channel is None or e.get("channel") == channel))
    path = outdir / entry["file"]
    # mmap=True leaves the samples on disk and pages in only what you touch,
    # so an 8 GB segment plots without 8 GB of RAM. Slice it like an array.
    y = (np.memmap(path, dtype=np.float32, mode="r") if mmap else np.fromfile(path, dtype=np.float32))
    i0 = entry["start_sample"]
    return np.arange(i0, i0 + len(y), dtype=np.int64), y


def iter_segments(outdir, channel=None, mmap=False):
    """Yield (scan indices, samples) for each segment of one channel, in order.

    The way to walk a multi-segment run without holding it all at once:

        for idx, y in iter_segments(outdir, "ai0"):
            ...                       # one hour at a time

    Scan indices are global and continuous across segments, so the pieces line
    up end to end - unless the manifest records a GAP between them, in which
    case they are adjacent in index but not in time.
    """
    outdir = Path(outdir)
    manifest, _ = load_long_run(outdir)
    if channel is None:
        names = channels_of(outdir)
        channel = names[0] if names else None

    entries = sorted((e for e in manifest if "file" in e
                      and (channel is None or e.get("channel") == channel)),
                     key=lambda e: e["index"])
    for entry in entries:
        path = outdir / entry["file"]
        y = (np.memmap(path, dtype=np.float32, mode="r") if mmap
             else np.fromfile(path, dtype=np.float32))
        i0 = entry["start_sample"]
        yield np.arange(i0, i0 + len(y), dtype=np.int64), y


def load_run(outdir, channel=None):
    """(scan indices, samples) for a WHOLE run of one channel, concatenated.

    Convenient, and it costs RAM: 4 bytes per scan per channel, so an hour at
    25 kS/s is 360 MB and a day is 8.6 GB. Check before you call it -

        sum(e["n"] for e in load_long_run(outdir)[0] if "file" in e)

    - or use iter_segments to work a segment at a time, or livescope.RunFiles
    to read just the window you want to look at.
    """
    parts = list(iter_segments(outdir, channel))
    if not parts:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.float32)
    return (np.concatenate([i for i, _ in parts]),
            np.concatenate([y for _, y in parts]))


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
