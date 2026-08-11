"""
NI USB-6289 screw-terminal pinout (128 pins) and wiring helpers.

Transcribed from the device's screw-terminal pinout sheet. Pins 1-64 are the
analog connector, 65-128 the digital connector.

    from pinout_6289 import pin_of, signal_at, wiring

    pin_of("AI 14")        -> 24
    signal_at(24)          -> 'AI 14'
    wiring("ai14", "RSE")  -> printed hookup for that channel

Run `python pinout_6289.py` for the full table, or
`python pinout_6289.py --verify` to cross-check the channel names against
what the DAQmx driver reports for the connected device.
"""

import argparse
import re

# ---------------------------------------------------------------------------
# Analog connector, pins 1-64
# ---------------------------------------------------------------------------
PINOUT = {
    1: "AI 0",     17: "AI 4",     33: "AI 16",       49: "AI 20",
    2: "AI 8",     18: "AI 12",    34: "AI 24",       50: "AI 28",
    3: "AI GND",   19: "AI GND",   35: "AI GND",      51: "AI GND",
    4: "AI 1",     20: "AI 5",     36: "AI 17",       52: "AI 21",
    5: "AI 9",     21: "AI 13",    37: "AI 25",       53: "AI 29",
    6: "AI GND",   22: "AI GND",   38: "AI GND",      54: "AI GND",
    7: "AI 2",     23: "AI 6",     39: "AI 18",       55: "AI 22",
    8: "AI 10",    24: "AI 14",    40: "AI 26",       56: "AI 30",
    9: "AI GND",   25: "AI GND",   41: "AI GND",      57: "AI GND",
    10: "AI 3",    26: "AI 7",     42: "AI 19",       58: "AI 23",
    11: "AI 11",   27: "AI 15",    43: "AI 27",       59: "AI 31",
    12: "AI GND",  28: "AI GND",   44: "AI GND",      60: "AI GND",
    13: "AI SENSE", 29: "APFI 0",  45: "AI SENSE 2",  61: "APFI 1",
    14: "AI GND",  30: "AI GND",   46: "AI GND",      62: "AI GND",
    15: "AO 0",    31: "AO 1",     47: "AO 2",        63: "AO 3",
    16: "AO GND",  32: "AO GND",   48: "AO GND",      64: "AO GND",
}

# ---------------------------------------------------------------------------
# Digital connector, pins 65-128
# ---------------------------------------------------------------------------
PINOUT.update({
    65: "P0.0",         81: "PFI 8/P2.0",   97: "P0.8",     113: "P0.24",
    66: "P0.1",         82: "D GND",        98: "P0.9",     114: "D GND",
    67: "P0.2",         83: "PFI 9/P2.1",   99: "P0.10",    115: "P0.25",
    68: "P0.3",         84: "D GND",       100: "P0.11",    116: "D GND",
    69: "P0.4",         85: "PFI 10/P2.2", 101: "P0.12",    117: "P0.26",
    70: "P0.5",         86: "D GND",       102: "P0.13",    118: "D GND",
    71: "P0.6",         87: "PFI 11/P2.3", 103: "P0.14",    119: "P0.27",
    72: "P0.7",         88: "D GND",       104: "P0.15",    120: "D GND",
    73: "PFI 0/P1.0",   89: "PFI 12/P2.4", 105: "P0.16",    121: "P0.28",
    74: "PFI 1/P1.1",   90: "D GND",       106: "P0.17",    122: "D GND",
    75: "PFI 2/P1.2",   91: "PFI 13/P2.5", 107: "P0.18",    123: "P0.29",
    76: "PFI 3/P1.3",   92: "D GND",       108: "P0.19",    124: "D GND",
    77: "PFI 4/P1.4",   93: "PFI 14/P2.6", 109: "P0.20",    125: "P0.30",
    78: "PFI 5/P1.5",   94: "D GND",       110: "P0.21",    126: "D GND",
    79: "PFI 6/P1.6",   95: "PFI 15/P2.7", 111: "P0.22",    127: "P0.31",
    80: "PFI 7/P1.7",   96: "+5 V",        112: "P0.23",    128: "D GND",
})

# Signal -> pin. Ground/return names appear many times, so those map to a list.
SIGNAL_TO_PIN = {}
for _pin, _sig in PINOUT.items():
    SIGNAL_TO_PIN.setdefault(_sig, []).append(_pin)


def _norm(name):
    """'ai14', 'AI14', 'AI 14', 'Dev1/ai14' -> 'AI 14'."""
    s = str(name).strip().upper()
    s = s.split("/")[-1] if s.startswith("DEV") else s
    m = re.fullmatch(r"(AI|AO)\s*(\d+)", s)
    if m:
        return f"{m.group(1)} {int(m.group(2))}"
    m = re.fullmatch(r"PFI\s*(\d+)", s)
    if m:
        return f"PFI {int(m.group(1))}"
    return s


def signal_at(pin):
    """Pin number -> signal name."""
    if pin not in PINOUT:
        raise KeyError(f"pin {pin} is out of range (1-128)")
    return PINOUT[pin]


def pin_of(signal):
    """Signal name -> pin number (or list of pins for grounds/returns).

    Accepts 'AI 14', 'ai14', 'Dev1/ai14', 'PFI 3', 'P0.7', 'AI GND'.
    """
    key = _norm(signal)

    if key in SIGNAL_TO_PIN:
        pins = SIGNAL_TO_PIN[key]
        return pins[0] if len(pins) == 1 else pins

    # PFI and P1/P2 share terminals: 'PFI 3' lives on 'PFI 3/P1.3'
    for name, pins in SIGNAL_TO_PIN.items():
        if "/" in name and key in [p.strip() for p in name.split("/")]:
            return pins[0]

    raise KeyError(f"unknown signal {signal!r} (normalised to {key!r})")


def nearest_ground(pin, ground="AI GND"):
    """Closest pin carrying `ground` to the given pin — the one to wire to."""
    cands = SIGNAL_TO_PIN[ground]
    return min(cands, key=lambda g: abs(g - pin))


# ---------------------------------------------------------------------------
# Differential pairing: on 32-channel M Series the positive input AI n is
# paired with AI n+8 as its negative input.
# ---------------------------------------------------------------------------
DIFF_POSITIVE = list(range(0, 8)) + list(range(16, 24))


def diff_pair(n):
    """Differential partner of AI n, or None if AI n cannot be a DIFF channel."""
    return n + 8 if n in DIFF_POSITIVE else None


def wiring(channel, terminal_config="RSE"):
    """Print how to wire an AI channel for a given terminal configuration."""
    key = _norm(channel)
    m = re.fullmatch(r"AI (\d+)", key)
    if not m:
        raise ValueError(f"{channel!r} is not an analog input channel")
    n = int(m.group(1))
    if not 0 <= n <= 31:
        raise ValueError(f"AI {n} does not exist on the USB-6289")

    cfg = terminal_config.upper()
    sig_pin = pin_of(f"AI {n}")
    bank_sense = "AI SENSE" if n < 16 else "AI SENSE 2"

    print(f"AI {n}  ({cfg})")
    print(f"  signal (+) -> pin {sig_pin:3d}  [AI {n}]")

    if cfg == "DIFF":
        partner = diff_pair(n)
        if partner is None:
            print(f"  ERROR: AI {n} is a negative input; it cannot be a DIFF")
            print(f"         channel. Use AI {n - 8} in DIFF mode instead,")
            print(f"         which uses AI {n} as its negative terminal.")
            return
        print(f"  signal (-) -> pin {pin_of(f'AI {partner}'):3d}  [AI {partner}]")
        print(f"  (optional bias resistors to AI GND, e.g. pin "
              f"{nearest_ground(sig_pin)})")
    elif cfg == "RSE":
        print(f"  return     -> pin {nearest_ground(sig_pin):3d}  [AI GND]")
    elif cfg == "NRSE":
        print(f"  reference  -> pin {pin_of(bank_sense):3d}  [{bank_sense}]")
    else:
        raise ValueError(f"unknown terminal config {terminal_config!r}")


def table():
    """Print the full pinout, analog and digital connectors side by side."""
    for lo, hi, title in ((1, 64, "ANALOG CONNECTOR"),
                          (65, 128, "DIGITAL CONNECTOR")):
        print(f"\n{title}  (pins {lo}-{hi})")
        print("-" * 74)
        quarter = (hi - lo + 1) // 4
        for row in range(quarter):
            cells = []
            for col in range(4):
                p = lo + row + col * quarter
                cells.append(f"{p:3d} {PINOUT[p]:<14s}")
            print("  ".join(cells))


def verify(device="Dev1"):
    """Cross-check the transcribed AI/AO names against the DAQmx driver."""
    import nidaqmx

    dev = nidaqmx.system.Device(device)
    print(f"Verifying against {dev.name} ({dev.product_type})\n")

    ok = True
    for kind, chans in (("AI", dev.ai_physical_chans),
                        ("AO", dev.ao_physical_chans)):
        driver = {c.name.split("/")[-1].upper() for c in chans}
        sheet = {s.replace(" ", "") for s in PINOUT.values()
                 if s.startswith(kind + " ") and s.split()[-1].isdigit()}
        missing, extra = driver - sheet, sheet - driver
        print(f"{kind}: driver reports {len(driver)}, pinout lists {len(sheet)}")
        if missing:
            print(f"  in driver but not on the sheet: {sorted(missing)}")
            ok = False
        if extra:
            print(f"  on the sheet but not in driver: {sorted(extra)}")
            ok = False

    print("\nMatch." if ok else "\nMismatch - re-check the transcription.")
    return ok


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="USB-6289 pinout reference.")
    p.add_argument("--verify", action="store_true",
                   help="cross-check names against the connected device")
    p.add_argument("--device", default="Dev1")
    p.add_argument("--pin", type=int, help="look up a single pin")
    p.add_argument("--signal", help="look up a single signal")
    a = p.parse_args()

    if a.pin:
        print(f"pin {a.pin} -> {signal_at(a.pin)}")
    elif a.signal:
        print(f"{a.signal} -> pin {pin_of(a.signal)}")
    elif a.verify:
        verify(a.device)
    else:
        table()
