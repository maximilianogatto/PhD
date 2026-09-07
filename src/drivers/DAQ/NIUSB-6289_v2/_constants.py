"""Every shared constant of the USB-6289 driver, in one place.

WHY A MODULE AND NOT A LINE IN EACH FILE. ADC_BITS was defined in _ai.py and
again in postprocess.py, and the sample-format table existed three times - in
_longrun (what to write), postprocess (what to read) and livescope (what to
tail). Nothing kept them equal, so adding a format meant editing three dicts
and changing the converter resolution meant remembering two files. Anything
more than one module needs to agree on lives here instead.

IMPORTS NOTHING BUT NUMPY, ON PURPOSE. postprocess.py and livescope.py are
documented to work on any machine with no NI-DAQmx installed, and they import
this module - so nothing here may pull in nidaqmx, directly or otherwise. That
is why _TERM_CFG stays in _ai.py: it holds nidaqmx enum objects. The pin tables
stay in pinout_6289.py for the same reason in reverse - they are already a
single home, and they are data rather than settings.
"""

import numpy as np

# ======================================================= the converter itself
#: Resolution of the USB-6289's ADC, in bits. NI specifies the 628x family at
#: 18-bit (the 625x family is 16). This is only a FALLBACK: _configure reads
#: ai_resolution back from the device and records that in the manifest, so a
#: run carries the number its own hardware reported rather than this one.
#:
#: It matters because it sets the code span a raw sample is interpreted over:
#:     volts = v_min + code * (v_max - v_min) / (2**ADC_BITS - 1)
#: Get it wrong and every reconstructed voltage is wrong by the ratio of the
#: two spans - 24 instead of 18 would divide by 64x too much.
ADC_BITS = 18

# ============================================================ sample formats
#: What a sample is on disk. ONE table: the file suffix, the numpy dtype and
#: what the numbers mean. Adding a format is one row here, not three dicts.
#:
#:   float32   volts, already scaled by DAQmx
#:   uint32    raw converter codes, offset binary - the interchange format
SAMPLE_FORMATS = {
    "float32": {"suffix": ".f32", "dtype": np.float32, "units": "V"},
    "uint32":  {"suffix": ".u32", "dtype": np.uint32,  "units": "ADC codes"},
}

DATATYPES = tuple(SAMPLE_FORMATS)                       # for a vals=Enum(...)
SUFFIX_OF = {k: v["suffix"] for k, v in SAMPLE_FORMATS.items()}
DTYPE_OF = {k: v["dtype"] for k, v in SAMPLE_FORMATS.items()}
UNITS_OF = {k: v["units"] for k, v in SAMPLE_FORMATS.items()}
DATATYPE_OF_SUFFIX = {v: k for k, v in SUFFIX_OF.items()}

#: Bytes per sample. Every supported format is 4, which is what lets livescope
#: seek into a growing file by sample index without knowing which one it is.
SAMPLE_BYTES = 4

#: The on-the-wire dtype for raw files: little-endian unsigned 32-bit, spelled
#: explicitly so a big-endian host would still write what the other group reads.
RAW_DTYPE = "<u4"

# ============================================================== the device
AI_CHANNELS = tuple(f"ai{i}" for i in range(32))
AO_CHANNELS = tuple(f"ao{i}" for i in range(4))    # terminals 15 / 31 / 47 / 63
COUNTERS = ("ctr0", "ctr1")
PFI_LINES = tuple(f"PFI{i}" for i in range(16))
AI_RANGES = (0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)   # M Series programmable gains

#: Used only when the device will not report its own limits.
AI_MAX_SINGLE_FALLBACK = 666_666.67
AI_MAX_MULTI_FALLBACK = 500_000.0

#: What the M Series wires to each PFI line by default, for terminal_roles().
M_SERIES_DEFAULTS = {
    "PFI3":  "CTR 1 SRC (default)",
    "PFI4":  "CTR 1 GATE (default)",
    "PFI8":  "CTR 0 SRC (default)",
    "PFI9":  "CTR 0 GATE (default)",
    "PFI10": "CTR 0 AUX (default)",
    "PFI11": "CTR 1 AUX (default)",
    "PFI12": "CTR 0 OUT (default)",
    "PFI13": "CTR 1 OUT (default)",
    "PFI14": "FREQ OUT (default)",
}

# ========================================================== analog output
AO_MAX = 10.0                # +/- volts
AO_FIFO_FALLBACK = 8191      # M Series spec, used only when the device is mute
SHAPES = ("square", "sine", "triangle", "ramp")
ROLES = ("off", "dc", "wave")

# ============================================================ DAQmx errors
OVERFLOW = -200279           # PC not reading the driver buffer fast enough
FIFO_OVERFLOW = -200361      # USB not draining the onboard FIFO fast enough
UNDERFLOW = -200621          # driver not writing fast enough

# ================================================== counters and timestamps
COUNTER_BITS = 32            # rolls over every 47.7 h at 25 kS/s
DRAIN_ERROR_BACKOFF = 1.0    # s, after a counter read that failed immediately
MAX_DRAIN_ERRORS = 30        # consecutive failures before the reader gives up
READ_TIMEOUT = 1.5           # s; longer than the 1 s between 1 pps pulses

# ================================================================ analysis
ANCHOR_CANDIDATES = 8        # leading artefacts tolerated by clean_edges
