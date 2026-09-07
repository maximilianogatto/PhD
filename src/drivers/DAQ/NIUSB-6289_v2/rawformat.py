"""ADC codes on one side, volts on the other, and the map between them.

WHY THIS EXISTS. Another group stores bolometer data as little-endian uint32
ADC codes; this driver records float32 volts. Both describe the same
measurement, and the only thing standing between them is an affine map. Put
that map in a sidecar next to the data and either side can produce the other,
exactly.

RAW CODES PLUS THE MAP ARE A SUPERSET OF VOLTS. codes -> volts is the map;
volts -> codes is its inverse, and the inverse is exact only because a scaled
reading already sits on the code grid it came from. That asymmetry is the
whole argument for which one to keep: storing both doubles the disk and adds
nothing, because either can be produced from the other as long as the map
travels with it.

THE MAP THIS MODULE USES is the ideal one:

    lsb   = (v_max - v_min) / (2**bits - 1)
    code  = round((volts - v_min) / lsb)          0 .. 2**bits - 1
    volts = v_min + code * lsb

It is NOT the board's calibrated polynomial (ai_dev_scaling_coeff), which
carries per-device gain and offset corrections. That distinction matters in
exactly one case and no other:

    reconstructing OUR volts from the codes we send   -> this map is exact,
        because it is the same map in both directions and the sidecar states
        it. This is what the other group needs.
    recovering the board's TRUE internal codes        -> this map is close but
        not identical; that needs an unscaled read (AnalogUnscaledReader) and
        the device coefficients, captured at acquisition time.

So a file written by export_raw round-trips to the original volts to within
half an LSB, and says so in its sidecar. If you later switch the acquisition
to unscaled reads, the sidecar gains the real coefficients and everything
downstream keeps working.

OFFSET BINARY, NOT TWO'S COMPLEMENT. code 0 is the most negative input and
2**bits - 1 the most positive, so the type is unsigned and a bipolar range has
mid-scale at 0 V. Reading these files as int32 makes the trace jump between
extremes at every zero crossing - the classic way to get this wrong, which is
why the convention is named in the sidecar rather than assumed.
"""

import json
from pathlib import Path

import numpy as np

from _constants import ADC_BITS, RAW_DTYPE     # noqa: E402

#: kept as an alias so callers written against the old name still work
DEFAULT_BITS = ADC_BITS


def lsb_volts(v_min, v_max, bits=DEFAULT_BITS):
    """Volts per code step for a range, using the ideal map."""
    return (float(v_max) - float(v_min)) / (2 ** bits - 1)


def volts_to_codes(volts, v_min, v_max, bits=DEFAULT_BITS):
    """(codes, n_clipped). Values outside the range CLIP, and are counted.

    Clipping is reported rather than raised: a run that briefly saturated is
    still worth sending, but silently flattening its peaks is not something a
    caller should have to discover downstream.
    """
    volts = np.asarray(volts, dtype=np.float64)
    step = lsb_volts(v_min, v_max, bits)
    top = 2 ** bits - 1

    raw = np.rint((volts - float(v_min)) / step)
    n_clipped = int(np.count_nonzero((raw < 0) | (raw > top)))
    return np.clip(raw, 0, top).astype(np.uint32), n_clipped


def codes_to_volts(codes, v_min, v_max, bits=DEFAULT_BITS):
    """The inverse of volts_to_codes. Exact - it is the same affine map."""
    codes = np.asarray(codes, dtype=np.float64)
    return (float(v_min) + codes * lsb_volts(v_min, v_max, bits)).astype(np.float32)


def channel_range(outdir, channel):
    """(v_min, v_max) for one channel, from the run.json long_run wrote.

    describe() records the range per channel, so every run already carries
    what is needed to reconstruct codes - including runs recorded before this
    module existed.
    """
    info = json.loads((Path(outdir) / "run.json").read_text())
    for entry in info.get("ai", {}).get("channels", []):
        if entry.get("name") == channel:
            return float(entry["v_min"]), float(entry["v_max"])
    raise KeyError(
        f"{channel!r} is not in {outdir}/run.json - channels there are "
        f"{[c.get('name') for c in info.get('ai', {}).get('channels', [])]}")


def sidecar(channel, v_min, v_max, rate, n_samples, start_sample=0,
            bits=DEFAULT_BITS, n_clipped=0, source=None):
    """Everything the other end needs to turn the codes back into volts."""
    return {
        "format": "raw ADC codes, offset binary",
        "dtype": RAW_DTYPE,
        "byte_order": "little endian",
        "bits": int(bits),
        "code_min": 0,
        "code_max": 2 ** bits - 1,
        "channel": channel,
        "v_min": float(v_min),
        "v_max": float(v_max),
        "lsb_volts": lsb_volts(v_min, v_max, bits),
        "reconstruct": "volts = v_min + code * lsb_volts",
        "sample_rate_hz": float(rate) if rate else None,
        "start_sample": int(start_sample),
        "n_samples": int(n_samples),
        "n_clipped": int(n_clipped),
        "derived_from": source,
        "note": ("Codes were produced from float32 volts with the ideal affine "
                 "map above, not from an unscaled read, so they reconstruct "
                 "THESE volts exactly but are not the board's calibrated "
                 "internal codes."),
    }


def export_raw(outdir, dest, channel=None, bits=DEFAULT_BITS, verbose=True):
    """Write one .u32 per segment, plus one .json sidecar per channel.

    Reads the run a segment at a time, so an 8 h run never has to fit in RAM.
    Returns the list of files written.
    """
    from postprocess import channels_of, iter_segments, load_long_run

    outdir, dest = Path(outdir), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    manifest, _ = load_long_run(outdir)
    rate = manifest[0].get("nominal_rate")
    channels = [channel] if channel else channels_of(outdir)

    written = []
    for name in channels:
        v_min, v_max = channel_range(outdir, name)
        total, clipped, first = 0, 0, None
        for i, (idx, volts) in enumerate(iter_segments(outdir, name, mmap=True)):
            codes, n_clip = volts_to_codes(volts, v_min, v_max, bits)
            path = dest / f"seg_{i:05d}_{name}.u32"
            codes.astype(RAW_DTYPE, copy=False).tofile(path)
            written.append(path)
            if first is None:
                first = int(idx[0])
            total += len(codes)
            clipped += n_clip
            if verbose:
                print(f"  {path.name}  {len(codes):,} samples"
                      + (f"  ({n_clip:,} CLIPPED)" if n_clip else ""))

        meta = sidecar(name, v_min, v_max, rate, total, first or 0, bits,
                       clipped, source=str(outdir))
        meta_path = dest / f"{name}.json"
        meta_path.write_text(json.dumps(meta, indent=2))
        written.append(meta_path)
        if verbose:
            print(f"  {meta_path.name}: {total:,} samples, "
                  f"{v_min:+g}..{v_max:+g} V, {meta['lsb_volts'] * 1e6:.4f} uV/code"
                  + (f", {clipped:,} CLIPPED" if clipped else ""))
    return written


def load_raw(path, sidecar_path=None):
    """(volts, meta) for a .u32 written by export_raw - the reader's side."""
    path = Path(path)
    if sidecar_path is None:
        sidecar_path = path.parent / f"{path.stem.split('_')[-1]}.json"
    meta = json.loads(Path(sidecar_path).read_text())
    codes = np.fromfile(path, dtype=meta["dtype"])
    return codes_to_volts(codes, meta["v_min"], meta["v_max"], meta["bits"]), meta
