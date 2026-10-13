"""Verify the generator's per-line timing and frame marker against perturb_model.

For each test: freeze the ring, then check
  1. the firmware's planned shifts (pdump) against the Python model,
  2. every descriptor length in one frame against the model,
  3. the sync position inside sampled line buffers (sub-sample placement),
  4. the frame marker lines decode to (test, rung, frame),
  5. line counts and structure for the S tests (dropped fields, 240p, ...),
  6. the colour carrier: burst phase against a continuous 4fsc reference is
     180 deg on every line in colour-under mode, and rotates with the line's
     shift in direct-colour ("follows") mode.
"""
import json, sys, time
import numpy as np
import perturb_model as pm
from gen_cmd import dump_line, open_gen, request

US = pm.SAMPLE_HZ / 1e6
ORIGIN = 16.0  # LINE_ORIGIN in src/ntsc_timing.h; main() reads it from the generator
# (test, rung, seed, chroma mode, ring half to inspect)
TESTS = [("V0", 1, 1, "under", 0), ("H1", 4, 7, "under", 0), ("H1", 6, 3, "under", 0), ("H2", 6, 1, "under", 0),
         ("H3", 3, 1, "under", 0), ("H4", 3, 1, "under", 0), ("H5", 1, 1, "under", 0), ("H5", 8, 1, "under", 0),
         ("H1", 6, 3, "follows", 0),
         ("S3", 3, 1, "under", 0), ("S5", 1, 1, "under", 0), ("S6", 1, 1, "under", 0), ("S6", 4, 1, "under", 1),
         ("S7", 5, 1, "under", 1), ("S7", 5, 1, "under", 0),
         ("S4", 1, 1, "under", 0), ("S4", 2, 1, "under", 0), ("S4", 3, 1, "under", 0), ("S4", 5, 1, "under", 0),
         ("S4", 5, 1, "under", 1), ("S4", 6, 1, "under", 0),
         ("S8", 5, 1, "under", 0), ("S8", 5, 1, "under", 1), ("S9", 5, 1, "under", 0),
         ("S1", 3, 1, "under", 0), ("S1", 5, 1, "under", 0), ("S2", 3, 1, "under", 0),
         ("C1", 3, 1, "under", 0), ("C1", 4, 1, "under", 0), ("C2", 1, 1, "under", 0), ("C3", 4, 5, "under", 0),
         ("C4", 4, 1, "under", 0), ("F2", 7, 1, "under", 0), ("F2", 8, 1, "under", 1), ("F3", 3, 1, "under", 0),
         ("L1", 5, 3, "under", 0), ("L2", 4, 1, "under", 0), ("L3", 4, 1, "under", 0), ("L4", 1, 1, "under", 0),
         ("L4", 4, 1, "under", 0),
         # (..., start frame): begin just before a loss or stress window
         ("V1", 1, 1, "under", 0, 0), ("S10", 11, 1, "under", 0, 26), ("S11", 11, 1, "under", 0, 26),
         ("X1", 2, 1, "under", 0, 35), ("X1", 2, 1, "under", 1, 35), ("X1", 3, 1, "under", 0, 35),
         ("X1", 5, 1, "under", 0, 35)]

# S4: expected sync pulses (start us, width us) on a vertical-interval line, by variant,
# when the frame alters it; "low" = low for the whole line (unserrated, continuing).
VBI_EXPECT = {
    (1, 2): [(0, 4.7)],                      # line 2: EQ pair -> one plain H sync
    (2, 5): "low",                           # line 5: unserrated broad pulses, joined
    (3, 2): [(0, 4.7)], (3, 5): "low",
    (4, 5): [(0, 2.3), (31.8, 2.3)],         # line 5: V-sync missing -> EQ pulses
    (5, 5): [(0, 2.3), (31.8, 2.3)],
    (6, 5): [(0, 2.3), (31.8, 2.3)],
}
NORMAL_VS = [(0, 27.1), (31.8, 27.1)]
HALF_BASE = 600


class Gen:
    def __init__(self):
        self.s = open_gen()

    def cmd(self, c, until="\n", timeout=15.0):
        return request(self.s, c, until=until, timeout=timeout)

    def dump(self, idx):
        return dump_line(self.s, idx)


def sync_pos(codes, blank, tip):
    """Sub-sample 50% crossing of the first falling sync edge."""
    mid = (blank + tip) / 2
    k = int(np.argmax(codes < mid))
    return k - 1 + (codes[k - 1] - mid) / (codes[k - 1] - codes[k])


def pulses_in(codes, mid):
    """(start us, width us) of runs below `mid`, relative to the line's sync origin."""
    b = np.concatenate([[False], codes < mid, [False]])
    d = np.diff(b.astype(int))
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    return [((a - ORIGIN) / US, (e - a) / US) for a, e in zip(starts, ends) if (e - a) / US > 1.0]


def phasor(codes, sync, t0_us, t1_us, start_mod4, e):
    """Subcarrier phase (deg) and peak amplitude (codes) of samples t0..t1 us after
    sync, against the continuous reference sin((pi/2)(start + e + k)), where
    start is the nominal sample count before this line (mod 4)."""
    k = np.arange(int(sync + t0_us * US), int(sync + t1_us * US))
    seg = codes[k] - codes[k].mean()
    th = np.pi / 2 * (k + start_mod4 + e)
    c, s = 2 * np.mean(seg * np.cos(th)), 2 * np.mean(seg * np.sin(th))
    return np.degrees(np.arctan2(c, s)) % 360, float(np.hypot(c, s))


def burst_phase(codes, sync, start_mod4, e):
    return phasor(codes, sync, 5.8, 7.6, start_mod4, e)[0]


def content_check(g, tid, test, man, slots, plan, cum, half, frame, ire, sync_tip_code, blank_code):
    """The quantity each S1/S2/C/L test changes, measured on rendered lines. Returns (ok, note)."""
    p, st = test["params"], pm.base_style(test)
    nsub = man["generator"]["subsample_steps"]
    i = 50  # an active colour-bar line (line 51)
    c = g.dump(HALF_BASE * half + i)
    x = ire(c)
    timing = pm.variant_mode(test) == "timing"
    sp = sync_pos(c, blank_code, sync_tip_code) if timing else ORIGIN
    win = lambda t0, t1: x[int(sp + t0 * US):int(sp + t1 * US)]
    bar = lambda b, a=0.3, z=0.7: (9.4 + 52.6 * (b + a) / 7, 9.4 + 52.6 * (b + z) / 7)  # us window inside bar b
    if tid == "S1":
        depth = win(8.2, 9.1).mean() - win(1.5, 3.5).mean()
        return abs(depth - p["sync_ire"]) < 1.5, f" sync depth {depth:.1f} IRE (want {p['sync_ire']})"
    if tid == "S2":
        k0 = max(0, int(sp - 1.5 * US))
        seg = x[k0:k0 + int(3 * US)]
        hi, lo = win(8.2, 9.1).mean(), win(1.5, 3.5).mean()
        t10 = np.argmax(seg < hi - 0.1 * (hi - lo)); t90 = np.argmax(seg < hi - 0.9 * (hi - lo))
        rise = (t90 - t10) * pm.SAMPLE_NS
        return abs(rise - p["rise_ns"]) < max(0.2 * p["rise_ns"], 80), f" sync fall {rise:.0f} ns (want {p['rise_ns']})"
    if tid in ("C1", "C2"):
        amp = phasor(c, sp, 5.8, 7.6, cum[i], 0)[1] * 140 / (man["generator"]["levels"]["white_code"] - sync_tip_code0(man))
        want = 2 * st["burst_amp_ire"]
        return abs(2 * amp - want) < 2.0, f" burst {2 * amp:.1f} IRE p-p (want {want:g})"
    if tid == "C4":
        b_ph = phasor(c, sp, 5.8, 7.6, cum[i], 0)[0]
        y0, y1 = bar(1)
        hue = (phasor(c, sp, y0, y1, cum[i], 0)[0] - b_ph + 180) % 360
        want = (167.1 + p["deg"]) % 360
        return abs((hue - want + 180) % 360 - 180) < 3, f" yellow hue {hue:.1f} deg (want {want:.1f})"
    if tid == "L4":
        y0, y1 = bar(0)
        y = win(y0, y1).mean()
        want = 76.9 * st["video_gain"]
        return abs(y - want) < 2.0, f" gray bar {y:.1f} IRE (want {want:.1f})"
    if tid == "L2":
        # On the flat gray bar: the sync tip sits at DAC code 0, which clips the noise's negative half.
        y0, y1 = bar(0, 0.15, 0.85)
        rms = win(y0, y1).std()
        want = (st["noise_rms_ire"] ** 2 + 0.55 ** 2) ** 0.5  # plus the 8-bit DAC's own quantization
        return abs(rms - want) < 0.3 * want + 0.3, f" noise {rms:.2f} IRE rms on the gray bar (want ~{want:.2f})"
    if tid == "L3":
        notes, ok = [], True
        for j in (50, 120, 200, 350):
            cj = g.dump(HALF_BASE * half + j)
            xj = ire(cj)
            dc = xj[int(ORIGIN + 8.2 * US):int(ORIGIN + 9.1 * US)].mean()
            want = pm.variant_value(test, plan[j][1], nsub)
            ok &= abs(dc - want) < 1.2
            notes.append(f"{dc:+.1f}/{want:+.1f}")
        return ok, f" blanking vs model (IRE): {' '.join(notes)}"
    if tid == "L1":
        drop = [(j, f) for j, (_, f) in enumerate(slots) if f & pm.SLOT_DROPOUT]
        j, f = drop[0]
        var = (f >> 4) & 3
        cj = g.dump(HALF_BASE * half + j)
        xj = ire(cj)
        t0, t1 = pm.dropout_span_us(test, var)
        ks = np.arange(int(round(ORIGIN + t0 * US)) + 1, int(round(ORIGIN + t1 * US)) - 1)
        err = np.max(np.abs(xj[ks] - np.array([pm.snow_ire(100 + var, int(k)) for k in ks])))
        return err < 1.5, f" dropout on line {slots[j][0]}: snow vs model max err {err:.2f} IRE, {len(drop)} dropout lines"
    return True, ""


def sync_tip_code0(man):
    return man["generator"]["levels"]["sync_code"]


def main():
    global ORIGIN
    g = Gen()
    ORIGIN = float(json.loads(next(l for l in g.cmd("info", until="}\n").splitlines()
                                   if l.startswith("{")))["line_origin_samples"])
    g.cmd("hold 0", until="frames")  # in case an earlier run stopped while frozen
    ok_all = True
    only = [a.upper() for a in sys.argv[1:]]  # e.g. verify_timing.py S8 S9
    for tid, rung, seed, mode, half, *rest in TESTS:
        start = rest[0] if rest else 0
        if only and tid not in only:
            continue
        assert g.cmd(f"chroma {mode}").strip().endswith("ok"), mode
        assert g.cmd(f"test {tid} {rung} {seed} {start}").strip().endswith("ok"), tid
        time.sleep(0.2)  # let a few frames go by after the rebuild
        fr = g.cmd("hold 1", until="frames").split("frames")[1].split()
        frame = int(fr[half])
        order_ok = abs(int(fr[0]) - int(fr[1])) == 1  # halves hold consecutive frames
        man = json.loads(next(l for l in g.cmd("manifest", until="}}\n").splitlines() if l.startswith("{")))
        test, nsub = man["test"], man["generator"]["subsample_steps"]
        lv = man["generator"]["levels"]
        cap = 910 + man["generator"]["max_ext_samples"]

        # 1. planned shifts / copies: firmware against the model. Float trig on the
        # ESP32 and double in Python can round differently right at a bin edge,
        # so a couple of lines per frame may legitimately pick the neighbouring bin.
        slots = pm.frame_slots(test, frame)
        rows = [l.split() for l in g.cmd(f"pdump {frame} 0 {len(slots)}", until="end").splitlines() if l and l[0].isdigit()]
        nxt = [l.split() for l in g.cmd(f"pdump {frame + 1} 0 1", until="end").splitlines() if l and l[0].isdigit()]
        timing = pm.variant_mode(test) == "timing"
        tol = lambda m: 0.02 * max(1.0, test["params"].get("peak_ns", 1)) + 1e-6 * abs(m)
        tgt_err = max(abs(float(r[2]) - pm.shift_ns(test, frame * 525 + int(r[1]) - 1)) / tol(float(r[2])) for r in rows)
        # firmware rounding of its own target (F2 rounds exact integers instead: checked against the model)
        self_bad = sum((int(r[3]), int(r[4])) != pm.quantize(float(r[2]), nsub) for r in rows) \
            if timing and tid != "F2" else 0
        model_bad = sum((int(r[3]), int(r[4])) != pm.plan(test, frame * 525 + int(r[1]) - 1, nsub) for r in rows)
        ev_bad = self_bad + (model_bad if model_bad > 2 else 0)

        # 2. line count and every descriptor length in this frame
        lens = [int(x) for x in g.cmd(f"lens {half}", until="end").replace("end", "").split()]
        plan = [(int(r[3]), int(r[4])) for r in rows] + [(int(nxt[0][3]), int(nxt[0][4]))]
        exp = [min(max(pm.nominal_samples(f) + plan[i + 1][0] - plan[i][0], 64), cap) for i, (_, f) in enumerate(slots)]
        len_bad = sum(a != b for a, b in zip(lens, exp)) + abs(len(lens) - len(exp))

        # 3. sync placement and subcarrier continuity on sampled lines
        scale = (lv["white_code"] - lv["sync_code"]) / 140
        blank_code = lv["sync_code"] + 40 * scale
        tip_code = blank_code + pm.base_style(test)["sync_ire"] * scale   # S1 makes sync shallower
        ire = lambda c: (c - lv["sync_code"]) / scale - 40
        cum = [pm.frame_start_mod4(test, frame)]
        for _, f in slots:
            cum.append((cum[-1] + pm.nominal_samples(f)) % 4)
        pos_err, ph_err, nosync_ok, checked = [], [], True, []
        for i in (21, 50, 99, 100, 101, 255, 256, 300, 400):
            if i >= len(slots):
                continue
            if slots[i][1] & pm.SLOT_NOISE:  # snow: no sync or burst to check
                continue
            if pm.line_halves(slots[i][0])[0] not in ("BLANK", "ACTIVE"):  # VBI line: no H sync or burst
                continue
            if slots[i][1] & pm.SLOT_DEAD:  # no signal: nothing to check here (checked below)
                continue
            c = g.dump(HALF_BASE * half + i)
            e, v = plan[i]
            sub_v = v * 2.0 / nsub if timing else 0.0   # copies only shift timing in timing mode
            if not timing:  # C3/L2/L3 never move sync: it sits at the origin (noise and hum upset detection)
                sp = ORIGIN
            elif slots[i][1] & pm.SLOT_ALT and tid == "S3":  # no sync pulse: nothing below blanking in the first 10 us
                nosync_ok &= c[:int(10 * US)].min() > tip_code + 0.25 * (blank_code - tip_code)  # burst dips to -20 IRE
                sp = ORIGIN + sub_v
            else:
                sp = sync_pos(c, blank_code, tip_code)
                pos_err.append(sp - (ORIGIN + sub_v))
            if pm.base_style(test)["burst_amp_ire"] == 0 or tid == "L2":  # C2: no burst; L2: noise scatters its phase
                continue
            if pm.variant_mode(test) == "carrier":         # C3: this line's copy carries a phase offset
                exp_ph = (180.0 - pm.variant_value(test, v, nsub)) % 360
            else:
                exp_ph = 180.0 if mode == "under" else (180.0 - 90.0 * (e + sub_v)) % 360
            ph_err.append((burst_phase(c, sp, cum[i], e) - exp_ph + 180) % 360 - 180)
            checked.append(i)
        pos_err, ph_err = np.array(pos_err), np.array(ph_err)

        # S4: the vertical-interval sync shapes; S9: snow lines carry the snow
        # rendering the model names, with no sync.
        struct_ok, struct_note = True, ""
        if tid == "S4":
            for L in (2, 5):
                exp = VBI_EXPECT.get((rung, L))
                if exp is None:
                    continue
                c = g.dump(HALF_BASE * half + L - 1)
                altered = bool(slots[L - 1][1] & pm.SLOT_ALT)
                if not altered:
                    exp = NORMAL_VS if L == 5 else [(0, 2.3), (31.8, 2.3)]
                mid = (blank_code + tip_code) / 2
                if exp == "low":
                    got = f"{np.mean(c < mid):.0%} low"
                    good_l = np.mean(c < mid) > 0.97
                else:
                    pl = pulses_in(c, mid)
                    got = [(round(a, 1), round(w, 1)) for a, w in pl]
                    good_l = len(pl) == len(exp) and all(abs(a - ea) < 0.3 and abs(w - ew) < 0.3
                                                         for (a, w), (ea, ew) in zip(pl, exp))
                struct_ok &= good_l
                struct_note += f" line {L}{' (altered)' if altered else ''}: {got};"
        if tid in ("S1", "S2", "C1", "C2", "C4", "L1", "L2", "L3", "L4"):
            ok_c, note_c = content_check(g, tid, test, man, slots, plan, cum, half, frame, ire, tip_code, blank_code)
            struct_ok &= ok_c
            struct_note += note_c
        # Lost lines: no signal must be DAC code 0 throughout; snow must match the model.
        dead = [i for i, (_, f) in enumerate(slots) if f & pm.SLOT_DEAD]
        if dead:
            c = g.dump(HALF_BASE * half + dead[len(dead) // 2])
            struct_ok &= bool(np.all(c == 0))
            struct_note += f" {len(dead)} no-signal lines (max code {int(c.max())});"
        if tid in ("S11", "X1"):
            snow = [i for i, (_, f) in enumerate(slots) if f & pm.SLOT_NOISE]
            if snow:
                i = snow[0]
                c = g.dump(HALF_BASE * half + i)
                want = np.array([pm.noise_ire(pm.noise_variant(frame, i), k) for k in range(len(c))])
                err = np.max(np.abs(ire(c) - want))
                struct_ok &= err < 1.5
                struct_note += f" {len(snow)} snow lines (vs model {err:.2f} IRE);"
        if tid == "S9":
            bar = [i for i, (_, f) in enumerate(slots) if f & pm.SLOT_NOISE]
            i = bar[0]
            c = g.dump(HALF_BASE * half + i)
            want = np.array([pm.noise_ire(pm.noise_variant(frame, i), k) for k in range(len(c))])
            got_ire = (c - tip_code) * 140 / (lv["white_code"] - lv["sync_code"]) - 40
            err = np.max(np.abs(got_ire - want))
            struct_ok &= err < 1.5  # one DAC code is ~1.9 IRE; rounding lands within half of that
            struct_note += f" snow line {slots[i][0]} vs model: max err {err:.2f} IRE, bar {len(bar)} lines;"

        # 4. frame markers (every slot showing a marker line)
        marks, marker_slots = [], 0
        for i, (L, f) in enumerate(slots):
            if L not in man["marker"]["lines"] or f & (pm.SLOT_DEAD | pm.SLOT_NOISE):  # lost lines carry no marker
                continue
            marker_slots += 1
            c = g.dump(HALF_BASE * half + i)
            st = sync_pos(c, blank_code, tip_code) if timing else ORIGIN  # noise/hum upset sync detection
            lo, hi = lv["sync_code"] + 47.5 * (lv["white_code"] - lv["sync_code"]) / 140, \
                lv["sync_code"] + 110 * (lv["white_code"] - lv["sync_code"]) / 140
            # average each cell over +-0.4 us: one sample can be flipped by L2's noise
            cells = [c[int(round(st + (10.5 + 1.4 * (i + 0.5) - 0.4) * US)):
                       int(round(st + (10.5 + 1.4 * (i + 0.5) + 0.4) * US))].mean() for i in range(36)]
            marks.append(pm.decode_marker([1 if x > (lo + hi) / 2 else 0 for x in cells]))
        g.cmd("hold 0", until="frames")

        ph_max = np.max(np.abs(ph_err)) if len(ph_err) else 0.0
        good = tgt_err < 1.0 and ev_bad == 0 and len_bad == 0 \
            and (len(pos_err) == 0 or np.max(np.abs(pos_err)) < (0.3 if tid == "S2" else 0.15)) \
            and ph_max < 4 and nosync_ok \
            and struct_ok \
            and order_ok and (marks or marker_slots == 0) and all(m == (int(man["test"]["code"]), rung, frame & 0xFFFF) for m in marks)
        ok_all &= good
        if not good and ph_max >= 4:
            print("   burst phase error by slot:", {i: round(float(p_), 1) for i, p_ in zip(checked, ph_err)})
        print(f"{'PASS' if good else 'FAIL'} {tid} r{rung} {mode} (frame {frame}, {len(lens)} lines, nsub {nsub}): "
              f"target err {tgt_err:.3f} of tol, plan vs model {model_bad} bin-edge, e/v bad {ev_bad}, length bad {len_bad}, "
              f"sync pos err {np.max(np.abs(pos_err)) if len(pos_err) else 0:.3f} smp, "
              f"burst phase err {ph_max:.1f} deg, no-sync ok {nosync_ok}, halves {fr[0]}/{fr[1]}, markers {marks}"
              f"{' |' + struct_note if struct_note else ''}")
    g.cmd("chroma under")
    g.cmd("test V0")
    print("ALL PASS" if ok_all else "SOME FAILED")


if __name__ == "__main__":
    main()
