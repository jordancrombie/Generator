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
         ("S7", 5, 1, "under", 1), ("S7", 5, 1, "under", 0)]
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
    for tid, rung, seed, mode, half in TESTS:
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
        pos_err, ph_err, nosync_ok = [], [], True
        for i in (21, 50, 99, 100, 101, 255, 256, 300, 400):
            if i >= len(slots):
                continue
            c = g.dump(HALF_BASE * half + i)
            e, v = plan[i]
            if slots[i][1] & pm.SLOT_NOSYNC:  # no sync pulse: nothing below blanking in the first 10 us
                nosync_ok &= c[:int(10 * US)].min() > tip_code + 0.25 * (blank_code - tip_code)  # burst dips to -20 IRE
                sp = ORIGIN + v * 2.0 / nsub
            else:
                sp = sync_pos(c, blank_code, tip_code)
                pos_err.append(sp - (ORIGIN + v * 2.0 / nsub))
            exp_ph = 180.0 if mode == "under" else (180.0 - 90.0 * (e + v * 2.0 / nsub)) % 360
            ph_err.append((burst_phase(c, sp, cum[i], e) - exp_ph + 180) % 360 - 180)
        pos_err, ph_err = np.array(pos_err), np.array(ph_err)

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
            and order_ok and marks and all(m == (int(man["test"]["code"]), rung, frame & 0xFFFF) for m in marks)
        ok_all &= good
        print(f"{'PASS' if good else 'FAIL'} {tid} r{rung} {mode} (frame {frame}, {len(lens)} lines, nsub {nsub}): "
              f"target err {tgt_err:.4f} ns, e/v bad {ev_bad}, length bad {len_bad}, "
              f"sync pos err {np.max(np.abs(pos_err)) if len(pos_err) else 0:.3f} smp, "
              f"burst phase err {np.max(np.abs(ph_err)):.1f} deg, no-sync ok {nosync_ok}, halves {fr[0]}/{fr[1]}, markers {marks}")
    g.cmd("chroma under")
    g.cmd("test V0")
    print("ALL PASS" if ok_all else "SOME FAILED")


if __name__ == "__main__":
    main()
