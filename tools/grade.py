"""Grade a field trial: pass/fail per run, a grade per test and an overall score.

usage: grade.py reports/<time>_suite_<dut>.json [--class frame|line] [--html [PATH]]

PROVISIONAL. The thresholds are the first proposals from docs/design.md
("Scoring") and haven't been checked against a known-good TBC yet. Grades
are computed from the saved analysis (observe_tbc.analyze() per run), so
old field trials can be re-graded when thresholds change, without
recapturing anything.

Each run gets one verdict:
  pass      every check met
  marginal  only minor checks missed (e.g. a garbled field on recovery)
  fail      a main check missed
  invalid   the rig is at fault (CH2 shows the generator's output was wrong)
  info      nothing to grade (V1, or the event wasn't inside the capture)

Picture figures are judged against the real input (CH2) when it was
captured, otherwise against nominal levels. Residual horizontal displacement
is judged as the excess over the V0 run in the same field trial (the
measurement floor: scope noise, ~11 ns on an unterminated CH1).

--class says which ideal applies to the structure tests (S5-S8): a frame
TBC / synchroniser should output standard fields whatever comes in; a line
TBC passes the structure through. Default: frame.
"""
import argparse, json, math, os, re, time
from collections import OrderedDict

# Proposed pass thresholds (docs/design.md, Scoring).
LIMITS = {
    "output_jitter_ns": 5.0,     # output line-period jitter, rms
    "disp_rms_ns": 10.0,         # residual horizontal displacement, rms (excess over V0)
    "disp_peak_ns": 35.0,        # ... 99th percentile
    "hue_deg": 3.0,              # yellow hue vs input (or nominal)
    "sat_pct": 5.0,              # chroma/burst ratio vs input (or nominal)
    "luma_ire": 2.0,             # gray bar vs input (or nominal)
    "burst_ire": 2.0,            # output burst 40 +- this
    "hue_jitter_deg": 3.0,       # C3: hue stability
    "killed_chroma_ire": 3.0,    # C2: chroma left with no burst
    "noise_gain": 1.1,           # L2: output noise / input noise
    "wander_ire": 1.0,           # L3: blanking wander, rms
}
# Nominal picture (SMPTE bars, 75 %) for single-channel runs.
NOMINAL = {"yellow_hue_deg": 167.1, "yellow_chroma_pp_ire": 62.0, "burst_pp_ire": 40.0, "gray_ire": 76.9}
# Group weights for the overall score: re-timing is the TBC's core job.
WEIGHTS = {"H": 3.0, "F": 1.5, "S": 2.0, "C": 1.5, "L": 1.0, "X": 1.0, "V": 1.0}
# Ladders that aren't ordered mild -> severe: graded as "k of n rungs".
UNORDERED = {"S4", "S6", "C1", "X1"}
STRUCTURE = {"S5", "S6", "S7", "S8"}     # line vs frame ideals differ
PICTURE_TESTS = re.compile(r"^(V0|H\d|F\d|S[1239]|C\d|L\d)$")
DISPLACEMENT_TESTS = re.compile(r"^(H\d|F\d|S[1239])$")   # timing tests (noise tests raise the floor themselves)


def check(name, ok, value="", limit="", minor=False):
    return {"name": name, "ok": bool(ok), "value": value, "limit": limit, "minor": minor}


def ctx_of(runs, tbc_class="frame"):
    """Shared context: the V0 run's displacement floor and vertical position."""
    v0 = next((r for r in runs if r and r["test"]["id"] == "V0" and not r.get("dead")), None)
    disp = ((v0 or {}).get("picture") or {}).get("displacement")
    line = ((v0 or {}).get("markers") or {}).get("line_after_vsync")
    return {"class": tbc_class, "floor_ns": disp["rms_ns"] if disp else None,
            "v0_out": (v0 or {}).get("picture"), "v0_in": ((v0 or {}).get("input") or {}).get("picture"),
            "floor_peak_ns": disp["peak_ns"] if disp else None,
            "base_line": line["median"] if line else None}


def excess(v, floor):
    return math.sqrt(max(0.0, v * v - floor * floor)) if floor is not None else v


def common_checks(r, ctx, tid):
    out = [check("output alive", not r.get("dead"), "dead" if r.get("dead") else "video")]
    if r.get("dead"):
        return out
    structure_passes = ctx["class"] == "line" and tid in STRUCTURE
    if not structure_passes:
        lengths = r["fields"]["lengths"]
        out.append(check("standard output fields", r["fields"]["all_standard"],
                         ", ".join(f"{k}×{v}" for k, v in lengths.items()), "262.5 lines"))
    fs_ = r.get("field_summary") or {}
    out.append(check("never its own pattern", fs_.get("fallback", 0) == 0, fs_.get("fallback", 0), "0 fields"))
    j = r["h"]["jitter_ns_rms"]
    if j is not None:
        out.append(check("output H stability", j < LIMITS["output_jitter_ns"], f"{j:.2f} ns rms",
                         f"< {LIMITS['output_jitter_ns']:g} ns"))
    return out


def sequence_checks(r, ctx, tid):
    """Continuous-input tests: every frame once, in order, nothing mixed or torn."""
    ev, fs_ = r["markers"]["events"], r.get("field_summary") or {}
    out = [check("markers decoded", r["markers"]["decoded"] > 0, r["markers"]["decoded"], "> 0")]
    if not r["markers"]["decoded"]:
        return out
    out.append(check("no back-steps or skips", ev["backward_steps"] + ev["skipped_frames"] == 0,
                     f"{ev['backward_steps']} back, {ev['skipped_frames']} skipped", "0"))
    # A free-running frame sync repeats a frame each time its delay wraps: minor.
    out.append(check("no repeated frames", ev["repeats"] + ev["single_field_frames"] == 0,
                     f"{ev['repeats']} repeated, {ev['single_field_frames']} single-field", "0", minor=True))
    bad = fs_.get("garbled", 0) + fs_.get("blend", 0) + fs_.get("ghost", 0) + len(fs_.get("field_mismatches", []))
    out.append(check("no garbled, ghosted or torn fields", bad == 0, bad, "0"))
    out += position_checks(r, ctx)
    return out


def position_checks(r, ctx):
    lv = r["markers"].get("line_after_vsync")
    if not lv:
        return []
    spread = lv["max"] - lv["min"]
    out = [check("picture steady vertically", spread <= 1, f"lines {lv['min']}–{lv['max']}", "±1 line")]
    if ctx["base_line"] is not None:
        out.append(check("picture where V0 put it", abs(lv["median"] - ctx["base_line"]) <= 1,
                         f"line {lv['median']:.0f}", f"line {ctx['base_line']:.0f}", minor=True))
    return out


def sat(pc):
    return pc["yellow_chroma_pp_ire"] / pc["burst_pp_ire"] if pc.get("burst_pp_ire") else None


def sent(tid, p, base):
    """The picture this test sends, given V0's (single channel: worked out from the params)."""
    e = dict(base)
    if tid == "C1":
        e["burst_pp_ire"] = base["burst_pp_ire"] * float(p["burst_pp_ire"]) / 40.0
    if tid == "C4":
        e["yellow_hue_deg"] = base["yellow_hue_deg"] + float(p["deg"])
    if tid == "L4":
        k = float(p["white_pct"]) / 100.0
        e["gray_ire"] = 7.5 + (base["gray_ire"] - 7.5) * k
        e["yellow_chroma_pp_ire"] = base["yellow_chroma_pp_ire"] * k
    return e


def picture_checks(r, ctx, tid):
    """V0: the TBC's picture against its input (static accuracy: gain, hue,
    saturation, burst). Every other test: against what the TBC's own V0
    transfer predicts for this test's input, so a test grades how the TBC
    handled that fault, not its static errors again."""
    pc = r.get("picture")
    if not pc:
        return [check("picture measurable", False, "no colour-bar lines in our fields", "", minor=True)]
    p = r["test"].get("params", {})
    pi = (r.get("input") or {}).get("picture")
    out = []

    d = pc.get("displacement")
    if d and DISPLACEMENT_TESTS.match(tid):
        ex, exp_ = excess(d["rms_ns"], ctx["floor_ns"]), excess(d["peak_ns"], ctx["floor_peak_ns"])
        out.append(check("residual H displacement", ex < LIMITS["disp_rms_ns"],
                         f"{ex:.1f} ns rms over V0", f"< {LIMITS['disp_rms_ns']:g} ns"))
        out.append(check("residual H displacement, peak", exp_ < LIMITS["disp_peak_ns"],
                         f"{exp_:.0f} ns over V0", f"< {LIMITS['disp_peak_ns']:g} ns", minor=True))

    if tid == "C2":   # no burst: the ideal is monochrome
        c = pc["yellow_chroma_pp_ire"]
        out.append(check("colour killed", c < LIMITS["killed_chroma_ire"], f"chroma {c:.1f} IRE p-p",
                         f"< {LIMITS['killed_chroma_ire']:g}"))
        return out

    v0_out, v0_in = ctx["v0_out"], ctx["v0_in"] or NOMINAL
    if tid == "V0" or not v0_out:
        # Absolute: the output against what went in.
        ref, src = (pi, "input") if pi else (sent(tid, p, NOMINAL), "nominal")
        exp_hue, exp_sat, exp_gray = ref["yellow_hue_deg"], sat(ref), [ref["gray_ire"]]
        if tid == "L4" and not pi:
            exp_gray.append(NOMINAL["gray_ire"])
        db = pc["burst_pp_ire"] - 40.0
        out.append(check("output burst", abs(db) <= LIMITS["burst_ire"], f"{pc['burst_pp_ire']:.1f} IRE p-p",
                         f"40 ± {LIMITS['burst_ire']:g}"))
    else:
        # Relative: the TBC's V0 transfer applied to this test's input.
        src = "V0 transfer"
        inp = pi or sent(tid, p, v0_in)
        exp_hue = v0_out["yellow_hue_deg"] + (inp["yellow_hue_deg"] - v0_in["yellow_hue_deg"])
        exp_sat = sat(v0_out) * sat(inp) / sat(v0_in) if sat(v0_in) and sat(inp) and sat(v0_out) else None
        exp_gray = [v0_out["gray_ire"] * inp["gray_ire"] / v0_in["gray_ire"]]
        if tid == "L4":                           # an AGC that restores the level also passes
            exp_gray.append(v0_out["gray_ire"])
        if tid == "C1":                           # ACC: burst regenerated as on V0
            db = pc["burst_pp_ire"] - v0_out["burst_pp_ire"]
            out.append(check("burst regenerated", abs(db) <= LIMITS["burst_ire"], f"{db:+.1f} IRE vs V0",
                             f"± {LIMITS['burst_ire']:g}"))

    dh = (pc["yellow_hue_deg"] - exp_hue + 180) % 360 - 180
    out.append(check("hue", abs(dh) < LIMITS["hue_deg"], f"{dh:+.1f}° vs {src}", f"< {LIMITS['hue_deg']:g}°"))
    if exp_sat and sat(pc):
        e = 100 * (sat(pc) / exp_sat - 1)
        out.append(check("saturation (chroma / burst)", abs(e) < LIMITS["sat_pct"], f"{e:+.1f} % vs {src}",
                         f"< {LIMITS['sat_pct']:g} %"))
    dg = min((pc["gray_ire"] - g for g in exp_gray), key=abs)
    out.append(check("luma (gray bar)", abs(dg) < LIMITS["luma_ire"], f"{dg:+.1f} IRE vs {src}",
                     f"< {LIMITS['luma_ire']:g} IRE"))
    if tid == "C3":
        j = pc["yellow_hue_jitter_deg"]
        out.append(check("hue stability", j < LIMITS["hue_jitter_deg"], f"±{j:.1f}°", f"< {LIMITS['hue_jitter_deg']:g}°"))
    if tid == "L2":
        # Added noise only: each side's V0 noise (the TBC's own floor, the
        # generator's and the scope's) is taken out in quadrature first.
        n0_out = v0_out["gray_noise_ire_rms"] if v0_out else 0.0
        n0_in = v0_in.get("gray_noise_ire_rms", 0.0) if v0_out else 0.0
        n_in = excess(pi["gray_noise_ire_rms"], n0_in) if pi else 100.0 / 10 ** (float(p["snr_db"]) / 20)
        gain = v0_out["gray_ire"] / v0_in["gray_ire"] if v0_out else 1.0     # the TBC's own luma gain
        n_out = excess(pc["gray_noise_ire_rms"], n0_out)
        ratio = n_out / (n_in * gain) if n_in else None
        if ratio is not None:
            out.append(check("noise not amplified", ratio < LIMITS["noise_gain"],
                             f"×{ratio:.2f} (added noise, vs input × luma gain)", f"< ×{LIMITS['noise_gain']:g}"))
    if tid == "L3":
        w = pc["blanking_wander_ire"]
        out.append(check("hum clamped", w < LIMITS["wander_ire"], f"{w:.2f} IRE rms", f"< {LIMITS['wander_ire']:g}"))
    return out


def structure_checks(r, ctx, tid):
    """S5-S8, frame class: standard fields out; drops and repeats allowed; no tears or roll."""
    fs_ = r.get("field_summary") or {}
    out = [check("markers decoded", r["markers"]["decoded"] > 0, r["markers"]["decoded"], "> 0")]
    torn = len(fs_.get("field_mismatches", []))
    out.append(check("no torn or stuck frames", torn == 0, torn, "0"))
    bad = fs_.get("garbled", 0) + fs_.get("blend", 0) + fs_.get("ghost", 0)
    out.append(check("no garbled or ghosted fields", bad == 0, bad, "0"))
    ev = r["markers"]["events"]
    out.append(check("no back-steps", ev["backward_steps"] == 0, ev["backward_steps"], "0", minor=True))
    if ctx["class"] == "frame":
        out += position_checks(r, ctx)
    return out


def grade_run(r, ctx):
    t = r["test"]
    tid = t["id"]
    res = {"test": tid, "rung": t["rung"], "rungs": t.get("rungs"), "params": t.get("params", {}),
           "checks": [], "notes": []}
    inp = r.get("input")
    if inp is not None and inp.get("present") and not inp.get("ok"):
        res["verdict"] = "invalid"
        res["notes"].append("rig: " + "; ".join(inp.get("issues", [])))
        return res
    if inp is not None and not inp.get("present") and tid != "V1":
        res["notes"].append("no sync found on CH2 (reference not connected, or this test's input too "
                            "disturbed to analyse): judged as single-channel")
    if inp is not None and inp.get("verified") is False:
        res["notes"].append("input not verified on CH2 (markers not decodable for this test)")

    if tid == "V1":
        fs_ = r.get("field_summary") or {}
        ok = fs_.get("ours", 0) == 0 and fs_.get("unmarked", 0) == 0
        res["checks"] = [check("fallback never taken for ours", ok, fs_.get("ours", 0), "0 fields")]
        res["verdict"] = "info" if ok else "invalid"
        if not ok:
            res["notes"].append("the analysis mistook the TBC's own output for ours")
        return res

    checks = common_checks(r, ctx, tid)
    if not r.get("dead"):
        if tid in ("S10", "S11"):
            L = r.get("loss")
            if not L or not L["gap_captured"]:
                res["checks"], res["verdict"] = checks, "info"
                res["notes"].append("the gap wasn't inside the capture")
                return res
            checks = [c for c in checks if c["name"] != "standard output fields"]   # judged below
            checks.append(check("rides through the gap", L["handled"], L["how"], "no fallback, no sync loss"))
            checks.append(check("clean recovery", L["clean"], f"{L['garbled_fields']} garbled", "0", minor=True))
        elif tid == "X1":
            H = r.get("hunt") or {}
            if H.get("after_clean") is None:
                res["checks"], res["verdict"] = checks, "info"
                res["notes"].append("the after-stress window wasn't captured")
                return res
            checks = [c for c in checks if c["name"] != "standard output fields"]
            checks.append(check("clean after stress", H["after_clean"], "clean" if H["after_clean"] else "faults after",
                                "clean within 3 frames"))
            checks.append(check("no stuck or ghosted fields", H["stuck_or_ghost"] == 0, H["stuck_or_ghost"], "0",
                                minor=True))
        elif tid in STRUCTURE:
            checks += structure_checks(r, ctx, tid)
        else:
            checks += sequence_checks(r, ctx, tid)
        if PICTURE_TESTS.match(tid):
            checks += picture_checks(r, ctx, tid)
    res["checks"] = checks
    main_fail = any(not c["ok"] and not c["minor"] for c in checks)
    minor_fail = any(not c["ok"] and c["minor"] for c in checks)
    res["verdict"] = "fail" if main_fail else "marginal" if minor_fail else "pass"
    return res


SCORE = {"pass": 1.0, "marginal": 0.5, "fail": 0.0}


def grade_tests(graded):
    """Per test: rungs tested, verdicts, and the grade (highest rung passed)."""
    tests = OrderedDict()
    for g in graded:
        tests.setdefault(g["test"], []).append(g)
    out = OrderedDict()
    for tid, gs in tests.items():
        gs = sorted(gs, key=lambda g: g["rung"])
        scored = [g for g in gs if g["verdict"] in SCORE]
        score = sum(SCORE[g["verdict"]] for g in scored) / len(scored) if scored else None
        if not scored:
            summary = "not graded"
        elif tid in UNORDERED or len(scored) == 1:
            ok = sum(g["verdict"] != "fail" for g in scored)
            summary = (f"{ok} of {len(scored)} rungs passed" if len(scored) > 1 else
                       f"rung {scored[0]['rung']}: {scored[0]['verdict']}")
        else:
            best = None
            for g in scored:
                if g["verdict"] == "fail":
                    break
                best = g["rung"]
            summary = (f"passes up to rung {best} of {gs[0]['rungs']}" if best is not None
                       else f"fails from rung {scored[0]['rung']}")
        out[tid] = {"score": score, "summary": summary, "rungs": [(g["rung"], g["verdict"]) for g in gs]}
    return out


def overall(tests):
    groups = {}
    for tid, t in tests.items():
        if t["score"] is not None and not tid.startswith("V1"):
            groups.setdefault(tid[0], []).append(t["score"])
    if not groups:
        return None, {}
    gscore = {k: sum(v) / len(v) for k, v in groups.items()}
    w = sum(WEIGHTS.get(k, 1.0) for k in gscore)
    return 100 * sum(WEIGHTS.get(k, 1.0) * s for k, s in gscore.items()) / w, gscore


def grade(runs, tbc_class="frame"):
    runs = [r for r in runs if r]
    ctx = ctx_of(runs, tbc_class)
    graded = [grade_run(r, ctx) for r in runs]
    tests = grade_tests(graded)
    score, groups = overall(tests)
    notes = []
    dead = sum(1 for r in runs if r.get("dead") and r["test"]["id"] != "V1")
    if dead and dead == sum(1 for r in runs if r["test"]["id"] != "V1"):
        notes.insert(0, "the TBC's output was dead in every run: this grades a dead unit, not its "
                        "correction. Power-cycle it and run the field trial again")
        score = None
    elif dead:
        notes.append(f"the TBC's output was dead in {dead} run(s); those runs fail")
    if ctx["floor_ns"] is None:
        notes.append("no live V0 run: displacement is judged without subtracting the measurement floor")
    if not any(r.get("input") for r in runs):
        notes.append("single channel: picture judged against nominal levels")
    return {"provisional": True, "class": tbc_class, "limits": LIMITS, "floor_ns": ctx["floor_ns"],
            "runs": graded, "tests": tests, "groups": groups, "score": score, "notes": notes}


def print_grades(G):
    print(f"\nGrades (PROVISIONAL thresholds; {G['class']} TBC ideals"
          + (f"; displacement floor {G['floor_ns']:.1f} ns from V0" if G["floor_ns"] is not None else "") + ")")
    for g in G["runs"]:
        failed = [c for c in g["checks"] if not c["ok"]]
        why = "; ".join(f"{c['name']}: {c['value']}" + (" (minor)" if c["minor"] else "") for c in failed)
        why = why or "; ".join(g["notes"])
        print(f"  {g['test']:4s} r{g['rung']:<2d} {g['verdict']:8s} {why}")
    print("Per test:")
    for tid, t in G["tests"].items():
        print(f"  {tid:4s} {t['summary']}")
    if G["score"] is None:
        print("Overall: not scored")
    else:
        gs = ", ".join(f"{k} {100 * v:.0f}" for k, v in sorted(G["groups"].items()))
        print(f"Overall: {G['score']:.0f} / 100 ({gs})")
    for n in G["notes"]:
        print(f"note: {n}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("suite", help="reports/<time>_suite_<dut>.json from run_suite.py")
    ap.add_argument("--class", dest="tbc_class", choices=("frame", "line"), default="frame")
    ap.add_argument("--html", nargs="?", const="auto")
    a = ap.parse_args()
    data = json.load(open(a.suite))
    G = grade(data["runs"], a.tbc_class)
    print_grades(G)
    if a.html:
        import html_report
        results = data["runs"]
        path = None if a.html == "auto" else a.html
        print(f"HTML report: {html_report.write(results, path, grades=G)}")


if __name__ == "__main__":
    main()
