"""Prove the files are little-endian regardless of what is handed in.

The risk is not this Mac - it is that the byte order of the deliverable would
be a property of the acquiring machine. So feed the writer BIG-endian arrays,
which is what a big-endian host's native uint32 would look like, and check the
bytes that land on disk.
"""
import shutil, sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, "/Users/maximilianogatto/Library/CloudStorage/OneDrive-Personal/PhD/src/drivers/DAQ/NIUSB-6289_v2")
from _longrun import SegmentWriter

import _constants as C

VALUES = [1, 2, 258, 4_294_967_295]        # 258 = 0x00000102, distinguishable
LE_BYTES = (b"\x01\x00\x00\x00" b"\x02\x00\x00\x00"
            b"\x02\x01\x00\x00" b"\xff\xff\xff\xff")

print(f"host is {sys.byteorder}-endian; declared on-disk dtypes:")
for name, d in C.DTYPE_OF.items():
    print(f"  {name:8s} {d.str}")
assert C.DTYPE_OF["uint32"].str == "<u4" and C.DTYPE_OF["float32"].str == "<f4"
assert C.INDEX_DTYPE.str == "<i8"

for order, label in (("<u4", "little-endian input"),
                     (">u4", "BIG-endian input   "),
                     ("=u4", "native input       ")):
    OUT = Path("endian_" + order[0].replace("<", "le").replace(">", "be")
               .replace("=", "na"))
    shutil.rmtree(OUT, ignore_errors=True); OUT.mkdir(parents=True)
    w = SegmentWriter(OUT, ["ai1"], 25_000.0, verbose=False, datatype="uint32")
    w.log({"event": "start", "channels": ["ai1"], "nominal_rate": 25_000.0,
           "datatype": "uint32",
           "scaling": {"ai1": {"v_min_actual": -2.0, "v_max_actual": 2.0,
                               "bits": 18}}, "wall": time.time()})
    w.write({"ai1": np.array(VALUES, dtype=order)}, 0)
    w.write_edges(np.array([1], dtype=order.replace("u4", "i8")))
    w.close(len(VALUES))

    seg = next(OUT.glob("*.u32"))
    raw = seg.read_bytes()
    assert raw == LE_BYTES, f"{label}: got {raw!r}"
    assert (OUT / "edges.i64").read_bytes() == b"\x01\x00\x00\x00\x00\x00\x00\x00"
    # and it reads back to the same numbers
    back = np.fromfile(seg, dtype="<u4")
    assert list(back) == VALUES, back
    print(f"  {label} -> {raw[:8].hex(' ')} ...  LITTLE endian on disk, "
          f"reads back {list(back)}")

print("\nthe writer normalises byte order; the deliverable does not depend "
      "on the acquiring host")
