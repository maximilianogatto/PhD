"""
Every function in the driver, one at a time, with a PASS/FAIL summary.

    python example_basics.py                    # everything
    python example_basics.py --no-pps           # no rubidium connected
    python example_basics.py --no-ao            # nothing wired to the outputs
    python example_basics.py --loopback         # AO 0 wired to AI 0
    python example_basics.py --device Dev2

Each step is independent and its own failure is caught, so one thing not
working does not hide the rest. At the end you get a table saying which parts
of the driver work with the hardware you have in front of you right now.

Nothing here records to disk and nothing takes longer than a second or two -
for the recording path use example_long_run.py instead.

WITHOUT HARDWARE: make a simulated device in NI MAX (Devices and Interfaces
-> right-click -> Create New -> Simulated NI-DAQmx Device -> USB-6289) and
run with --no-pps. A simulated board returns a sine wave and has no rubidium.

WIRING FOR THE FULL SET
    1 pps  -> PFI9        screw terminal 83, ground 82
    AO 0   -> AI 0        terminal 15 -> 1, grounds 16 -> 3   (--loopback)
"""

import argparse
import sys
import time
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from usb6289 import USB6289                        # noqa: E402
from postprocess import times_from_edges           # noqa: E402

RESULTS = []


def step(title, skip_if=False, skip_reason=""):
    """Run the decorated function, catch whatever it throws, record a verdict."""
    def wrap(function):
        print("\n" + "=" * 72)
        print(f"  {title}")
        print("=" * 72)
        if skip_if:
            print(f"  SKIPPED - {skip_reason}")
            RESULTS.append(("skip", title, skip_reason))
            return
        started = time.time()
        try:
            function()
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}")
            traceback.print_exc(limit=3)
            RESULTS.append(("fail", title, f"{type(e).__name__}: {e}"))
        else:
            RESULTS.append(("pass", title, f"{time.time() - started:.2f} s"))
    return wrap


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", default="Dev1")
    ap.add_argument("--pps-terminal", default="/Dev1/PFI9")
    ap.add_argument("--no-pps", action="store_true")
    ap.add_argument("--no-ao", action="store_true")
    ap.add_argument("--loopback", action="store_true",
                    help="AO 0 is wired to AI 0, so the waveform can be seen")
    args = ap.parse_args()
    no_pps, no_ao = args.no_pps, args.no_ao

    daq = USB6289("daq", device=args.device)

    # ------------------------------------------------------------------ 1
    @step("1. What is this board? (get_idn, caps)")
    def _():
        for key, value in daq.get_idn().items():
            print(f"    {key:16} {value}")
        caps = daq.caps
        print(f"    analog in        {len(caps['ai'])} channels")
        print(f"    analog out       {len(caps['ao'])} channels")
        print(f"    counters         {list(caps['ctrs'])}")
        print(f"    max rate 1 chan  {caps['ai_max_rate']:,.0f} S/s")
        print(f"    max rate N chan  {caps['ai_max_multi_rate']:,.0f} S/s "
              f"AGGREGATE - divide by the number of channels")
        print(f"    input ranges     {caps['ranges']} V")

    # ------------------------------------------------------------------ 2
    @step("2. The submodule tree (what you can reach)")
    def _():
        print(f"    daq.ai      {len(daq.ai.channels)} channels, "
              f"{len(daq.ai.parameters)} parameters")
        print(f"    daq.pps     {len(daq.pps.parameters)} parameters")
        print(f"    daq.ao      {len(daq.ao.channels)} channels, "
              f"{len(daq.ao.parameters)} parameters")
        print(f"    daq.ai.ai0 and daq.ai.channels[0] are the same object: "
              f"{daq.ai.ai0 is daq.ai.channels[0]}")
        print(f"    full_name of daq.ai.rate is '{daq.ai.rate.full_name}'")

    # ------------------------------------------------------------------ 3
    @step("3. Configure one input, and read back what the HARDWARE will do")
    def _():
        daq.ai.enable("ai0")
        daq.ai.rate(25_000)
        daq.ai.duration(0.2)
        daq.ai.ai0.v_min(-1.5)
        daq.ai.ai0.v_max(1.5)
        print(f"    requested rate   {daq.ai.rate():,.3f} S/s")
        print(f"    ACTUAL rate      {daq.ai.actual_rate():,.3f} S/s "
              f"<- the clock divides 20 MHz; use this one")
        print(f"    convert clock    {daq.ai.conv_rate():,.0f} Hz")
        print(f"    samples          {daq.ai.n_samples():,} per channel")

    # ------------------------------------------------------------------ 4
    @step("4. check() - find configuration problems before starting")
    def _():
        print("    a good configuration:")
        daq.check()

        print("\n    now break it on purpose (5 channels at 200 kS/s):")
        daq.ai.enable("ai0", "ai1", "ai2", "ai3", "ai4")
        daq.ai.rate(200_000)
        problems = daq.check()
        assert any(level == "error" for level, _, _ in problems), \
            "check() should have caught the aggregate rate"

        daq.ai.enable("ai0")           # put it back
        daq.ai.rate(25_000)

    # ------------------------------------------------------------------ 5
    @step("5. wiring() - which screw terminals to connect")
    def _():
        rows = daq.wiring()
        print(f"\n    ...and the same thing as data: {len(rows)} rows, e.g.")
        print(f"    {rows[0]}")

    # ------------------------------------------------------------------ 6
    @step("6. acquire() - one record")
    def _():
        data = daq.ai.acquire()
        y = data["ai0"]
        print(f"    got {{{', '.join(data)}}}, {len(y):,} samples")
        print(f"    ai0: {y.mean():+.4f} +/- {y.std():.4f} V, "
              f"range {y.min():+.4f} .. {y.max():+.4f} V")
        print(f"    dtype {y.dtype} - float32 holds 18 ADC bits with ~64x to "
              f"spare, but ACCUMULATE in float64:")
        print(f"      y.mean()                -> {y.mean():.9f}")
        print(f"      y.mean(dtype=np.float64)-> {y.mean(dtype=np.float64):.9f}")

    # ------------------------------------------------------------------ 7
    @step("7. The trace parameter - QCoDeS's view of the same data")
    def _():
        # trace NEVER measures. acquire() is the only thing that does.
        daq.ai.acquire()
        y = daq.ai.ai0.trace()
        t = daq.ai.time_axis()
        print(f"    time_axis {len(t):,} points, {t[0]:.6f} .. {t[-1]:.4f} s")
        print(f"    trace     {len(y):,} points")
        print(f"    same object as daq.ai.last['ai0']: "
              f"{np.array_equal(y, daq.ai.last['ai0'])}")

        # Reading it without acquiring first is an error, not stale data.
        daq.ai.rate(25_000)            # any config change drops the held scan
        try:
            daq.ai.ai0.trace()
        except RuntimeError as e:
            print(f"    reading without acquire() raises, as it should:")
            print(f"      {str(e)[:70]}...")

    # ------------------------------------------------------------------ 8
    @step("8. Two channels at once, and the mux skew between them")
    def _():
        daq.ai.enable("ai0", "ai1")
        data = daq.ai.acquire()
        print(f"    one acquisition -> {list(data)}")
        print(f"    they are NOT simultaneous - one ADC behind a mux:")
        for channel in daq.ai.active:
            print(f"      {channel.short_name}  sampled "
                  f"{channel.time_offset() * 1e6:6.2f} us into each scan")
        print(f"    so ai1's true time axis is "
              f"time_axis() + {daq.ai.ai1.time_offset() * 1e6:.2f} us")
        print(f"    per-channel rate now {daq.ai.actual_rate():,.0f} S/s "
              f"({daq.ai.actual_rate() * 2:,.0f} S/s aggregate)")
        daq.ai.enable("ai0")

    # ------------------------------------------------------------------ 9
    @step("9. acquire_chunks() - streaming, constant memory")
    def _():
        total = 0
        for i, (scan, chunk) in enumerate(daq.ai.acquire_chunks(duration=1.0)):
            total += len(chunk["ai0"])
            if i < 3:
                print(f"    chunk {i}: starts at scan {scan:,}, "
                      f"{len(chunk['ai0']):,} samples")
        print(f"    ... {total:,} samples total, never more than one chunk "
              f"in RAM at a time")

    # ----------------------------------------------------------------- 10
    @step("10. Analog output - DC levels", skip_if=no_ao,
          skip_reason="--no-ao")
    def _():
        daq.ao.ao1.dc(0.5)             # 'off' -> 'dc' automatically
        daq.ao.ao2.dc(-0.25)
        print(f"    roles      {[c.role() for c in daq.ao.channels]}")
        print(f"    dc_levels  {daq.ao.dc_levels()}")
        print(f"    broadcast  daq.ao.channels.dc() = {daq.ao.channels.dc()}")
        print("    these are applied immediately - no clock, nothing to start")
        daq.ao.ao1.role("off")
        daq.ao.ao2.role("off")

    # ----------------------------------------------------------------- 11
    @step("11. Analog output - a waveform", skip_if=no_ao,
          skip_reason="--no-ao")
    def _():
        daq.ao.wave_channel("ao0")
        daq.ao.shape("square")
        daq.ao.freq(100.0)
        daq.ao.amp(1.0)
        print(f"    asked for  {daq.ao.freq():g} Hz")
        print(f"    buffer     {daq.ao.buffer_len():,} samples "
              f"(FIFO holds {daq.ao.fifo_samples():,})")
        print(f"    onboard?   {daq.ao.onboard()}  <- True means it loops from "
              f"the board and USB cannot starve it")
        actual = daq.ao.start()
        print(f"    generating at {actual:g} Hz (snapped so a whole number of "
              f"samples fits one period)")
        time.sleep(0.3)
        daq.ao.stop()
        print(f"    stopped, ao0 back to 0 V")

    # ----------------------------------------------------------------- 12
    @step("12. Loopback - generate and measure the same signal",
          skip_if=no_ao or not args.loopback,
          skip_reason="needs --loopback and AO 0 wired to AI 0")
    def _():
        daq.ai.enable("ai0")
        daq.ai.duration(0.1)
        daq.ao.wave_channel("ao0")

        # generating() guarantees the output stops even if this raises
        with daq.ao.generating(shape="square", freq=100.0, amp=1.0):
            data = daq.ai.acquire()

        y = data["ai0"]
        mid = (y.max() + y.min()) / 2
        high = y >= mid
        crossings = np.flatnonzero(~high[:-1] & high[1:]) + 1
        print(f"    measured {y.min():+.3f} .. {y.max():+.3f} V")
        if len(crossings) >= 2:
            spp = np.diff(crossings).mean()
            print(f"    {len(crossings)} periods, {spp:.1f} samples each "
                  f"-> {daq.ai.actual_rate() / spp:.2f} Hz")
        print(f"    ao0 is back at {0.0:g} V and the settings are restored: "
              f"freq={daq.ao.freq():g} Hz")

    # ----------------------------------------------------------------- 13
    @step("13. The 1 pps counter - the atomic time axis",
          skip_if=no_pps, skip_reason="--no-pps")
    def _():
        daq.pps.terminal(args.pps_terminal)
        daq.ai.enable("ai0")
        daq.ai.duration(3.0)           # long enough to span a few seconds

        data, edges = daq.acquire_with_pps()
        print(f"    {len(edges)} atomic seconds caught in "
              f"{daq.ai.duration():g} s (expect ~{int(daq.ai.duration())})")
        print(f"    edge scan indices: {edges[:5]}")
        if len(edges) >= 2:
            print(f"    scans between edges: {np.diff(edges)}")
            print(f"    measured_rate {daq.pps.measured_rate():,.3f} "
                  f"scans/atomic second")
            print(f"    ppm           {daq.pps.ppm():+.1f} "
                  f"-> {abs(daq.pps.ppm()) * 0.0864:.2f} s of drift per day")
            t = times_from_edges(np.arange(len(data["ai0"])), edges)
            print(f"    atomic axis   {t[0]:+.4f} .. {t[-1]:+.4f} s "
                  f"(t=0 is the first edge, so the start is negative)")

    # ----------------------------------------------------------------- 14
    @step("14. clock_check() - the board's clock against the rubidium",
          skip_if=no_pps, skip_reason="--no-pps")
    def _():
        daq.clock_check(seconds=5)

    # ----------------------------------------------------------------- 15
    @step("15. describe() and the QCoDeS snapshot")
    def _():
        description = daq.describe()
        print(f"    describe() keys: {list(description)}")
        print(f"    ai.rate_actual   {description['ai']['rate_actual']:,.3f}")
        print(f"    ai.channels      "
              f"{[c['name'] for c in description['ai']['channels']]}")
        snapshot = daq.snapshot()
        print(f"    snapshot nests by subsystem: "
              f"{list(snapshot['submodules'])}")

    daq.close()

    # ------------------------------------------------------------- summary
    print("\n" + "=" * 72)
    print("  SUMMARY")
    print("=" * 72)
    width = max(len(title) for _, title, _ in RESULTS)
    for verdict, title, detail in RESULTS:
        mark = {"pass": "PASS", "fail": "FAIL", "skip": "skip"}[verdict]
        print(f"  {mark}  {title:<{width}}  {detail}")
    failed = sum(1 for v, _, _ in RESULTS if v == "fail")
    passed = sum(1 for v, _, _ in RESULTS if v == "pass")
    print(f"\n  {passed} passed, {failed} failed, "
          f"{len(RESULTS) - passed - failed} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
