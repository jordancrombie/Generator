"""Fingerprint a TBC from several field trials.

usage: fingerprint.py SUITE.json [SUITE.json ...] [--name NAME] [--save] [--html [PATH]]

A fingerprint describes how a TBC behaves, so units and models can be told
apart. Its traits come in three kinds:

  model   behaviours that should hold for every unit of a model: does the
          output lock to its input, what does it show with no signal, does
          it have a colour killer, ACC, per-line jitter correction ...
  unit    measured figures that may differ between units of one model:
          clock offset, gains, hue offset, output jitter floor, latency
  rig     figures that depend on the rig as much as the TBC (output level
          in volts depends on termination). Recorded, not used to match.

Each trait is taken from every trial and aggregated. Categories get the
majority value and how many trials agreed. Numbers get the median and range.
Tests whose verdict varies between trials are listed as rates ("passes in
3 of 5 trials"), since they say more as a rate than as a value.

Use field trials of one unit, run with two channels and the same tools
version. More trials make a firmer fingerprint; 3 or more is sensible.
--save writes fingerprints/<name>.json (kept in the repo), the library that
identification will match against.
"""
import argparse, json, math, os, re, statistics, time
from collections import Counter, OrderedDict

import grade
from run_suite import loss_tolerance

LIBRARY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fingerprints")
SCHEMA = "tbt-fingerprint/1"

# (key, kind, label, unit). Order is the order of the trait card.
TRAITS = [
    ("output_lock", "model", "Output locked to its input", ""),
    ("no_signal_output", "model", "With no input at all (V1)", ""),
    ("lost_fields_no_signal", "model", "Lost fields ridden through, no signal (S10)", "fields"),
    ("lost_fields_snow", "model", "Lost fields ridden through, snow (S11)", "fields"),
    ("gap_shows_as", "model", "A lost-field gap shows as", ""),
    ("colour_killer", "model", "Colour killer (C2)", ""),
    ("acc", "model", "Automatic colour control (C1, 10 IRE burst)", ""),
    ("chroma_phase_correction", "model", "Per-line chroma phase correction (C3)", ""),
    ("line_jitter_correction", "model", "Random line-jitter correction (H1, 50 ns rms)", ""),
    ("head_switch_correction", "model", "Head-switch skew correction (H4, 10 µs)", ""),
    ("slow_timing_correction", "model", "Slow timing correction (H2, H3, F1)", ""),
    ("progressive_240p", "model", "240p input (S5)", ""),
    ("no_vsync_position", "model", "Picture with no V-sync (S4 r6)", ""),
    ("hum_clamp", "model", "Hum clamped (L3, 20 IRE)", ""),
    ("level_control", "model", "Level control (L4, 120 %)", ""),
    ("rate_ppm", "unit", "Output line rate vs input", "ppm"),
    ("latency_ms", "unit", "Latency, range seen", "ms"),
    ("luma_gain", "unit", "Luma gain (V0, gray out / in)", "×"),
    ("saturation_ratio", "unit", "Saturation (V0, chroma/burst out ÷ in)", "×"),
    ("hue_offset_deg", "unit", "Hue offset (V0, out − in)", "°"),
    ("burst_out_ire", "unit", "Output burst (V0)", "IRE p-p"),
    ("output_jitter_ns", "unit", "Output H jitter (V0)", "ns rms"),
    ("noise_out_ire", "unit", "Output noise on gray (V0)", "IRE rms"),
    ("acc_fraction", "unit", "ACC fraction (0 none, 1 full)", ""),
    ("jitter_correction_db", "unit", "H1 r4 correction (input ÷ residual)", "dB"),
    ("sync_depth_v", "rig", "Output sync depth (V0; depends on termination)", "V"),
]
INFO = {k: (kind, label, unit) for k, kind, label, unit in TRAITS}


def find(runs, tid, rung=None):
    """The live run of a test (and rung); None if absent or dead."""
    for r in runs:
        if r["test"]["id"] == tid and (rung is None or r["test"]["rung"] == rung) and not r.get("dead"):
            return r
    return None


def pic(r):
    return (r or {}).get("picture")


def inpic(r):
    return ((r or {}).get("input") or {}).get("picture")


def verdict(G, tid, rung):
    return next((g["verdict"] for g in G["runs"] if g["test"] == tid and g["rung"] == rung), None)


def traits_of(runs):
    """One trial's traits as {key: value}; a trait is left out if its test wasn't run."""
    runs = [r for r in runs if r]
    G = grade.grade(runs)
    t = {}
    v0 = find(runs, "V0")

    rates = [r["input"]["rate_ppm"] for r in runs if (r.get("input") or {}).get("rate_ppm") is not None]
    if rates:
        m = statistics.median(rates)
        t["output_lock"] = "locked" if abs(m) < 2 else "free-running"
        t["rate_ppm"] = round(m, 2)
    lat = [r["input"]["latency"]["median_ms"] for r in runs if (r.get("input") or {}).get("latency")]
    if lat:
        t["latency_ms"] = [round(min(lat), 2), round(max(lat), 2)]

    v1 = next((r for r in runs if r["test"]["id"] == "V1"), None)
    if v1:
        fs_ = v1.get("field_summary") or {}
        t["no_signal_output"] = ("no output (no sync)" if v1.get("dead") else
                                 "its own pattern" if fs_.get("fallback") else
                                 "black" if fs_.get("blank") else "other")

    for tid, key in (("S10", "lost_fields_no_signal"), ("S11", "lost_fields_snow")):
        tol = loss_tolerance(runs, tid)
        if tol is not None:
            t[key] = 11 if isinstance(tol, str) else tol      # 11 = "∞ (>10)"
    hows = [r["loss"]["how"] for r in runs if r.get("loss") and r["loss"]["handled"] and r["loss"]["gap_captured"]]
    if hows:
        base = Counter(h.replace("garbled on recovery", "blanked") for h in hows).most_common(1)[0][0]
        t["gap_shows_as"] = base

    c2 = pic(find(runs, "C2", 1))
    if c2:
        t["colour_killer"] = "yes" if c2["yellow_chroma_pp_ire"] < grade.LIMITS["killed_chroma_ire"] else "no"

    c1 = find(runs, "C1", 3)
    if c1 and pic(c1) and pic(v0):
        bi = (inpic(c1) or {}).get("burst_pp_ire")
        bi0 = (inpic(v0) or {}).get("burst_pp_ire")
        b = bi / bi0 if bi and bi0 else 10 / 40              # input burst, relative to V0's
        c = pic(c1)["yellow_chroma_pp_ire"] / pic(v0)["yellow_chroma_pp_ire"]
        frac = max(0.0, min(1.5, math.log(c) / math.log(1 / b))) if c > 0 and b < 1 else 0.0
        t["acc_fraction"] = round(frac, 2)
        t["acc"] = "full" if frac > 0.8 else "partial" if frac > 0.2 else "none"

    c3 = [r for r in runs if r["test"]["id"] == "C3" and pic(r) and not r.get("dead")]
    if c3:
        r = max(c3, key=lambda r: r["test"]["rung"])
        j_in = (inpic(r) or {}).get("yellow_hue_jitter_deg") or float(r["test"]["params"]["rms_deg"])
        t["chroma_phase_correction"] = "yes" if pic(r)["yellow_hue_jitter_deg"] < 0.3 * j_in else "no"

    h1 = find(runs, "H1", 4)
    floor = (pic(v0) or {}).get("displacement", {}) or {}
    d = (pic(h1) or {}).get("displacement")
    if d and floor.get("rms_ns") is not None:
        ex = grade.excess(d["rms_ns"], floor["rms_ns"])
        ratio = ex / 50.0
        t["line_jitter_correction"] = "corrects" if ratio < 0.2 else "partial" if ratio < 0.7 else "none"
        t["jitter_correction_db"] = round(20 * math.log10(50.0 / max(ex, 1.0)), 1)

    vh4 = verdict(G, "H4", 5)
    if vh4:
        t["head_switch_correction"] = "corrects" if vh4 == "pass" else "passes it through"
    slow = [verdict(G, tid, rung) for tid, rung in (("H2", 3), ("H3", 5), ("F1", 3))]
    slow = [v for v in slow if v]
    if slow:
        t["slow_timing_correction"] = ("corrects" if all(v == "pass" for v in slow) else
                                       "partial" if any(v == "pass" for v in slow) else "no")

    s5 = find(runs, "S5", 1)
    if s5:
        lengths = s5["fields"]["lengths"]
        t["progressive_240p"] = ("converted to standard fields" if s5["fields"]["all_standard"] else
                                 "passed as 240p" if "262" in lengths else "other")

    s4 = find(runs, "S4", 6)
    base = ((v0 or {}).get("markers") or {}).get("line_after_vsync")
    lv = ((s4 or {}).get("markers") or {}).get("line_after_vsync")
    if lv and base:
        # Where it lands varies with each lock; whether it holds its place doesn't.
        shift = lv["median"] - base["median"]
        t["no_vsync_position"] = ("holds its position" if abs(shift) <= 1 else
                                  "drops lower" if shift > 0 else "moves higher")

    l3 = pic(find(runs, "L3", 3))
    if l3:
        t["hum_clamp"] = "yes" if l3["blanking_wander_ire"] < grade.LIMITS["wander_ire"] else "no"

    l4 = find(runs, "L4", 4)
    if pic(l4) and pic(v0):
        gi = (inpic(l4) or {}).get("gray_ire")
        gi0 = (inpic(v0) or {}).get("gray_ire")
        k_in = gi / gi0 if gi and gi0 else 1.2
        k_out = pic(l4)["gray_ire"] / pic(v0)["gray_ire"]
        t["level_control"] = ("AGC restores the level" if abs(k_out - 1) < 0.3 * abs(k_in - 1)
                              else "passes the level" if abs(k_out - k_in) < 0.3 * abs(k_in - 1) else "partial")

    if pic(v0):
        p0, i0 = pic(v0), inpic(v0)
        t["burst_out_ire"] = round(p0["burst_pp_ire"], 1)
        t["noise_out_ire"] = round(p0["gray_noise_ire_rms"], 2)
        if i0:
            t["luma_gain"] = round(p0["gray_ire"] / i0["gray_ire"], 3)
            t["saturation_ratio"] = round(grade.sat(p0) / grade.sat(i0), 3)
            t["hue_offset_deg"] = round((p0["yellow_hue_deg"] - i0["yellow_hue_deg"] + 180) % 360 - 180, 1)
    if v0:
        if v0["h"]["jitter_ns_rms"] is not None:
            t["output_jitter_ns"] = round(v0["h"]["jitter_ns_rms"], 2)
        sd = (v0.get("level") or {}).get("sync_depth_v")
        if sd:
            t["sync_depth_v"] = round(sd, 3)
    return t, G


def aggregate(per_trial):
    """Combine each trait over the trials: majority/agreement, or median/range."""
    out = OrderedDict()
    for key, kind, label, unit in TRAITS:
        vals = [t[key] for t in per_trial if key in t]
        if not vals:
            continue
        entry = {"kind": kind, "label": label, "unit": unit, "n": len(vals)}
        if isinstance(vals[0], list):                         # a range per trial: overall range
            entry["value"] = [min(v[0] for v in vals), max(v[1] for v in vals)]
            entry["agree"] = None
        elif isinstance(vals[0], (int, float)) and not isinstance(vals[0], bool):
            entry["value"] = round(statistics.median(vals), 3)
            entry["range"] = [min(vals), max(vals)]
            mode, cnt = Counter(vals).most_common(1)[0]
            entry["agree"] = round(cnt / len(vals), 2) if kind == "model" else None
            if kind == "model":
                entry["value"] = mode
        else:
            mode, cnt = Counter(vals).most_common(1)[0]
            entry["value"], entry["agree"] = mode, round(cnt / len(vals), 2)
            if cnt < len(vals):
                entry["seen"] = dict(Counter(vals))
        out[key] = entry
    return out


def variable_behaviours(grades):
    """Tests whose verdict differs between trials, as pass rates."""
    by = OrderedDict()
    for G in grades:
        for g in G["runs"]:
            by.setdefault((g["test"], g["rung"]), []).append(g["verdict"])
    out = []
    for (tid, rung), vs in by.items():
        if len(set(vs)) > 1:
            c = Counter(vs)
            out.append({"test": tid, "rung": rung, "trials": len(vs), "verdicts": dict(c),
                        "pass_rate": round((c.get("pass", 0) + 0.5 * c.get("marginal", 0)) / len(vs), 2)})
    return out


def fingerprint(suites, name=None):
    per, grades, used = [], [], []
    for path, data in suites:
        runs = [r for r in data["runs"] if r]
        if all(r.get("dead") for r in runs if r["test"]["id"] != "V1"):
            print(f"skipping {os.path.basename(path)}: the TBC's output was dead throughout")
            continue
        t, G = traits_of(runs)
        per.append(t)
        grades.append(G)
        used.append({"trial": os.path.basename(path).split("_suite_")[0],
                     "channels": 2 if any(r.get("input") for r in runs) else 1,
                     "runs": len(runs), "score": None if G["score"] is None else round(G["score"], 1)})
    if not per:
        raise SystemExit("no usable field trials")
    dut = suites[0][1].get("dut", {})
    scores = [u["score"] for u in used if u["score"] is not None]
    return {"schema": SCHEMA, "name": name or dut.get("name") or "unnamed",
            "notes": dut.get("notes", ""), "created": time.strftime("%Y-%m-%d %H:%M"),
            "trials": used, "score": {"median": statistics.median(scores), "range": [min(scores), max(scores)]}
            if scores else None,
            "traits": aggregate(per), "variable": variable_behaviours(grades)}


def fmt_value(e):
    v, u = e["value"], e["unit"]
    if isinstance(v, list):
        s = f"{v[0]:g} – {v[1]:g}"
    elif isinstance(v, float):
        s = f"{v:g}"
    elif v == 11 and u == "fields":
        s = "∞ (>10)"
    else:
        s = str(v)
    if u and u not in ("fields",) or (u == "fields" and v != 11):
        s += f" {u}" if u else ""
    if e.get("range") and e["range"][0] != e["range"][1] and e["kind"] != "model":
        s += f"  (range {e['range'][0]:g} – {e['range'][1]:g})"
    return s


def print_card(F):
    print(f"\nFingerprint: {F['name']}  ({len(F['trials'])} field trials"
          + (f", score {F['score']['median']:.0f}, range {F['score']['range'][0]:.0f}–{F['score']['range'][1]:.0f}"
             if F["score"] else "") + ")")
    for kind, title in (("model", "Model traits (behaviour)"), ("unit", "Unit traits (measured)"),
                        ("rig", "Rig-dependent (not used to match)")):
        rows = [(k, e) for k, e in F["traits"].items() if e["kind"] == kind]
        if not rows:
            continue
        print(f"  {title}:")
        for k, e in rows:
            agree = (f"   [{e['agree'] * e['n']:.0f}/{e['n']} trials]" if e.get("agree") is not None and e["agree"] < 1
                     else "")
            print(f"    {e['label']:<52s} {fmt_value(e)}{agree}")
    if F["variable"]:
        print("  Varies between trials:")
        for v in F["variable"]:
            vs = ", ".join(f"{k} {n}" for k, n in v["verdicts"].items())
            print(f"    {v['test']} r{v['rung']}: {vs} (of {v['trials']})")


def write_html(F, path):
    import html_report as hr
    def agreeing(e):
        return "" if e.get("agree") is None else f"{e['agree'] * e['n']:.0f}/{e['n']}"

    def table(kind):
        rows = "".join(
            f"<tr><td>{hr.esc(e['label'])}</td><td><b>{hr.esc(fmt_value(e))}</b></td>"
            f"<td class=num>{agreeing(e)}</td></tr>"
            for e in F["traits"].values() if e["kind"] == kind)
        return f'<div class="scroll"><table><thead><tr><th>Trait</th><th>Value</th><th>Trials agreeing</th></tr></thead><tbody>{rows}</tbody></table></div>'
    var = "".join(f"<tr><td>{hr.esc(v['test'])} r{v['rung']}</td><td>"
                  + ", ".join(f"{hr.esc(k)} {n}" for k, n in v["verdicts"].items())
                  + f"</td><td class=num>{v['pass_rate']:.2f}</td></tr>" for v in F["variable"])
    trials = ", ".join(f"{t['trial']} ({t['score']:.0f})" if t["score"] is not None else t["trial"] for t in F["trials"])
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>TBT fingerprint · {hr.esc(F['name'])}</title>
<style>{hr.CSS}</style></head><body><main>
<h1>Fingerprint</h1><p class="sub">Device: <b style="color:var(--fg)">{hr.esc(F['name'])}</b> · {len(F['trials'])} field trials · {hr.esc(F['created'])}</p>
<p class="note">Model traits are behaviours that should hold for every unit of a model; unit traits are
measurements that may differ between units. "Trials agreeing" shows how consistent each trait was.
Trials: {hr.esc(trials)}.</p>
<h2>Model traits</h2>{table('model')}
<h2>Unit traits</h2>{table('unit')}
<h2>Rig-dependent</h2>{table('rig')}
<h2>Varies between trials</h2>
{'<div class="scroll"><table><thead><tr><th>Test</th><th>Verdicts</th><th>Pass rate</th></tr></thead><tbody>' + var + '</tbody></table></div>' if var else '<p class="cap">Every verdict was the same in every trial.</p>'}
<footer>Generated by tools/fingerprint.py · TBT (Time Base Torture)</footer></main></body></html>"""
    with open(path, "w") as f:
        f.write(page)
    return os.path.normpath(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("suites", nargs="+", help="reports/<time>_suite_<dut>.json files of one unit")
    ap.add_argument("--name", help="fingerprint name (default: the trials' DUT name)")
    ap.add_argument("--save", action="store_true", help="save to fingerprints/<name>.json")
    ap.add_argument("--html", nargs="?", const="auto")
    a = ap.parse_args()
    suites = [(p, json.load(open(p))) for p in a.suites]
    F = fingerprint(suites, a.name)
    print_card(F)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", F["name"]).strip("-").lower()
    if a.save:
        os.makedirs(LIBRARY, exist_ok=True)
        path = os.path.join(LIBRARY, f"{slug}.json")
        with open(path, "w") as f:
            json.dump(F, f, indent=1, ensure_ascii=False)
        print(f"saved {os.path.normpath(path)}")
    if a.html:
        path = (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports",
                             time.strftime("%Y%m%d-%H%M%S") + f"_fingerprint_{slug}.html")
                if a.html == "auto" else a.html)
        print(f"HTML: {write_html(F, path)}")


if __name__ == "__main__":
    main()
