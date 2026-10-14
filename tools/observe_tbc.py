"""Observe what the TBC did with a test, from a run's CH1 capture.

usage: observe_tbc.py RUN_DIR [RUN_DIR ...] [--ch 1|2] [--html [PATH]]

Reports output H stability, field lengths (in lines) and the frame-marker
sequence decoded from the video. When the run also captured CH2 (the
generator's output, teed off before the TBC), the CH1 report adds an input
check, the TBC's latency, and its gain against the real input. This is an observation tool, not the
grader: it does not score anything. analyze() returns the same results as
data (used by run_test.py, run_suite.py and html_report.py).
"""
import argparse, json, os
from collections import Counter

import numpy as np

import perturb_model as pm


def has_channel(run, ch):
    return f"ch{ch}" in np.load(os.path.join(run, "capture.npz")).files


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


FSC = 315e6 / 88


def picture_metrics(x, hs, fs, tip, blank):
    """Burst, hue, saturation, levels and noise on the output's colour-bar lines (IRE).

    For the C and L tests: what the TBC did to colour and level. IRE are
    scaled from the output's own sync (40 IRE), so an unterminated chain
    (double amplitude) doesn't skew them.
    """
    us = fs / 1e6
    ire = lambda v: (v - blank) * 40.0 / (blank - tip)
    bar = lambda b, a=0.3, z=0.7: (9.4 + 52.6 * (b + a) / 7, 9.4 + 52.6 * (b + z) / 7)

    def demod(s, t0, t1):
        i0, i1 = int(s + t0 * us), int(s + t1 * us)
        seg = x[i0:i1]
        lo = np.exp(-2j * np.pi * FSC * (np.arange(i0, i1) - s) / fs)
        return float(ire(seg.mean())), 2 * np.mean((seg - seg.mean()) * lo) * 40.0 / (blank - tip)

    rows = []
    for s in hs:
        if int(s + 64 * us) >= len(x) or s < 2 * us:
            continue
        g_y, _ = demod(s, *bar(0))
        b_y, _ = demod(s, *bar(6))
        # Top colour-bar rows: gray high, blue low. Relative, so a TBC with low
        # gain (the TBC-1000's gray sits near 56 IRE) still qualifies.
        if not (g_y > 35 and b_y < 0.5 * g_y and g_y > b_y + 20):
            continue
        _, burst = demod(s, 5.8, 7.6)
        y_y, yel = demod(s, *bar(1))
        i0, i1 = int(s + bar(0, 0.15, 0.85)[0] * us), int(s + bar(0, 0.15, 0.85)[1] * us)
        rows.append((abs(burst) * 2, np.degrees(np.angle(yel) - np.angle(burst)) + 180, abs(yel) * 2, g_y,
                     float(ire(x[int(s + 8.2 * us):int(s + 9.1 * us)].mean())), float(np.std(ire(x[i0:i1])))))
    if len(rows) < 10:
        return None
    a = np.array(rows)
    hue = (a[:, 1] + 180) % 360 - 180          # keep the wrap away from 167 deg
    hue = (hue - np.median(hue) + 180) % 360 - 180 + np.median(hue)
    return {"lines": len(rows),
            "burst_pp_ire": float(np.median(a[:, 0])),
            "yellow_hue_deg": float(np.median(hue) % 360), "yellow_hue_jitter_deg": float(np.std(hue)),
            "yellow_chroma_pp_ire": float(np.median(a[:, 2])),
            "gray_ire": float(np.median(a[:, 3])),
            "blanking_wander_ire": float(np.std(a[:, 4])),
            "gray_noise_ire_rms": float(np.median(a[:, 5]))}


# ---- Per-field classification -----------------------------------------------
LO, HI = 7.5, 70.0   # marker cell levels, IRE


def classify_fields(x, fs, hs, vs, marks, tip, blank, lp, line):
    """One record per complete output field: parity, length, class, frame, ghost."""
    us = fs / 1e6
    ire = lambda v: (v - blank) * 40.0 / (blank - tip)
    cell_t = (10.5 + 1.4 * (np.arange(36) + 0.5)) * us
    by_field = {}
    for m in marks:
        by_field.setdefault(m[4], []).append(m)
    mline = int(np.median([m[3] for m in marks])) if marks else 15
    moff = float(np.median([m[2] for m in marks])) * us if marks else 0.0

    def cells_at(s, off):
        """Marker cells scaled 0 (low) .. 1 (high) by this field's own framing
        cells (start "10", stop "01"), so a TBC's gain doesn't matter."""
        pos = (s + cell_t + off).astype(int)
        if pos.max() >= len(lp):
            return None
        c = ire(lp[pos])
        hi, lo = (c[0] + c[35]) / 2, (c[1] + c[34]) / 2
        if hi - lo < 15:                              # no marker-like swing here
            return None
        return (c - lo) / (hi - lo)

    def mid_cells(c):
        return int(np.sum((c[2:34] > 0.25) & (c[2:34] < 0.75)))

    # Reference picture: the median luma profile of our own (marked) fields at
    # a few heights. An unmarked field is "ours, unmarked" only if it matches;
    # anything else with picture in it is foreign (e.g. the TBC's own bars).
    QS = (0.3, 0.5, 0.7)
    prof_t = np.arange(12, 60, 1.0) * us

    def profile(field, q):
        k = int(q * len(field))
        return ire(lp[(field[k] + prof_t).astype(int)]) if k < len(field) else None

    refs = {q: [] for q in QS}
    for fi in by_field:
        if fi + 1 < len(vs):
            fld = hs[(hs > vs[fi]) & (hs < vs[fi + 1])]
            for q in QS:
                p_ = profile(fld, q)
                if p_ is not None and int(p_.max()) < 200:
                    refs[q].append(p_)
    ref = {q: np.median(v, axis=0) for q, v in refs.items() if v}

    log = []
    for fi in range(len(vs) - 1):
        v0, v1 = vs[fi], vs[fi + 1]
        field = hs[(hs > v0) & (hs < v1)]
        before = hs[hs < v0]
        frac = ((v0 - before[-1]) / line) % 1.0 if len(before) else 0.0
        rec = {"i": fi, "parity": 1 if (frac < 0.25 or frac > 0.75) else 2,
               "lines": round(float((v1 - v0) / line) * 2) / 2, "class": "", "frame": None, "ghost": False,
               "t_s": round(float(v0 / fs), 7)}
        rec["standard"] = rec["lines"] == 262.5
        if fi in by_field:
            m = by_field[fi][0]
            rec["class"], rec["frame"] = "ours", int(m[1][2])
            c = cells_at(field[m[3] - 1], m[2] * us) if m[3] - 1 < len(field) else None
            rec["ghost"] = bool(c is not None and mid_cells(c) >= 3)
        else:
            c = cells_at(field[mline - 1], moff) if mline - 1 < len(field) else None
            if c is not None and c[0] > 0.6 and c[1] < 0.4 and mid_cells(c) >= 3:
                rec["class"], rec["ghost"] = "blend", True    # our marker, two frames mixed
            else:
                kinds, others = [], []
                for q in QS:
                    p_ = profile(field, q)
                    if p_ is None:
                        continue
                    if p_.std() < 3 and -5 < p_.mean() < 15:
                        kinds.append("blank")
                    elif q in ref and np.sqrt(np.mean((p_ - ref[q]) ** 2)) < 8:
                        kinds.append("ours")
                    else:
                        kinds.append("other")
                        others.append(p_)
                if "other" in kinds or not kinds:
                    # Not our picture. A TBC's own fallback pattern (e.g. the
                    # AVT-8710's bars) is the same at every height; a garbled
                    # field (seen while a TBC re-acquires) is not.
                    same = len(others) >= 2 and all(np.sqrt(np.mean((a - others[0]) ** 2)) < 5 for a in others[1:])
                    rec["class"] = "fallback" if same and len(others) == len(kinds) else "garbled"
                else:
                    rec["class"] = "unmarked" if "ours" in kinds else "blank"
        log.append(rec)
    return log, mline


def summarize_fields(log):
    """Counts by class, and frames whose two fields disagree (a stuck or torn field)."""
    counts = Counter(f["class"] for f in log)
    mismatches = []
    for a, b in zip(log, log[1:]):   # an output frame: a parity-1 field then a parity-2 field
        if a["parity"] == 1 and b["parity"] == 2 and a["class"] == b["class"] == "ours" and a["frame"] != b["frame"]:
            mismatches.append((a["frame"], b["frame"]))
    return {"ours": counts.get("ours", 0), "unmarked": counts.get("unmarked", 0),
            "blend": counts.get("blend", 0), "blank": counts.get("blank", 0),
            "fallback": counts.get("fallback", 0), "garbled": counts.get("garbled", 0),
            "ghost": sum(f["ghost"] for f in log),
            "nonstandard": sum(not f["standard"] for f in log), "field_mismatches": mismatches}


def loss_summary(test, log):
    """S10/S11: did the TBC ride through N lost fields? Needs a gap inside the capture."""
    n = int(test["params"]["fields"])
    lost_frames = {gf // 2 for gf in range(0, 4000) if gf % pm.LOSS_PERIOD < n}
    seen = [f["frame"] for f in log if f["class"] == "ours"]
    covered = bool(seen) and any(min(seen) < lf < max(seen) for lf in lost_frames)
    s = summarize_fields(log)
    # Handled = output kept standard fields and never fell back to its own
    # pattern. Clean = handled, and nothing but our picture (held) or black.
    held = frame_events(seen)["repeats"]
    handled = covered and s["fallback"] == 0 and s["nonstandard"] == 0
    clean = handled and s["garbled"] == 0 and s["blend"] == 0
    how = ("fell back to its own pattern" if s["fallback"] else "lost sync" if s["nonstandard"] else
           "garbled on recovery" if s["garbled"] else "blanked" if s["blank"] else
           "held last frame" if held else "passed")
    return {"fields_lost": n, "gap_captured": covered, "handled": handled, "clean": clean, "how": how,
            "blank_fields": s["blank"], "fallback_fields": s["fallback"], "garbled_fields": s["garbled"],
            "blended_fields": s["blend"], "unmarked_fields": s["unmarked"], "nonstandard_fields": s["nonstandard"],
            "stuck_or_torn": len(s["field_mismatches"])}


def hunt_summary(log):
    """X1: anomalies before, during and after each stress burst (by input frame).

    A field without our marker belongs to the stress window if either
    neighbouring marked field does (losses only happen under stress); the
    first frames after stress are 'settling' (the TBC's frame store lags)."""
    W = lambda fr: pm.hunt_window(fr, settle=True)
    marked = [(k, f["frame"]) for k, f in enumerate(log) if f["class"] == "ours"]
    win = {}
    for k, f in enumerate(log):
        if f["class"] == "ours":
            w = W(f["frame"])
        else:
            prev = next((W(fr) for j, fr in reversed(marked) if j < k), None)
            nxt = next((W(fr) for j, fr in marked if j > k), None)
            w = "stress" if "stress" in (prev, nxt) else prev or nxt or "before"
        d = win.setdefault(w, {"fields": 0, "not_ours": 0, "ghost": 0, "mismatch": 0, "back": 0, "repeat": 0})
        d["fields"] += 1
        d["not_ours"] += f["class"] not in ("ours", "unmarked")
        d["ghost"] += f["ghost"]
    ours = [f for f in log if f["class"] == "ours"]
    for a, b in zip(ours, ours[1:]):
        d = win.get(W(b["frame"]))
        if d is None:
            continue
        if b["frame"] < a["frame"]:
            d["back"] += 1
        elif b["frame"] == a["frame"] and not (a["parity"] == 1 and b["parity"] == 2):
            d["repeat"] += 1          # same frame again, not as its own second field
    for a, b in zip(log, log[1:]):
        if a["parity"] == 1 and b["parity"] == 2 and a["class"] == b["class"] == "ours" and a["frame"] != b["frame"]:
            win[W(b["frame"])]["mismatch"] += 1
    bad = lambda d: d["not_ours"] + d["ghost"] + d["mismatch"] + d["back"] + d["repeat"]
    after = win.get("after")
    return {"windows": win,
            "after_clean": None if after is None else bad(after) == 0,
            "stuck_or_ghost": sum(d["mismatch"] + d["ghost"] for d in win.values())}


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
           "dma_restarts_during": man.get("capture", {}).get("dma_restarts_during", 0),
           "started_unix": man.get("capture", {}).get("started_unix")}

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
    # No vertical sync at all: the TBC isn't outputting video (seen on a
    # Datavideo TBC-1000, which went dead for about a minute after input losses).
    out["dead"] = len(vs) < 2
    # Output level in volts, to spot the swing sagging before the output dies.
    out["level"] = {"ptp_v": float(np.ptp(x)), "sync_depth_v": None}
    if len(hs) > 100:
        tips = [x[int(s + 1.5 * us):int(s + 3.5 * us)].mean() for s in hs[:2000]]
        porch = [x[int(s + 8.2 * us):int(s + 9.1 * us)].mean() for s in hs[:2000]]
        out["level"]["sync_depth_v"] = float(np.median(porch) - np.median(tips))

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
                        marks.append((float(s / fs), d, float(shifts[j] / us), line_no, fi))
                        break
                    if d:
                        spurious += 1
    # Whose picture is each output field: ours (our marker), blended, blank,
    # or foreign (a picture without our marker, e.g. the TBC's own fallback
    # bars)? Only our own fields feed the picture measurements, so a TBC that
    # gives up and shows its own bars can never pass as our signal.
    if len(hs) > 100:
        fl, mlines = classify_fields(x, fs, hs, vs, marks, tip, blank, lp, line)
        out["field_log"] = fl
        out["field_summary"] = summarize_fields(fl)
        ours = {f["i"] for f in fl if f["class"] == "ours" and not f["ghost"]}
        fidx = np.searchsorted(vs, hs) - 1
        out["picture"] = picture_metrics(x, hs[np.isin(fidx, list(ours))], fs, tip, blank) if ours else None
        tid = man["test"]["id"]
        if tid in ("S10", "S11"):
            out["loss"] = loss_summary(man["test"], fl)
        if tid == "X1":
            out["hunt"] = hunt_summary(fl)
    else:
        out["field_log"], out["field_summary"], out["picture"] = [], None, None
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
    if r.get("dead"):
        print("  OUTPUT DEAD: no video sync on the channel (the TBC isn't outputting video)")
    h = r["h"]
    if h["period_ns"] is not None:
        print(f"  H sync: {h['pulses']} pulses, period {h['period_ns']:.2f} ns, jitter {h['jitter_ns_rms']:.2f} ns rms, "
              f"{h['irregular_intervals']} irregular intervals")
    fl = r["fields"]
    print(f"  fields: {fl['count']} V-syncs, lengths (lines) {fl['lengths']}")
    pc = r.get("picture")
    if pc:
        print(f"  picture: burst {pc['burst_pp_ire']:.1f} IRE p-p, yellow hue {pc['yellow_hue_deg']:.1f} deg "
              f"(±{pc['yellow_hue_jitter_deg']:.1f}), yellow chroma {pc['yellow_chroma_pp_ire']:.1f} IRE p-p, "
              f"gray {pc['gray_ire']:.1f} IRE, blanking wander {pc['blanking_wander_ire']:.2f} IRE rms, "
              f"noise {pc['gray_noise_ire_rms']:.2f} IRE rms")
    fs_ = r.get("field_summary")
    if fs_:
        print(f"  output fields: {fs_['ours']} ours, {fs_['unmarked']} ours unmarked, {fs_['blend']} blended, "
              f"{fs_['blank']} blank, {fs_['garbled']} garbled, {fs_['fallback']} TBC's own pattern, {fs_['ghost']} ghosted, "
              f"{len(fs_['field_mismatches'])} frames with mismatched fields")
    if r.get("loss"):
        L = r["loss"]
        verdict = ("no gap in capture" if not L["gap_captured"] else
                   f"{'clean' if L['clean'] else 'handled' if L['handled'] else 'NOT handled'} ({L['how']})")
        print(f"  lost {L['fields_lost']} field(s): {verdict} (blank {L['blank_fields']}, garbled {L['garbled_fields']}, "
              f"own pattern {L['fallback_fields']}, non-standard {L['nonstandard_fields']}, stuck/torn {L['stuck_or_torn']})")
    if r.get("hunt"):
        H = r["hunt"]
        for w in ("before", "stress", "settling", "after"):
            d = H["windows"].get(w)
            if d:
                print(f"  {w:8s}: {d['fields']} fields, not ours {d['not_ours']}, ghosted {d['ghost']}, "
                      f"stuck/torn {d['mismatch']}, back {d['back']}, repeated {d['repeat']}")
        print(f"  after stress: {'clean' if H['after_clean'] else 'NOT clean' if H['after_clean'] is False else 'not captured'}")
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


def compare_input(out, inp):
    """What CH2 (the generator's output) says about a CH1 run: was the input
    right, how late is the TBC's output, and what did it do to the levels."""
    tid = out["test"]["id"]
    res = {"present": not inp["dead"], "ok": True, "issues": [], "markers": inp["markers"]["decoded"],
           "h_jitter_ns_rms": inp["h"]["jitter_ns_rms"], "fields": inp["fields"]["lengths"],
           "level": inp["level"], "picture": inp["picture"]}
    if tid == "V1":                     # no signal by design: CH2 should be dead
        res["ok"] = inp["dead"]
        if not inp["dead"]:
            res["issues"].append("V1 should send no signal, but CH2 has video")
        return res
    if inp["dead"]:
        res["ok"] = False
        res["issues"].append("no video on CH2 (reference not connected, or the generator stopped)")
        return res
    ev = inp["markers"]["events"]
    if not inp["markers"]["decoded"]:
        res["issues"].append("no frame markers decoded on CH2")
    if ev["backward_steps"] or ev["repeats"]:
        res["issues"].append(f"generator frame sequence broken ({ev['backward_steps']} back, {ev['repeats']} repeated)")
    if out.get("dma_restarts_during"):
        res["issues"].append("generator DMA restarted during the capture")
    res["ok"] = not res["issues"]

    # Latency: the same field (frame and parity) found on both channels.
    def starts(r):
        d = {}
        for f in r.get("field_log", []):
            if f["class"] == "ours" and f["frame"] is not None:
                d.setdefault((f["frame"], f["parity"]), f["t_s"])
        return d
    a, b = starts(out), starts(inp)
    dl = np.array([a[k] - b[k] for k in a.keys() & b.keys()]) * 1e3
    if len(dl):
        med = float(np.median(dl))
        res["latency"] = {"median_ms": med, "fields": med / pm.FIELD_MS, "matched": int(len(dl)),
                          "steady": float(np.mean(np.abs(dl - med) < 0.05)),
                          "min_ms": float(dl.min()), "max_ms": float(dl.max())}

    # Gain against the real input rather than nominal levels: the generator's
    # output (and its chroma roll-off) is whatever CH2 measured.
    po, pi = out.get("picture"), inp.get("picture")
    if po and pi:
        ratio = lambda k: po[k] / pi[k] if pi[k] else None
        res["vs_input"] = {"burst": ratio("burst_pp_ire"), "chroma": ratio("yellow_chroma_pp_ire"),
                           "gray": ratio("gray_ire"), "hue_deg": po["yellow_hue_deg"] - pi["yellow_hue_deg"]}
    # Output line rate against the input's: 0 if the TBC locks its output to the
    # input; otherwise it free-runs (a frame synchroniser) and the latency
    # slides, dropping or repeating a frame each time it wraps.
    po_ns, pi_ns = out["h"]["period_ns"], inp["h"]["period_ns"]
    if po_ns and pi_ns:
        res["rate_ppm"] = (pi_ns / po_ns - 1) * 1e6
    lo, li = out.get("level", {}).get("sync_depth_v"), inp["level"].get("sync_depth_v")
    if lo and li:
        res["sync_ratio"] = lo / li     # depends on how each run is terminated
    return res


def print_input(r):
    i = r.get("input")
    if i is None:
        print("  input: CH2 not captured (single channel); picture figures are against nominal levels")
        return
    if not i["present"] and r["test"]["id"] != "V1":
        print("  input (CH2): no video. Is the reference connected? (--channels 1 for a single-channel rig)")
        return
    state = "ok" if i["ok"] else "CHECK: " + "; ".join(i["issues"])
    extra = ""
    if i["present"]:
        extra = f", {i['markers']} markers, H jitter {i['h_jitter_ns_rms']:.1f} ns rms" if i["h_jitter_ns_rms"] is not None else ""
    print(f"  input (CH2): {state}{extra}")
    L = i.get("latency")
    rate = i.get("rate_ppm")
    lock = (f"; output line rate {rate:+.1f} ppm vs input"
            + (" (locked)" if abs(rate) < 2 else " (free-running: latency drifts)")) if rate is not None else ""
    if L:
        print(f"  latency: {L['median_ms']:.3f} ms ({L['fields']:.2f} fields), {L['matched']} fields matched, "
              f"{100 * L['steady']:.0f} % within 0.05 ms{lock}")
    v = i.get("vs_input")
    if v:
        f = lambda x: f"x{x:.2f}" if x is not None else "—"
        print(f"  vs input: burst {f(v['burst'])}, yellow chroma {f(v['chroma'])}, gray {f(v['gray'])}, "
              f"hue {v['hue_deg']:+.1f} deg")


def report(run, ch=1):
    """Analyze a run, print its report, and return the results. A CH1 report
    also analyzes CH2 when the run captured it (see compare_input)."""
    r = analyze(run, ch)
    if ch == 1 and has_channel(run, 2):
        inp = analyze(run, 2)
        r["input"] = compare_input(r, inp)
    print_report(r)
    if ch == 1:
        print_input(r)
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
