"""Run several test rungs on one scope session and report on each.

usage: run_suite.py [SPEC ...] [--keep] [--rate HZ] [--seconds S] [--settle S]

SPEC is TEST:RUNGS, e.g. H1:1-6, S7:4, H5:1,3,5 or H2:all.
With no SPEC, runs the smoke set: one representative rung of every test.

The scope settles once for the whole suite; each rung then costs the TBC
lock time plus the capture. Each run is reported and then deleted, as in
run_test.py; --keep keeps them (about 4 bytes x rate x seconds each, two
int16 channels, so mind the disk with long suites).
"""
import argparse, time

import dwf_scope
from gen_cmd import open_gen, request
from run_test import RUNS, finish, run_one

SMOKE = ["V0:1", "H1:4", "H2:3", "H3:5", "H4:5", "H5:4", "F1:3", "S3:5", "S5:1", "S6:5", "S6:6", "S7:4"]


def rungs_of(spec, catalog):
    test, _, r = spec.partition(":")
    if r in ("", "all"):
        return [(test, i) for i in range(1, catalog[test.upper()] + 1)]
    out = []
    for part in r.split(","):
        lo, _, hi = part.partition("-")
        out += [(test, i) for i in range(int(lo), int(hi or lo) + 1)]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", nargs="*")
    ap.add_argument("--keep", action="store_true", help="keep each run's capture and manifest instead of deleting them")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--rate", type=float, default=50e6)
    ap.add_argument("--seconds", type=float, default=0.5)
    ap.add_argument("--settle", type=float, default=2.0, help="seconds for the TBC to lock before capturing")
    ap.add_argument("--out", default=RUNS)
    a = ap.parse_args()

    t0 = time.time()
    with dwf_scope.Scope(a.rate, a.seconds) as scope:
        s = open_gen()
        # Rung counts from the firmware's catalog: "  H1  random line jitter  rms_ns: 5 10 20 ..."
        catalog = {}
        for line in request(s, "tests").splitlines():
            f = line.split()
            if f and ":" in line and len(f[0]) == 2 and f[0][1].isdigit():
                catalog[f[0]] = len(line.split(":", 1)[1].split())
        plan = [r for spec in (a.specs or SMOKE) for r in rungs_of(spec, catalog)]
        size = f", keeping about {4 * a.rate * a.seconds * len(plan) / 1e9:.1f} GB of captures" if a.keep else ""
        print(f"{len(plan)} runs{size}")
        for i, (test, rung) in enumerate(plan, 1):
            t = time.time()
            run, _ = run_one(s, scope, test, rung, a.seed, a.settle, a.out)
            print(f"[{i}/{len(plan)}] {test} r{rung}: {time.time() - t:.1f} s")
            finish(run, a.keep)
        request(s, "test V0", until="\n")
    print(f"done in {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
