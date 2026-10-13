"""Observe what the TBC did with a test, from a run's CH1 capture.

usage: observe_tbc.py RUN_DIR [--ch 1|2]

Reports output H stability, field lengths (in lines) and the frame-marker
sequence decoded from the video. This is an observation tool, not the
grader: it does not score anything.
"""
import argparse, json, os
from collections import Counter

import numpy as np

import perturb_model as pm


def load(run, ch):
    z = np.load(os.path.join(run, "capture.npz"))
    x = z[f"ch{ch}"].astype(np.float32) * np.float32(z["scale"][ch - 1]) + np.float32(z["offset"][ch - 1])
    return x, float(z["rate"]), json.load(open(os.path.join(run, "manifest.json")))


def sync_pulses(x, us):
    """Falling-edge times (samples, sub-sample) and widths (us) of all sync-level pulses > 1.5 us."""
    tip, top = np.percentile(x, 0.5), np.percentile(x, 99.5)
    mid = tip + 0.15 * (top - tip)
    b = x < mid
    f = np.where(~b[:-1] & b[1:])[0]
    r = np.where(b[:-1] & ~b[1:])[0]
    ri = np.searchsorted(r, f)
    ok = ri < len(r)
    f, w = f[ok], (r[ri[ok]] - f[ok]) / us
    keep = w > 1.5
    f, w = f[keep], w[keep]
    return f + (x[f] - mid) / (x[f] - x[f + 1]), w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--ch", type=int, default=1)
    a = ap.parse_args()
    x, fs, man = load(a.run, a.ch)
    us = fs / 1e6
    t = man["test"]
    print(f"{t['id']} r{t['rung']} {t['params']} ({t.get('chroma', '')}), CH{a.ch}, {len(x) / fs:.3f} s")

    ft, w = sync_pulses(x, us)
    hs = ft[(w > 3.5) & (w < 6)]                      # horizontal syncs
    per = np.diff(hs) / us
    full = per[np.abs(per - 63.556) < 1.0]
    print(f"  H sync: {len(hs)} pulses, period {np.mean(full) * 1000:.2f} ns, jitter {np.std(full) * 1000:.2f} ns rms, "
          f"{np.sum(np.abs(per - 63.556) >= 1.0)} irregular intervals")

    # Vertical sync: first broad pulse (> 20 us) after a gap marks a field start.
    broad = ft[w > 20]
    vs = broad[np.concatenate([[True], np.diff(broad) > 100 * us])]
    line = np.median(full) * us
    fields = np.diff(vs) / line
    if len(fields):
        print(f"  fields: {len(vs)} V-syncs, lengths (lines) {Counter(np.round(fields * 2) / 2).most_common()}")

    # Frame markers: on the lines manifest["marker"]["lines"] name. Search
    # the first 30 lines after each V-sync, trying cell offsets of +-12 us so a
    # marker the TBC did not fully re-time is still found (and its offset shown).
    tip = np.median([x[int(s + 1.5 * us):int(s + 3.5 * us)].mean() for s in hs[:2000]])
    blank = np.median([x[int(s + 8.2 * us):int(s + 9.1 * us)].mean() for s in hs[:2000]])
    v_ire = (blank - tip) / 40.0
    thr = blank + 38.75 * v_ire
    k = max(1, int(0.8 * us))
    lp = np.convolve(x, np.ones(k, np.float32) / k, mode="same")      # suppress chroma
    cell_t = (10.5 + 1.4 * (np.arange(36) + 0.5)) * us
    shifts = np.arange(-12.0, 12.01, 0.1) * us
    marks = []
    for v0 in vs:
        for s in hs[hs > v0][:30]:
            pos = (s + cell_t[None, :] + shifts[:, None]).astype(int)
            if pos.max() >= len(lp):
                break
            bits = (lp[pos] > thr).astype(int)
            for j in np.argsort(np.abs(shifts)):  # prefer the smallest offset
                if bits[j, 0] == 1 and bits[j, 1] == 0:
                    d = pm.decode_marker(bits[j])
                    if d:
                        marks.append((s / fs, d, shifts[j] / us))
                        break
    if not marks:
        print("  markers: none decoded (VBI blanked or not captured)")
        return
    codes = Counter((c, r) for _, (c, r, _), _ in marks)
    frames = [f for _, (_, _, f), _ in marks]
    offs = np.array([o for _, _, o in marks])
    print(f"  marker position vs output sync: {np.median(offs):+.1f} us median, range {offs.min():+.1f}..{offs.max():+.1f} us")
    print(f"  markers: {len(marks)} decoded, test/rung {dict(codes)}")
    steps = Counter(np.diff(frames).tolist())
    print(f"  frame counter {frames[0]}..{frames[-1]}, step histogram (0 = same frame's other field) {dict(steps)}")
    seq = " ".join(str(f) for f in frames[:40])
    print(f"  sequence: {seq}{' ...' if len(frames) > 40 else ''}")


if __name__ == "__main__":
    main()
