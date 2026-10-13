"""Measure an NTSC capture from dwf_capture.py: levels, line rate, burst, bar colours.

usage: analyze_capture.py CAPTURE.npy [channel(1|2)] [rate_hz]

Assumes the SMPTE bars pattern for the colour table. Hue is measured
relative to the decoded burst (burst = 180 deg), as a vectorscope does.
"""
import sys, math
import numpy as np

path = sys.argv[1]
ch = int(sys.argv[2]) - 1 if len(sys.argv) > 2 else 0
fs = float(sys.argv[3]) if len(sys.argv) > 3 else 100e6
x = np.load(path)[ch]
FSC = 315e6 / 88
US = fs / 1e6

# --- levels & sync edges -------------------------------------------------------
tip = np.percentile(x, 0.5)
top = np.percentile(x, 99.5)
mid = tip + 0.15 * (top - tip)          # well below blanking, above sync tip
below = x < mid
fall = np.where(~below[:-1] & below[1:])[0]
fall_t = fall + (x[fall] - mid) / (x[fall] - x[fall + 1])
d = np.diff(fall_t)
hlines = np.where(np.abs(d / US - 63.556) < 1.0)[0]   # full-line H syncs
per = d[hlines]
line_hz = fs / np.median(per)
# sync tip and blanking (back porch, after burst) from full lines
tip = np.median([x[int(fall_t[k] + 1.5 * US):int(fall_t[k] + 3.5 * US)].mean() for k in hlines])
blank = np.median([x[int(fall_t[k] + 8.2 * US):int(fall_t[k] + 9.1 * US)].mean() for k in hlines])
mid = (tip + blank) / 2
fall_t = fall_t  # (edges found at a lower threshold; fine for period/jitter)
ire_per_v = 40.0 / (blank - tip)
print(f"sync tip {tip*1000:.0f} mV, blanking {blank*1000:.0f} mV -> sync amplitude {(blank-tip)*1000:.0f} mV "
      f"(spec 286 mV into 75 ohm)")
print(f"line rate {line_hz:.3f} Hz (spec 15734.266), period jitter {np.std(per)/US*1000:.2f} ns rms "
      f"over {len(per)} lines")

def ire(v): return (v - blank) * ire_per_v

# --- per-line measurements -----------------------------------------------------
t = np.arange(len(x)) / fs
def demod(i0, i1, ref):
    """Mean level and complex chroma (phase relative to ref sync) over samples i0..i1."""
    seg = x[i0:i1]; tt = (np.arange(i0, i1) - ref) / fs
    lo = np.exp(-2j * np.pi * FSC * tt)
    return seg.mean(), 2 * np.mean((seg - seg.mean()) * lo)

COLORS = [("gray", 76.9, 0, None), ("yellow", 69.0, 62, 167.1), ("cyan", 56.1, 88, 283.5),
          ("green", 48.4, 82, 240.7), ("magenta", 36.2, 82, 60.7), ("red", 28.6, 88, 103.5),
          ("blue", 15.6, 62, 347.1)]
res = {c[0]: [] for c in COLORS}
bursts = []
for k in hlines:
    s0 = fall_t[k]; si = int(s0)
    if si + int(64 * US) >= len(x): break
    _, b = demod(si + int(5.6 * US), si + int(7.6 * US), s0)
    if abs(b) * ire_per_v < 5: continue          # no burst (VBI)
    # is this a top-bars line? gray bar ~77 IRE and blue bar ~16 IRE
    a0, w = 9.4 * US, 52.66 * US / 7
    meas = []
    for i in range(7):
        c0 = si + int(a0 + (i + 0.25) * w); c1 = si + int(a0 + (i + 0.75) * w)
        y, c = demod(c0, c1, s0)
        meas.append((ire(y), 2 * abs(c) * ire_per_v, (math.degrees(np.angle(c) - np.angle(b)) + 180) % 360))
    if not (meas[0][0] > meas[1][0] > meas[2][0] > meas[3][0] and meas[0][1] < 5 and meas[1][1] > 5): continue
    bursts.append(abs(b) * ire_per_v * 2)
    for (nm, *_), m in zip(COLORS, meas): res[nm].append(m)

print(f"burst {np.median(bursts):.1f} IRE p-p (spec 40) over {len(bursts)} bar lines")
print(f"{'bar':8s} {'Y IRE':>13s} {'chroma p-p':>15s} {'hue deg':>17s}")
for nm, ey, ea, eh in COLORS:
    if not res[nm]: print(f"{nm}: no bar lines found"); continue
    m = np.array(res[nm]); y, a = np.median(m[:, 0]), np.median(m[:, 1])
    hs = f"{np.median((m[:, 2] - (eh or 0) + 180) % 360 - 180) + (eh or 0):6.1f} ({eh})" if eh else ""
    print(f"{nm:8s} {y:6.1f} ({ey:5.1f})  {a:6.1f} ({ea:3d})   {hs}")
