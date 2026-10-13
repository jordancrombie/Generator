"""Observe what the TBC did with a test, from a run's CH1 capture.

usage: observe_tbc.py RUN_DIR [RUN_DIR ...] [--ch 1|2] [--html [PATH]]

Reports output H stability, field lengths (in lines) and the frame-marker
sequence decoded from the video. This is an observation tool, not the
grader: it does not score anything. analyze() returns the same results as
data (used by run_test.py, run_suite.py and html_report.py).
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


def frame_events(frames):
    """Classify the frame-counter sequence: each frame should show up twice (two fields)."""
    if len(frames) < 3:
        return {"in_order": False, "backward_steps": 0, "skipped_frames": 0, "repeats": 0, "single_field_frames": 0}
    steps = np.diff(frames)
    inner = Counter(frames[1:-1])
    seen = set(frames)
    return {
        "in_order": bool(np.all(steps >= 0) and np.all(steps <= 1)),
        "backward_steps": int(np.sum(steps < 0)),
        "skipped_frames": sum(1 for f in range(min(frames), max(frames) + 1) if f not in seen),
        "repeats": sum(1 for n in inner.values() if n > 2),
        "single_field_frames": sum(1 for f, n in inner.items() if n == 1 and f not in (frames[0], frames[-1])),
    }


def excerpt(x, hs, us, start_line, n_bins=220):
    """Min/max envelope of one line (2 us before sync to 64 us after) for plotting.

    220 bins over 66 us are each longer than a subcarrier cycle (0.28 us), so
    chroma fills a solid band instead of a ragged one."""
    if start_line >= len(hs):
        return None
    s = int(hs[start_line] - 2 * us)
    seg = x[s:s + int(66 * us)]
    if s < 0 or len(seg) < n_bins:
        return None
    bins = np.array_split(seg, n_bins)
    return {"t0_us": -2.0, "t1_us": 64.0,
            "min": [round(float(b.min()), 4) for b in bins], "max": [round(float(b.max()), 4) for b in bins]}


def analyze(run, ch=1):
    """Results of one run as a dict (JSON-serialisable)."""
    x, fs, man = load(run, ch)
    us = fs / 1e6
    out = {"run": os.path.basename(os.path.normpath(run)), "channel": ch, "seconds": len(x) / fs,
           "test": man["test"], "ideal": man.get("ideal", {}), "dut": man.get("dut", {}),
           "generator": man.get("generator", {}),
           "dma_restarts_during": man.get("capture", {}).get("dma_restarts_during", 0)}

    ft, w = sync_pulses(x, us)
    hs = ft[(w > 3.5) & (w < 6)]                      # horizontal syncs
    per = np.diff(hs) / us
    full = per[np.abs(per - 63.556) < 1.0]
    out["h"] = {"pulses": int(len(hs)),
                "period_ns": float(np.mean(full) * 1000) if len(full) else None,
                "jitter_ns_rms": float(np.std(full) * 1000) if len(full) else None,
                "irregular_intervals": int(np.sum(np.abs(per - 63.556) >= 1.0))}

    # Vertical sync: first broad pulse (> 20 us) after a gap marks a field start.
    broad = ft[w > 20]
    vs = broad[np.concatenate([[True], np.diff(broad) > 100 * us])] if len(broad) else broad
    line = np.median(full) * us if len(full) else 63.556 * us
    fields = np.round(np.diff(vs) / line * 2) / 2
    out["fields"] = {"count": int(len(vs)), "lengths": {f"{k:g}": int(v) for k, v in Counter(fields.tolist()).items()},
                     "all_standard": bool(len(fields) > 0 and np.all(fields == 262.5))}

    # Frame markers: search every line of each output field, trying cell
    # offsets of +-12 us, so a marker is found wherever the TBC put it. Its
    # line number after the output V-sync shows vertical lock (steady) or a
    # rolling/offset picture (varying); its offset shows residual H error.
    # Only markers naming this run's test and rung count: snow (S9) scanned at
    # many offsets occasionally passes the 4-bit CRC by chance.
    want = (int(man["test"]["code"]), int(man["test"]["rung"]))
    marks, spurious = [], 0
    if len(hs) > 100:
        tip = np.median([x[int(s + 1.5 * us):int(s + 3.5 * us)].mean() for s in hs[:2000]])
        blank = np.median([x[int(s + 8.2 * us):int(s + 9.1 * us)].mean() for s in hs[:2000]])
        thr = blank + 38.75 * (blank - tip) / 40.0
        k = max(1, int(0.8 * us))
        lp = np.convolve(x, np.ones(k, np.float32) / k, mode="same")      # suppress chroma
        cell_t = (10.5 + 1.4 * (np.arange(36) + 0.5)) * us
        shifts = np.arange(-12.0, 12.01, 0.1) * us
        order = np.argsort(np.abs(shifts))                                 # prefer the smallest offset
        for fi, v0 in enumerate(vs):
            v1 = vs[fi + 1] if fi + 1 < len(vs) else np.inf
            field = hs[(hs > v0) & (hs < v1)]
            for line_no, s in enumerate(field, 1):
                pos = (s + cell_t[None, :] + shifts[:, None]).astype(int)
                if pos.max() >= len(lp):
                    break
                bits = (lp[pos] > thr).astype(np.int8)
                # start "10" and stop "01" framing, checked for all offsets at once
                cand = (bits[:, 0] == 1) & (bits[:, 1] == 0) & (bits[:, -2] == 0) & (bits[:, -1] == 1)
                if not cand.any():
                    continue
                for j in order[cand[order]]:
                    d = pm.decode_marker(bits[j])
                    if d and d[:2] == want:
                        marks.append((float(s / fs), d, float(shifts[j] / us), line_no))
                        break
                    if d:
                        spurious += 1
    frames = [int(m[1][2]) for m in marks]
    offs = [m[2] for m in marks]
    lines = [m[3] for m in marks]
    out["markers"] = {
        "decoded": len(marks),
        "test_rung": [list(k) for k in Counter(m[1][:2] for m in marks)],
        "frames": frames,
        "offset_us": {"median": float(np.median(offs)), "min": float(min(offs)), "max": float(max(offs))} if offs else None,
        "events": frame_events(frames),
        "spurious_rejected": spurious,
        "line_after_vsync": {"median": float(np.median(lines)), "min": int(min(lines)), "max": int(max(lines)),
                             "values": lines} if lines else None,
    }

    # One line of picture to plot: ~60 lines after the first V-sync (colour bars).
    first = int(np.searchsorted(hs, vs[0])) + 60 if len(vs) else 60
    out["excerpt"] = excerpt(x, hs, us, first)
    return out


def print_report(r):
    t = r["test"]
    dut = r["dut"].get("name") or "unspecified"
    print(f"{t['id']} r{t['rung']} {t['params']} ({t.get('chroma', '')}), DUT {dut}, CH{r['channel']}, {r['seconds']:.3f} s")
    h = r["h"]
    if h["period_ns"] is not None:
        print(f"  H sync: {h['pulses']} pulses, period {h['period_ns']:.2f} ns, jitter {h['jitter_ns_rms']:.2f} ns rms, "
              f"{h['irregular_intervals']} irregular intervals")
    fl = r["fields"]
    print(f"  fields: {fl['count']} V-syncs, lengths (lines) {fl['lengths']}")
    m = r["markers"]
    if not m["decoded"]:
        print("  markers: none decoded")
        return
    o, ev = m["offset_us"], m["events"]
    lv = m["line_after_vsync"]
    r1 = lambda v: round(v, 1) + 0.0  # no "-0.0"
    print(f"  marker position: {r1(o['median']):+.1f} us from output sync (range {r1(o['min']):+.1f}..{r1(o['max']):+.1f}), "
          f"line {lv['median']:.0f} after V-sync (range {lv['min']}..{lv['max']})")
    print(f"  markers: {m['decoded']} decoded, frames {m['frames'][0]}..{m['frames'][-1]}, "
          f"{'in order' if ev['in_order'] else 'out of order'}: {ev['backward_steps']} backward steps, "
          f"{ev['skipped_frames']} skipped frames, {ev['repeats']} repeated, {ev['single_field_frames']} single-field")
    seq = " ".join(str(f) for f in m["frames"][:40])
    print(f"  sequence: {seq}{' ...' if len(m['frames']) > 40 else ''}")


def report(run, ch=1):
    """Analyze a run, print its report, and return the results."""
    r = analyze(run, ch)
    print_report(r)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--ch", type=int, default=1)
    ap.add_argument("--html", nargs="?", const="auto", help="also write an HTML report (optional path)")
    a = ap.parse_args()
    results = [report(r, a.ch) for r in a.runs]
    if a.html:
        import html_report
        print(f"HTML report: {html_report.write(results, None if a.html == 'auto' else a.html)}")


if __name__ == "__main__":
    main()
