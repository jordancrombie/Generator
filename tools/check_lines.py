"""Pull rendered lines from the generator over serial and check them against NTSC-M.

Decodes sync width, burst amplitude/phase and per-bar luma/chroma (hue relative
to burst) for the SMPTE bars pattern.
"""
import sys, time, math
import numpy as np
import serial

import glob; PORT = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob("/dev/cu.wchusbserial*"))[0]
US = 14.31818181818

def dump(s, idx):
    s.reset_input_buffer(); s.write(f"dump {idx}\n".encode())
    buf = b""; t = time.time()
    while time.time() - t < 3:
        buf += s.read(4096)
        if buf.count(b"\n") >= 16 and buf.rstrip().endswith(b"\n") is False and len(buf) > 1900: break
    lines = buf.decode().split("\n")
    i = next(k for k, l in enumerate(lines) if l.startswith(f"line {idx} "))
    hexs = "".join(l.strip() for l in lines[i + 1:i + 16])
    return np.array([int(hexs[j:j + 2], 16) for j in range(0, len(hexs), 2)], float)

def info(s):
    s.reset_input_buffer(); s.write(b"info\n"); time.sleep(0.3)
    import json
    for l in s.read(4096).decode().splitlines():
        if l.startswith("{"): return json.loads(l)

s = serial.Serial(PORT, 115200, timeout=0.2)
cfg = info(s)
sync_c, white_c = cfg["sync_code"], cfg["white_code"]
ire = lambda c: (c - sync_c) * 140.0 / (white_c - sync_c) - 40.0

def demod(x, k0, n, idx):
    """U, V of samples k0..k0+n-1 (subcarrier phase from chain index)."""
    off = (2 * idx) % 4
    k = np.arange(k0, k0 + n); ph = (k + off) % 4
    sin = np.array([0, 1, 0, -1])[ph]; cos = np.array([1, 0, -1, 0])[ph]
    seg = x[k0:k0 + n]
    return seg.mean(), 2 * (seg * sin).mean(), 2 * (seg * cos).mean()

def pulses(x, level=-20):
    """(start_us, width_us) of sync pulses, measured at 50% of sync (-20 IRE).

    Burst troughs also reach -20 IRE, so only runs > 1 us count. The line is
    treated as circular because sync falls exactly at sample 0.
    """
    below = x < level
    roll = int(np.argmax(~below))           # start scanning from a non-sync sample
    b = np.roll(below, -roll)
    d = np.diff(np.concatenate([[0], b.astype(int), [0]]))
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    out = []
    for a, e in zip(starts, ends):
        if (e - a) / US > 1.0:
            out.append((((a + roll) % len(x)) / US, (e - a) / US))
    return sorted(out)

ok = True
def check(name, val, lo, hi, unit=""):
    global ok
    good = lo <= val <= hi; ok &= good
    print(f"  {'PASS' if good else 'FAIL'} {name}: {val:.2f}{unit}  (expect {lo}..{hi})")

for idx, desc in [(30, "line 31 F1 (bars)"), (561 - 1, "line 36 F2-frame (bars, other colour frame)")]:
    x = ire(dump(s, idx)); print(f"{desc}  [chain {idx}]")
    p = pulses(x)
    check("hsync width", p[0][1], 4.6, 4.8, " us")
    check("blank level", x[int(8.1 * US):int(9.1 * US)].mean(), -1.5, 1.5, " IRE")
    k0 = int(round(5.3 * US + 6)); _, bu, bv = demod(x, k0, 24, idx)
    bamp = math.hypot(bu, bv); bph = math.degrees(math.atan2(bv, bu)) % 360
    check("burst amplitude (peak)", bamp, 18, 22, " IRE"); check("burst phase (U=0)", bph, 179, 181, " deg")
    names = ["gray", "yellow", "cyan", "green", "magenta", "red", "blue"]
    exp = {"gray": (76.9, 0, None), "yellow": (69.0, 62, 167.1), "cyan": (56.1, 88, 283.5),
           "green": (48.4, 82, 240.7), "magenta": (36.2, 82, 60.7), "red": (28.6, 88, 103.5),
           "blue": (15.6, 62, 347.1)}
    a0, w = 9.4 * US, (52.6 * US) / 7
    for i, nm in enumerate(names):
        c = int(a0 + (i + 0.5) * w) & ~3
        y, u, v = demod(x, c - 12, 24, idx)
        amp = 2 * math.hypot(u, v)
        hue = (math.degrees(math.atan2(v, u)) - bph + 180) % 360  # relative to burst at 180
        ey, ea, eh = exp[nm]
        line = f"  {nm:8s} Y={y:6.1f} (exp {ey:5.1f})  chroma p-p={amp:5.1f} (exp {ea:3d})"
        good = abs(y - ey) < 2.5 and abs(amp - ea) < 4
        if eh is not None:
            dh = (hue - eh + 180) % 360 - 180; line += f"  hue={hue:6.1f} (exp {eh:5.1f})"; good &= abs(dh) < 3
        ok &= good; print(("  PASS" if good else "  FAIL") + line[1:])

print("vertical interval:")
for idx, exp in [(0, [(0, 2.3), (31.8, 2.3)]), (3, [(0, 27.1), (31.8, 27.1)]),
                 (9, [(0, 4.7)]), (262, [(0, 4.7), (31.8, 2.3)]), (265, [(0, 2.3), (31.8, 27.1)])]:
    p = pulses(ire(dump(s, idx)))
    good = len(p) == len(exp) and all(abs(a - ea) < 0.15 and abs(w_ - ew) < 0.15 for (a, w_), (ea, ew) in zip(p, exp))
    ok &= good
    print(f"  {'PASS' if good else 'FAIL'} line {idx+1}: pulses (start us, width us) "
          f"{[(round(a, 2), round(w_, 2)) for a, w_ in p]}  expect {exp}")
print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
