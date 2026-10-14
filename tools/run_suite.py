"""Run several test rungs on one scope session and report on each.

usage: run_suite.py [SPEC ...] [--channels 1|2] [--dut NAME] [--note TEXT] [--keep]
                    [--html [PATH]] [--rate HZ] [--seconds S] [--settle S]

SPEC is TEST:RUNGS, e.g. H1:1-6, S7:4, H5:1,3,5 or H2:all.
With no SPEC, runs the smoke set: one representative rung of every test.

The scope settles once for the whole suite; each rung then costs the TBC
lock time plus the capture. Each run is reported and then deleted, as in
run_test.py; --keep keeps them (about 2 bytes x rate x seconds per channel
each, so mind the disk with long suites). --channels is as in run_test.py.
"""
import argparse, json, os, re, time

import dwf_scope
import perturb_model as pm
from gen_cmd import open_gen, request
from run_test import add_common_args, dut_of, finish, run_one, write_html

SMOKE = ["V0:1", "H1:4", "H2:3", "H3:5", "H4:5", "H5:4", "F1:3", "F2:5", "F3:2",
         "S1:5", "S2:3", "S3:5", "S4:3", "S4:6", "S5:1", "S6:5", "S6:6", "S7:4", "S8:5", "S9:4",
         "C1:3", "C2:1", "C3:3", "C4:3", "L1:4", "L2:3", "L3:3", "L4:4"]


def rungs_of(spec, catalog):
    test, _, r = spec.partition(":")
    if r in ("", "all"):
        return [(test, i) for i in range(1, catalog[test.upper()] + 1)]
    out = []
    for part in r.split(","):
        lo, _, hi = part.partition("-")
        out += [(test, i) for i in range(int(lo), int(hi or lo) + 1)]
    return out


def save_results(results, dut):
    """Every run's analysis (minus the plotted waveform) as JSON, for compare_suites.py."""
    reports = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports")
    os.makedirs(reports, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", dut.get("name") or "dut").strip("-").lower()
    path = os.path.join(reports, time.strftime("%Y%m%d-%H%M%S") + f"_suite_{slug}.json")
    keep = [{k: v for k, v in r.items() if k != "excerpt"} for r in results if r]
    with open(path, "w") as f:
        json.dump({"dut": dut, "runs": keep}, f)
    return os.path.normpath(path)


def loss_tolerance(results, tid, key="handled"):
    # Runs where the TBC output was dead say nothing about tolerance: skip them.
    results = [r for r in results if r and not r.get("dead")]
    """Largest N such that every S10/S11 rung up to N fields was `key` ('handled': no
    fallback or sync loss; 'clean': also no glitches). 'inf (>10)' beyond 10."""
    runs = sorted((r for r in results if r and r["test"]["id"] == tid and r.get("loss")),
                  key=lambda r: r["loss"]["fields_lost"])
    if not runs:
        return None
    best = 0
    for r in runs:
        L = r["loss"]
        if not L["gap_captured"]:
            continue
        if not L[key]:
            break
        best = L["fields_lost"]
    return "inf (>10)" if best > 10 else best


def tolerance_lines(results):
    out = []
    for tid, what in (("S10", "no signal"), ("S11", "snow")):
        t = loss_tolerance(results, tid)
        if t is not None:
            c = loss_tolerance(results, tid, "clean")
            out.append(f"lost fields ({what}): rides through up to {t} in a row without falling back to its "
                       f"own pattern or losing sync; cleanly (no garbled fields) up to {c}")
    return out


def input_lines(results):
    """Suite-level notes from CH2: rig faults, a missing reference, and the TBC's latency."""
    out, withref = [], [r for r in results if r and r.get("input")]
    if not withref:
        return out
    absent = [r for r in withref if not r["input"]["present"]]
    if len(absent) == len(withref):
        return ["WARNING: CH2 showed no video in any run. Is the reference connected? "
                "(Use --channels 1 on a single-channel rig.)"]
    bad = [f"{r['test']['id']} r{r['test']['rung']}" for r in withref if r["input"]["present"] and not r["input"]["ok"]]
    if bad:
        out.append(f"WARNING: the generator's own output (CH2) looked wrong in {len(bad)} run(s): {', '.join(bad)}. "
                   "Those results say more about the rig than the TBC.")
    lat = [r["input"]["latency"]["median_ms"] for r in withref if r["input"].get("latency")]
    if lat:
        out.append(f"latency (TBC input to output): {min(lat):.2f}–{max(lat):.2f} ms over {len(lat)} runs "
                   f"({min(lat) / pm.FIELD_MS:.2f}–{max(lat) / pm.FIELD_MS:.2f} fields)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", nargs="*")
    add_common_args(ap)
    a = ap.parse_args()
    dut = dut_of(a)

    t0 = time.time()
    with dwf_scope.Scope(a.rate, a.seconds, channels=a.channels) as scope:
        s = open_gen()
        # Rung counts from the firmware's catalog: "  H1  random line jitter  rms_ns: 5 10 20 ..."
        catalog = {}
        for line in request(s, "tests").splitlines():
            f = line.split()
            if f and ":" in line and f[0][0].isalpha() and f[0][1:].isdigit():
                catalog[f[0]] = len(line.rsplit(":", 1)[1].split())  # the ladder follows the last colon
        plan = [r for spec in (a.specs or SMOKE) for r in rungs_of(spec, catalog)]
        size = f", keeping about {2 * a.channels * a.rate * a.seconds * len(plan) / 1e9:.1f} GB of captures" if a.keep else ""
        print(f"{len(plan)} runs{size}")
        results = []
        for i, (test, rung) in enumerate(plan, 1):
            t = time.time()
            run, _ = run_one(s, scope, test, rung, a.seed, a.settle, a.out, dut, (a.rate, a.seconds))
            print(f"[{i}/{len(plan)}] {test} r{rung}: {time.time() - t:.1f} s")
            results.append(finish(run, a.keep))
        request(s, "test V0", until="\n")
    print(f"done in {time.time() - t0:.1f} s")
    path = save_results(results, dut)
    print(f"results: {path}")
    for line in tolerance_lines(results):
        print(line)
    for line in input_lines(results):
        print(line)
    dead = [f"{r['test']['id']} r{r['test']['rung']}" for r in results if r and r.get("dead")]
    if dead:
        print(f"WARNING: TBC output was dead (no video sync) during {len(dead)} run(s): {', '.join(dead)}")
    write_html(results, a.html)


if __name__ == "__main__":
    main()
