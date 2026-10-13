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
ORIGIN = 4.0
# (test, rung, seed, chroma mode, ring half to inspect)
TESTS = [("V0", 1, 1, "under", 0), ("H1", 4, 7, "under", 0), ("H1", 6, 3, "under", 0), ("H2", 6, 1, "under", 0),
         ("H3", 3, 1, "under", 0), ("H4", 3, 1, "under", 0), ("H5", 1, 1, "under", 0), ("H5", 8, 1, "under", 0),
         ("H1", 6, 3, "follows", 0),
         ("S3", 3, 1, "under", 0), ("S5", 1, 1, "under", 0), ("S6", 1, 1, "under", 0), ("S6", 4, 1, "under", 1),
         ("S7", 5, 1, "under", 1), ("S7", 5, 1, "under", 0),
         ("S4", 1, 1, "under", 0), ("S4", 2, 1, "under", 0), ("S4", 3, 1, "under", 0), ("S4", 5, 1, "under", 0),
         ("S4", 5, 1, "under", 1), ("S4", 6, 1, "under", 0),
         ("S8", 5, 1, "under", 0), ("S8", 5, 1, "under", 1), ("S9", 5, 1, "under", 0)]

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


def burst_phase(codes, sync, start_mod4, e):
    """Burst phase (deg) against the continuous reference sin((pi/2)(start + e + k)),
    where start is the nominal sample count before this line (mod 4)."""
    k = np.arange(int(sync + 5.8 * US), int(sync + 7.6 * US))
    seg = codes[k] - codes[k].mean()
    th = np.pi / 2 * (k + start_mod4 + e)
    return np.degrees(np.arctan2(np.mean(seg * np.cos(th)), np.mean(seg * np.sin(th)))) % 360


def main():
    g = Gen()
    ok_all = True
    only = [a.upper() for a in sys.argv[1:]]  # e.g. verify_timing.py S8 S9
    for tid, rung, seed, mode, half in TESTS:
        if only and tid not in only:
            continue
        assert g.cmd(f"chroma {mode}").strip().endswith("ok"), mode
        assert g.cmd(f"test {tid} {rung} {seed}").strip().endswith("ok"), tid
        time.sleep(0.2)  # let a few frames go by after the rebuild
        fr = g.cmd("hold 1", until="frames").split("frames")[1].split()
        frame = int(fr[half])
        order_ok = abs(int(fr[0]) - int(fr[1])) == 1  # halves hold consecutive frames
        man = json.loads(next(l for l in g.cmd("manifest", until="}}\n").splitlines() if l.startswith("{")))
        test, nsub = man["test"], man["generator"]["subsample_steps"]
        lv = man["generator"]["levels"]
        cap = 910 + man["generator"]["max_ext_samples"]

        # 1. planned shifts
        rows = [l.split() for l in g.cmd(f"pdump {frame}", until="end").splitlines() if l and l[0].isdigit()]
        tgt_err = max(abs(float(r[2]) - pm.shift_ns(test, frame * 525 + int(r[1]) - 1)) for r in rows)
        ev_bad = sum((int(r[3]), int(r[4])) != pm.quantize(float(r[2]), nsub) for r in rows)

        # 2. line count and every descriptor length in this frame
        slots = pm.frame_slots(test, frame)
        lens = [int(x) for x in g.cmd(f"lens {half}", until="end").replace("end", "").split()]
        plan = [pm.quantize(pm.shift_ns(test, frame * 525 + i), nsub) for i in range(len(slots))]
        plan.append(pm.quantize(pm.shift_ns(test, (frame + 1) * 525), nsub))
        exp = [min(max(pm.nominal_samples(f) + plan[i + 1][0] - plan[i][0], 64), cap) for i, (_, f) in enumerate(slots)]
        len_bad = sum(a != b for a, b in zip(lens, exp)) + abs(len(lens) - len(exp))

        # 3. sync placement and subcarrier continuity on sampled lines
        tip_code = lv["sync_code"]
        blank_code = lv["sync_code"] + 40 * (lv["white_code"] - lv["sync_code"]) / 140
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
            c = g.dump(HALF_BASE * half + i)
            e, v = plan[i]
            if slots[i][1] & pm.SLOT_ALT and tid == "S3":  # no sync pulse: nothing below blanking in the first 10 us
                nosync_ok &= c[:int(10 * US)].min() > tip_code + 0.25 * (blank_code - tip_code)  # burst dips to -20 IRE
                sp = ORIGIN + v * 2.0 / nsub
            else:
                sp = sync_pos(c, blank_code, tip_code)
                pos_err.append(sp - (ORIGIN + v * 2.0 / nsub))
            exp_ph = 180.0 if mode == "under" else (180.0 - 90.0 * (e + v * 2.0 / nsub)) % 360
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
        marks = []
        for i, (L, _) in enumerate(slots):
            if L not in man["marker"]["lines"]:
                continue
            c = g.dump(HALF_BASE * half + i)
            st = sync_pos(c, blank_code, tip_code)
            lo, hi = lv["sync_code"] + 47.5 * (lv["white_code"] - lv["sync_code"]) / 140, \
                lv["sync_code"] + 110 * (lv["white_code"] - lv["sync_code"]) / 140
            cells = [c[int(round(st + (10.5 + 1.4 * (i + 0.5)) * US))] for i in range(36)]
            marks.append(pm.decode_marker([1 if x > (lo + hi) / 2 else 0 for x in cells]))
        g.cmd("hold 0", until="frames")

        good = tgt_err < 0.02 * max(1.0, test["params"].get("peak_ns", 1)) and ev_bad == 0 and len_bad == 0 \
            and (len(pos_err) == 0 or np.max(np.abs(pos_err)) < 0.15) and np.max(np.abs(ph_err)) < 4 and nosync_ok \
            and struct_ok \
            and order_ok and marks and all(m == (int(man["test"]["code"]), rung, frame & 0xFFFF) for m in marks)
        ok_all &= good
        if not good and len(ph_err) and np.max(np.abs(ph_err)) >= 4:
            print("   burst phase error by slot:", {i: round(float(p_), 1) for i, p_ in zip(checked, ph_err)})
        print(f"{'PASS' if good else 'FAIL'} {tid} r{rung} {mode} (frame {frame}, {len(lens)} lines, nsub {nsub}): "
              f"target err {tgt_err:.4f} ns, e/v bad {ev_bad}, length bad {len_bad}, "
              f"sync pos err {np.max(np.abs(pos_err)) if len(pos_err) else 0:.3f} smp, "
              f"burst phase err {np.max(np.abs(ph_err)):.1f} deg, no-sync ok {nosync_ok}, halves {fr[0]}/{fr[1]}, markers {marks}"
              f"{' |' + struct_note if struct_note else ''}")
    g.cmd("chroma under")
    g.cmd("test V0")
    print("ALL PASS" if ok_all else "SOME FAILED")


if __name__ == "__main__":
    main()
