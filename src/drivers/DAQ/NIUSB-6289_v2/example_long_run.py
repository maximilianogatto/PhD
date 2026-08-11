"""
A whole long run, start to finish. Run this on the lab PC:

    python example_long_run.py                 # 2 minutes, needs the 1 pps
    python example_long_run.py --minutes 0.5   # 30 seconds
    python example_long_run.py --no-pps        # bench test, no rubidium
    python example_long_run.py --device Dev2

It records, watches the run while it happens, stops, and reads the data back
- so if this script works end to end, the driver works.

You do not need real hardware to try it. In NI MAX: Devices and Interfaces ->
right-click -> Create New -> Simulated NI-DAQmx Device -> USB-6289. It will
produce a sine wave instead of your signal, and it has no 1 pps, so use
--no-pps.

WHERE THE DATA GOES. One directory, four files:

    <outdir>/run.json         the instrument settings, as JSON
    <outdir>/manifest.jsonl   which file holds which scans
    <outdir>/edges.i64        the atomic seconds
    <outdir>/seg_00000_ai0_<stamp>.f32     the samples

The .f32 is raw float32 back to back and nothing else, so even without this
library the data is one line away:

    import numpy as np
    y = np.fromfile("seg_00000_ai0_20260811T130000Z.f32", dtype=np.float32)

By default there is ONE file per channel for the whole run, however long it
is. Pass rotate_minutes=60 to long_run() if you would rather have hourly
files - worth it for runs of days, because a closed file can be archived
while the run continues, and a filesystem problem then costs one hour rather
than the lot.
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from usb6289 import USB6289                                    # noqa: E402
from postprocess import (channels_of, load_long_run,           # noqa: E402
                         load_segment, times_from_edges)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", default="Dev1")
    ap.add_argument("--minutes", type=float, default=2.0)
    ap.add_argument("--rate", type=float, default=25_000)
    ap.add_argument("--channels", default="ai0",
                    help="comma separated, e.g. ai0,ai3")
    ap.add_argument("--pps-terminal", default="/Dev1/PFI9")
    ap.add_argument("--no-pps", action="store_true",
                    help="no rubidium connected; the time axis is then the "
                         "board's own clock, which drifts ~4 s/day")
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()

    outdir = Path(args.outdir or
                  f"run_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
    channels = [c.strip() for c in args.channels.split(",")]

    # ---------------------------------------------------------------- 1. set up
    print("=" * 70)
    print("1. CONNECT")
    daq = USB6289("daq", device=args.device)

    daq.ai.enable(*channels)          # enables these, disables everything else
    daq.ai.rate(args.rate)
    for name in channels:
        channel = getattr(daq.ai, name)
        channel.v_min(-1.5)
        channel.v_max(1.5)            # snaps up to a hardware range

    if not args.no_pps:
        daq.pps.terminal(args.pps_terminal)

    # ------------------------------------------------------- 2. check BEFORE
    # This is the point of check(): find out now, not forty minutes in.
    print("\n" + "=" * 70)
    print("2. CHECK THE CONFIGURATION (no hardware is started)")
    daq.check(strict=True)            # raises if anything cannot run

    print("\n" + "=" * 70)
    print("3. WIRING - connect these before you start")
    daq.wiring()

    # ------------------------------------------------------------- 4. record
    print("\n" + "=" * 70)
    print(f"4. RECORD {args.minutes} min -> {outdir}")
    run = daq.long_run(outdir,
                       hours=args.minutes / 60,
                       rotate_minutes=None,      # ONE file per channel
                       background=True,          # returns immediately
                       require_pps=not args.no_pps)

    # background=True means the acquisition runs in a thread and we carry on
    # here - this is where you would drive the OPX, sweep a magnet, etc.
    while run.alive:
        time.sleep(5)
        state = run.status()
        seconds = state.get("seconds", 0)
        t, y = daq.live_preview()     # decimated, safe from another thread
        if len(y):
            print(f"  {seconds:7.1f} s recorded | last {len(y):,} decimated "
                  f"samples: {y.mean():+.4f} +/- {y.std():.4f} V")
        else:
            print(f"  {seconds:7.1f} s recorded")

    run.join()
    print("  run finished")

    # ------------------------------------------------------------ 5. read it
    print("\n" + "=" * 70)
    print("5. READ IT BACK")
    print(f"  files in {outdir}:")
    for path in sorted(outdir.iterdir()):
        print(f"    {path.name:50} {path.stat().st_size / 1e6:8.1f} MB")

    manifest, edges = load_long_run(outdir)
    names = channels_of(outdir)
    segments = sorted({e["index"] for e in manifest if "file" in e})
    gaps = [e for e in manifest if e.get("event") == "gap"]

    print(f"\n  channels : {names}")
    print(f"  segments : {segments}")
    print(f"  1 pps    : {len(edges):,} atomic seconds")
    print(f"  gaps     : {len(gaps)}   <- must be 0 for a clean run")
    for gap in gaps:
        print(f"      at scan {gap['sample']:,}: {gap['error']}")

    # mmap=True keeps it on disk - important once a segment is gigabytes
    scan_index, y = load_segment(outdir, segments[0], names[0], mmap=True)
    print(f"\n  {names[0]}: {len(y):,} scans, "
          f"{y.min():+.4f} .. {y.max():+.4f} V")

    # ------------------------------------------------------ 6. the time axis
    print("\n" + "=" * 70)
    print("6. TIME AXIS")
    if len(edges) >= 2:
        # The real one: every edge is exactly one atomic second, so this
        # measures the board's clock instead of trusting it.
        t = times_from_edges(scan_index, edges)
        scans_per_second = np.diff(edges)
        nominal = args.rate
        true_rate = scans_per_second.mean()
        ppm = (nominal - true_rate) / true_rate * 1e6
        print(f"  atomic time axis : {t[0]:+.4f} .. {t[-1]:+.4f} s")
        print(f"  nominal rate     : {nominal:12,.3f} S/s")
        print(f"  true rate        : {true_rate:12,.3f} scans/atomic second")
        print(f"  board clock error: {ppm:+12.1f} ppm "
              f"({'fast' if ppm < 0 else 'slow'})")
        print(f"  -> {abs(ppm) * 86400 / 1e6:.2f} s of drift per day")
    else:
        t = scan_index / args.rate
        print(f"  no 1 pps, so this is the BOARD's clock: "
              f"{t[0]:.4f} .. {t[-1]:.4f} s")
        print("  it is wrong by a few seconds per day and cannot tell you so")

    daq.close()
    print("\ndone.")


if __name__ == "__main__":
    main()
