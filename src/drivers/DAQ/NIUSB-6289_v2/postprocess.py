"""
Read-back and analysis for records written by the USB-6289 driver.

Free functions only - nothing here touches the hardware or needs an
instrument, so a recording can be analysed on a machine that has never seen
the DAQ. Split out of the driver for exactly that reason.

The on-disk layout written by long_run() is multichannel from v2 on:

    run.json          describe() at the start of the run
    manifest.jsonl    one line per file, plus a line for every gap
    edges.i64         1 pps edge positions, as global SCAN indices
    markers.i64       event positions, same units; absent if no markers
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

THE TIME AXIS HAS TWO INDEPENDENT PARTS, and confusing them is the mistake
this module exists to prevent:

    the SCALE comes from the rubidium. Consecutive 1 pps edges are one atomic
    second apart by definition, so the edge table IS the ruler and no sample
    rate is assumed anywhere. This is what absorbs the oscillator drift.

    the ZERO is a convention, chosen by times_from_edges(origin=...). It
    defaults to scan 0 - the first sample, i.e. the trigger on a triggered
    run - because that is the one point in the record with a physical meaning
    to the person who started it. The first 1 pps EDGE has no such meaning:
    on a triggered run it falls wherever the phase between the rubidium and
    the trigger puts it.
"""

import json
from pathlib import Path

import numpy as np

from _constants import (ADC_BITS, ANCHOR_CANDIDATES,      # noqa: E402
                        DATATYPE_OF_SUFFIX, DTYPE_OF, INDEX_DTYPE,
                        UNITS_OF)


# =============== Time to atomic seconds ==========================

def clean_edges(edges, scans_per_second=None, tol=0.002):
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

    tol=0.002 accepts a gap within 0.2% of a whole second - 2,000 ppm, still
    forty times looser than the +/-50 ppm this board is specified to drift, and
    tight enough to matter: at the old 5% a trigger that fired within 50 ms of
    an atomic second made the counter's leading zero look like a valid second
    too, and the axis came out shifted by that offset.

    WHAT REMAINS AMBIGUOUS, and it cannot be fixed here. If the trigger fires
    within `tol` of a second boundary, the leading zero is a whole second from
    the first real edge - to within tol - and no spacing test can tell it from
    a genuine atomic second, because it very nearly is one. The sweep's misfit
    tie-break usually still picks the real edge; when it does not, the zero is
    kept as edge 0 and the first real tick is labelled +1.000 s when it is
    really +0.998. Every LATER interval stays exact - durations, event
    spacings and the drift slope are untouched - and the error is bounded by
    tol, i.e. at most 2 ms on the placement of the axis relative to the
    trigger. The window in which it can happen is 2 ms wide, which is what tol
    buys.
    """
    edges = np.asarray(edges, dtype=np.int64)
    if len(edges) < 2:
        return edges, np.zeros(0, dtype=np.int64)

    # Two edges are filtered like any other number, NOT waved through. The
    # guard used to be len < 3, and a two-value table is exactly what a short
    # triggered acquire returns: one real edge, then the frozen end-of-run
    # count a few tens of ms later. Unfiltered, _atomic_seconds is then told
    # those 0.17 s ARE one atomic second and the axis comes out six times too
    # fast. With the filter the straggler is dropped, one edge is left, and
    # times_from_edges says so instead of inventing a scale.
    if not scans_per_second:
        # The MEDIAN gap is one second's worth of scans, and it stays that
        # even with artefacts at both ends - which is the point. Defaulting to
        # this rather than skipping the filter matters: an earlier version
        # returned the table unfiltered when the rate could not be read, so a
        # failure to look up the rate turned silently into no cleaning at all.
        scans_per_second = float(np.median(np.diff(edges)))
        if scans_per_second <= 0:
            return edges, np.zeros(0, dtype=np.int64)

    # Collapse runs of IDENTICAL values to their first occurrence. Repeats
    # cannot all be atomic seconds - they are what the counter emits while the
    # AI clock is stopped, and the clock is stopped in two ordinary
    # situations: waiting for a start trigger, and a long_run gap. Doing this
    # first is what makes the anchor search below work, because otherwise
    # every candidate anchor is the same frozen value.
    distinct = np.flatnonzero(np.concatenate([[True], np.diff(edges) != 0]))

    def sweep(start):
        """Greedy pass keeping edges a whole number of seconds apart.

        Returns the kept indices AND how badly they fit. Two anchors can keep
        the same number of edges - a leading zero and the first real edge do,
        when the trigger fired close to a second boundary - and then the count
        cannot choose between them. The fit can: anchored on the real edge
        every gap is a whole second to within the board's drift, while
        anchored on the zero every gap is off by the trigger's offset.
        """
        keep = [distinct[start]]
        misfit = 0.0
        for i in distinct[start + 1:]:
            seconds = (edges[i] - edges[keep[-1]]) / scans_per_second
            error = abs(seconds - round(seconds))
            if seconds >= 0.5 and error <= tol:
                keep.append(i)
                misfit += error
        return keep, misfit

    # Anchoring on the first value assumes it is real. Often it is not: with a
    # start trigger the counter runs while the task waits, so EVERY edge before
    # the trigger latches 0 and the first real one sits at an arbitrary
    # fraction of a second. Anchored on a zero, every genuine edge looks wrong
    # and the whole table is discarded - which is exactly what happened before
    # this loop existed. Trying the first few distinct values and keeping the
    # longest result costs a handful of passes and removes the assumption.
    best, _ = max((sweep(a)
                   for a in range(min(ANCHOR_CANDIDATES, len(distinct)))),
                  key=lambda result: (len(result[0]), -result[1]))
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
    if np.any(steps < 1):
        # A gap shorter than half a second is not an atomic second, so there
        # is no honest number to put here. This used to read steps[steps<1]=1,
        # which turned the impossible into a confident lie: a 0.17 s straggler
        # was labelled one second and every time after it came out ~6x wrong,
        # with nothing in the output to show it. clean_edges removes these -
        # reaching this line means it was skipped or overruled.
        bad = int((steps < 1).sum())
        raise ValueError(
            f"{bad} gap(s) shorter than half an atomic second "
            f"({gaps[steps < 1].min():.0f} scans at {scans_per_second:,.0f} "
            f"scans/s) - these are not 1 pps edges. Run clean_edges first, or "
            f"pass the correct scans_per_second")
    return np.concatenate([[0.0], np.cumsum(steps)])


def _edge_interp(idx, edges, atom_sec):
    """Scan indices -> seconds, measured from edges[0], extrapolating outside.

    np.interp CLAMPS beyond its range, which would flatten every sample before
    the first edge onto t=0 and every one after the last onto t=end. Outside
    the table the nearest interval's slope is continued instead.

    That slope is scans per ATOMIC second, (edges - edges) / (atom_sec -
    atom_sec), not simply the gap in scans: if the second edge of the run was
    missed, the first gap spans two seconds and using it raw would run the
    leading extrapolation at half speed. Same at the tail, where the old code
    also wrote `len(edges) - 1` for the last edge's time - true only when no
    edge was ever missed, and off by a whole second per missed edge otherwise,
    with a visible step at the last edge.
    """
    t = np.interp(idx, edges, atom_sec)

    before, after = idx < edges[0], idx > edges[-1]
    if before.any():
        lead = (edges[1] - edges[0]) / (atom_sec[1] - atom_sec[0])
        t[before] = (idx[before] - edges[0]) / lead
    if after.any():
        tail = (edges[-1] - edges[-2]) / (atom_sec[-1] - atom_sec[-2])
        t[after] = atom_sec[-1] + (idx[after] - edges[-1]) / tail
    return t


def times_from_edges(sample_idx, edges, scans_per_second=None, clean=True,
                     origin="start"):
    """Atomic seconds for global scan indices, measured from `origin`.

    Edge k is exactly k atomic seconds after edge 0, so this interpolates
    between the surrounding edges rather than assuming a sample rate - which
    is what absorbs oscillator drift over a multi-day run (uncorrected, ~4 s
    per day). The rubidium sets the SCALE of the axis. `origin` sets where its
    zero sits, and the two are independent:

        "start"   t = 0 at scan 0, the first sample of the record. On a
                  triggered run that is the instant the trigger fired, so this
                  axis is the board's own time_axis() with the drift taken
                  out, and the two are directly comparable. THE DEFAULT.
        "edge"    t = 0 at the first 1 pps edge kept. Use when you care where
                  the atomic seconds themselves fall - the trigger then lands
                  at a negative time, which is what it is: before the tick.
        <int>     t = 0 at that scan index, e.g. origin=marks[2] to measure
                  everything from a marker. ACCURATE ONLY NEAR THE FIRST
                  EDGE - see below.

    WHY THE DEFAULT CHANGED. It was "edge", and on a real run that put t=0 at
    an arbitrary point 0.83 s into the record - the first rubidium tick to
    arrive AFTER the trigger, whose position is set by nothing but the phase
    between two unrelated clocks. Every interval was right and the drift slope
    was right, but t_atomic - t_board carried a constant -827,920 us offset,
    which on a plot scaled to the few us of real drift is a flat line off the
    bottom of the axes. Anchoring on scan 0 costs one subtraction, needs no
    extra hardware, and makes the number on the axis mean what a reader
    assumes it means: seconds since the acquisition began.

    HOW FAR `origin` MAY SIT FROM THE FIRST EDGE. The zero is chosen by
    sliding the edge table, which is exact only if the board's rate is the
    same at both ends of the slide - and it is not, that being why this
    function exists. A slid lookup reads each sample against a part of the
    ruler recorded at a different time, so the error is

        (size of the slide) x (drift between the two windows)

    "start" and "edge" slide by less than a second: sub-microsecond, always,
    and the two differ by a constant to 33 us on a 300 s record. A marker
    hours into a long run is different - measured at 27 ms on an 8 h run
    drifting 8 to 20 ppm. If you need a marker-relative axis that good,
    subtract instead of sliding:

        t = times_from_edges(idx, edges, rate)      # both on the same axis
        t_rel = t - times_from_edges([marks[2]], edges, rate)[0]

    which is exact because a constant offset cannot distort anything.

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
        raise ValueError(
            f"need at least two 1 pps edges to build a time axis, have "
            f"{len(edges)}. A short triggered record can contain one real "
            f"edge and nothing else; acquire for longer, or use "
            f"ai.time_axis() and accept the board's own clock")

    idx = np.asarray(sample_idx, dtype=np.float64)
    atom_sec = _atomic_seconds(edges, scans_per_second)

    # CHOOSING THE ZERO IS SLIDING THE KNOTS. atom_sec[0] is 0, so t=0 sits
    # whereever the first knot is - which starts out as edges[0]. Moving the
    # whole table by `shift` therefore moves t=0 by `shift`, and the gaps
    # between knots are untouched, so the rubidium still sets the scale. All
    # three cases are just "how far from edges[0] do I want t=0 to be".
    if origin == "edge":
        shift = 0.0                          # already there: leave it alone
    elif origin == "start":
        shift = -edges[0]                    # scan 0, the trigger
    else:
        shift = float(origin) - edges[0]     # any scan, e.g. origin=marks[2]

    return _edge_interp(idx, edges + shift, atom_sec)

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
    edges = np.fromfile(outdir / "edges.i64", dtype=INDEX_DTYPE)
    return manifest, edges


def channels_of(outdir, manifest=None):
    """Channel names recorded in a long_run directory, in task order."""
    if manifest is None:
        manifest, _ = load_long_run(outdir)
    for entry in manifest:
        if "channels" in entry:
            return list(entry["channels"])
    return []


def trigger_time(outdir):
    """When the acquisition actually began, and where the atomic seconds sit.

    A triggered run does not start when you call long_run - it starts when the
    edge arrives, possibly seconds later, and the sample clock does not tick
    until then. So the manifest carries two different wall times:

        "start"       when the run was configured
        "first_scan"  when the first chunk came back; the trigger fired about
                      one chunk earlier

    Returns a dict with the estimated trigger wall time and, if the edge table
    is available, the offset from scan 0 to the FIRST atomic second in the
    record - which is the number that ties the trigger to the rubidium:

        the trigger happened `first_edge_seconds` before that atomic second.

    That offset is a property of the RUN, not of the time axis: it is the
    phase between the trigger and the rubidium, and it is arbitrary. It used
    to leak into the axis as well, because times_from_edges zeroed on the
    first edge; it now zeroes on scan 0, so `first_edge_seconds` is simply
    where the first tick shows up on that axis.

    Note what is NOT recoverable: the atomic seconds that passed while the task
    was armed and waiting. The counter counts the sample clock, and the clock
    was not running, so every edge in that window latched 0 and clean_edges
    discards them. Nothing is lost that was ever measured - the board simply
    was not counting yet.
    """
    manifest, edges = load_long_run(outdir)
    header = next((e for e in manifest if e.get("event") == "start"), {})
    first = next((e for e in manifest if e.get("event") == "first_scan"), None)
    rate = header.get("nominal_rate")

    out = {"configured_wall": header.get("wall"),
           "trigger": (first or {}).get("trigger"),
           "trigger_wall": None, "first_edge_scan": None,
           "first_edge_seconds": None}

    if first is not None and rate:
        out["trigger_wall"] = first["wall"] - first["chunk_scans"] / rate

    kept, _ = clean_edges(edges, rate)
    if len(kept):
        out["first_edge_scan"] = int(kept[0])
        out["first_edge_seconds"] = float(kept[0] / rate) if rate else None
    return out


#: what each on-disk format is called, and what numpy reads it as



def run_header(manifest):
    """The manifest's `start` row - channels, rate, datatype, scaling."""
    return next((e for e in manifest if e.get("event") == "start"), {})


def _stored_dtype(entry, header):
    """What is IN the file. Three sources, most trustworthy first.

    The header is written by the run that produced the files, so it wins. The
    suffix is a fallback for a manifest from before the format was recorded,
    and float32 is the last resort because that is all there was.
    """
    name = header.get("datatype")
    if name is None:
        name = DATATYPE_OF_SUFFIX.get(Path(entry["file"]).suffix, "float32")
    return DTYPE_OF[name]


def _as_units(y, outdir, channel, units, header=None):
    """Convert between raw converter codes and volts, either direction.

    ONE MAP, BOTH WAYS, and it is the standard offset-binary one:

        volts = v_min + code * (v_max - v_min) / (2**bits - 1)

    v_min, v_max and bits come from the manifest, read back from the hardware
    by the run that wrote the files - not from what was requested, because
    DAQmx snaps a requested range to a hardware one and the codes belong to
    the snapped range.

    codes -> volts is what a .u32 run needs and is exact. volts -> codes is
    only for reading an older .f32 run as codes; it re-quantises numbers that
    were already rounded to float32, so it is good to about an LSB.
    """
    if units not in ("volts", "codes"):
        raise ValueError(f"units must be 'volts' or 'codes', not {units!r}")

    stored_int = np.issubdtype(y.dtype, np.integer)
    if stored_int == (units == "codes"):
        return y                                # already what was asked for

    header = header or {}
    scaling = (header.get("scaling") or {}).get(channel, {})

    if stored_int:                              # codes -> volts
        # OFFSET BINARY over the converter's full scale: code 0 is v_min and
        # 2**bits - 1 is v_max. Three numbers from the manifest and nothing
        # else, which is the same arithmetic the collaborating group applies
        # at their end - so their volts and ours agree by construction.
        v_min = scaling.get("v_min_actual")
        v_max = scaling.get("v_max_actual")
        if v_min is None or v_max is None:
            raise ValueError(
                f"{channel}: this run holds raw codes but the manifest does "
                f"not record the voltage range they span, so they cannot be "
                f"turned into volts. Read it with units='codes'.")
        span = (1 << int(scaling.get("bits", ADC_BITS))) - 1
        # float64 throughout, then one narrowing at the end, so the result is
        # the correctly-rounded float32 rather than one that accumulated two
        # float32 roundings on the way. Doing the arithmetic in float32 would
        # save one segment's worth of temporary and shift the last bit of some
        # samples - not worth it now that load_run streams a segment at a time.
        return (v_min + np.asarray(y, dtype=np.float64)
                * (v_max - v_min) / span).astype(np.float32)

    # volts -> codes. Prefer the calibrated range the hardware reported over
    # the one that was requested - DAQmx snaps the request to a hardware
    # range, and the codes belong to the snapped one.
    v_min = scaling.get("v_min_actual")
    v_max = scaling.get("v_max_actual")
    if v_min is None or v_max is None:
        from rawformat import channel_range
        v_min, v_max = channel_range(outdir, channel)
    from rawformat import volts_to_codes
    codes, n_clipped = volts_to_codes(y, v_min, v_max,
                                      int(scaling.get("bits", ADC_BITS)))
    if n_clipped:
        import warnings
        warnings.warn(f"{channel}: {n_clipped:,} sample(s) outside "
                      f"{v_min:+g}..{v_max:+g} V were clipped to the code "
                      f"range", stacklevel=2)
    return codes


def load_segment(outdir, index, channel=None, mmap=False, manifest=None,
                 units="volts"):
    """(global scan indices, signal) for one channel of one segment.

    channel=None picks the first channel of the run, which is the only one
    that exists for a single-channel recording.
    """
    outdir = Path(outdir)
    if manifest is None:
        manifest, _ = load_long_run(outdir)
    if channel is None:
        names = channels_of(outdir, manifest)
        channel = names[0] if names else None

    # ONE row of the manifest: the file holding segment `index` of `channel`.
    # next(..., None) rather than a bare next(): an unmatched bare next raises
    # StopIteration with an EMPTY message, and inside a generator it would end
    # the iteration silently instead of erroring at all.
    entry = next((e for e in manifest
                  if e.get("index") == index and "file" in e
                  and (channel is None or e.get("channel") == channel)), None)
    if entry is None:
        have = sorted({e["index"] for e in manifest if "file" in e})
        raise KeyError(
            f"no segment {index} for channel {channel!r} in {outdir}. "
            f"Segments present: {have}. Channels: {channels_of(outdir, manifest)}")
    path = outdir / entry["file"]
    # mmap=True leaves the samples on disk and pages in only what you touch,
    # so an 8 GB segment plots without 8 GB of RAM. Slice it like an array.
    header = run_header(manifest)
    dtype = _stored_dtype(entry, header)
    y = (np.memmap(path, dtype=dtype, mode="r") if mmap else np.fromfile(path, dtype=dtype))
    y = _as_units(y, outdir, entry["channel"], units, header)
    i0 = entry["start_sample"]
    return np.arange(i0, i0 + len(y), dtype=np.int64), y


def iter_segments(outdir, channel=None, mmap=False, manifest=None,
                  units="volts"):
    """Yield (scan indices, samples) for each segment of one channel, in order.

    The way to walk a multi-segment run without holding it all at once:

        for idx, y in iter_segments(outdir, "ai0"):
            ...                       # one hour at a time

    Scan indices are global and continuous across segments, so the pieces line
    up end to end - unless the manifest records a GAP between them, in which
    case they are adjacent in index but not in time.
    """
    outdir = Path(outdir)
    if manifest is None:
        manifest, _ = load_long_run(outdir)
    if channel is None:
        names = channels_of(outdir, manifest)
        channel = names[0] if names else None

    header = run_header(manifest)
    for entry in segment_entries(manifest, channel):
        path = outdir / entry["file"]
        dtype = _stored_dtype(entry, header)
        y = (np.memmap(path, dtype=dtype, mode="r") if mmap
             else np.fromfile(path, dtype=dtype))
        y = _as_units(y, outdir, entry["channel"], units, header)
        i0 = entry["start_sample"]
        yield np.arange(i0, i0 + len(y), dtype=np.int64), y


def segment_entries(manifest, channel=None):
    """The manifest rows for one channel's segments, in segment order."""
    return sorted((e for e in manifest if "file" in e and (channel is None or e.get("channel") == channel)), key=lambda e: e["index"])


def load_run(outdir, channel=None, mmap=False, manifest=None,
             units="volts"):
    """(scan indices, samples) for a WHOLE run of one channel, concatenated.

    Convenient, and it costs RAM: 4 bytes per scan per channel, so an hour at
    25 kS/s is 360 MB and a day is 8.6 GB. Check before you call it -

        sum(e["n"] for e in load_long_run(outdir)[0] if "file" in e)

    - or use iter_segments to work a segment at a time, or livescope.RunFiles
    to read just the window you want to look at.
    """
    outdir = Path(outdir)
    if manifest is None:
        manifest, _ = load_long_run(outdir)
    if channel is None:
        names = channels_of(outdir, manifest)
        channel = names[0] if names else None

    entries = segment_entries(manifest, channel)
    if not entries:
        return (np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.uint32 if units == "codes" else np.float32))

    # FILL ONE BUFFER, do not build a list and concatenate it. The manifest
    # already says how long the run is, so the answer can be allocated once
    # and each segment copied into its slice and released. Listing the parts
    # first holds the WHOLE run twice: once in the parts and once in the
    # concatenated result.
    total = sum(e["n"] for e in entries)
    segments = iter_segments(outdir, channel, mmap, manifest, units)
    first_idx, first_y = next(segments)

    out = np.empty(total, dtype=first_y.dtype)
    out[:len(first_y)] = first_y
    pos = len(first_y)
    for _, y in segments:
        if pos + len(y) > total:
            raise ValueError(
                f"segment files hold more than the {total:,} scans the "
                f"manifest of {outdir} accounts for - the manifest and the "
                f"files disagree")
        out[pos:pos + len(y)] = y
        pos += len(y)

    # AND THE INDEX IS AN ARANGE. The writer numbers scans straight through a
    # run - a gap breaks continuity in TIME, not in scan index - so the whole
    # index is one arange, and building it that way avoids concatenating N
    # arrays of int64 that cost twice what the samples do. Verified against
    # the manifest rather than assumed, with the general path kept for a run
    # where it does not hold.
    starts = [e["start_sample"] for e in entries]
    contiguous = all(starts[k + 1] == starts[k] + entries[k]["n"] for k in range(len(entries) - 1))
    if contiguous:
        idx = np.arange(starts[0], starts[0] + pos, dtype=np.int64)
    else:
        idx = np.concatenate([np.arange(e["start_sample"],
                                        e["start_sample"] + e["n"],
                                        dtype=np.int64) for e in entries])
    return idx, out[:pos]


def load_markers(outdir):
    """Marker scan indices for a run, or an empty array if there are none.

    Raw, like everything else on disk. Unlike the 1 pps there is nothing to
    clean: events are irregular, so no interval is implausible and any filter
    would be guessing. Repeated values are real information - two pulses
    within one scan period, or pulses that arrived while the AI clock was
    stopped.
    """
    path = Path(outdir) / "markers.i64"
    if not path.exists():
        return np.zeros(0, dtype=np.int64)
    return np.fromfile(path, dtype=INDEX_DTYPE)


def marker_times(outdir):
    """(scan indices, atomic seconds) for every marker of a run.

    The whole point of recording the scan index rather than a time: this is
    one call, and it uses the same edge table that times the samples, so a
    marker and the data around it cannot disagree.

        marks, t = marker_times(outdir)
        idx, y = load_run(outdir, "ai0")
        y[marks - idx[0]]              # the samples AT the markers
        np.diff(t)                     # how long the OPX spent between them
    """
    outdir = Path(outdir)
    manifest, edges = load_long_run(outdir)
    marks = load_markers(outdir)
    if not len(marks) or not len(edges):
        return marks, np.zeros(0, dtype=np.float64)

    header = next((e for e in manifest if e.get("event") == "start"), {})
    rate = header.get("nominal_rate")
    return marks, times_from_edges(marks, edges, scans_per_second=rate)


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
    y = np.fromfile(f32_path, dtype=DTYPE_OF[
        DATATYPE_OF_SUFFIX.get(f32_path.suffix, "float32")])
    h5_path = f32_path.with_suffix(".h5")
    with h5py.File(h5_path, "w") as f:
        d = f.create_dataset("signal", data=y, chunks=True,
                             compression=compression,
                             compression_opts=compression_opts)
        d.attrs["units"] = UNITS_OF["uint32" if np.issubdtype(y.dtype, np.integer)
                                   else "float32"]
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
