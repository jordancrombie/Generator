"""Run one test rung on the generator, capture CH1/CH2 and report on it.

usage: run_test.py TEST [RUNG] [--keep] [--seed N] [--rate HZ] [--seconds S] [--out DIR]

Each run is saved to DIR/<timestamp>_<test>_r<rung>/ containing
  manifest.json  the generator's manifest plus capture metadata
  capture.npz    ch1, ch2 (int16 raw), scale, offset, rate
then observe_tbc's report is printed and the folder is deleted, unless
--keep is given (or the report fails, so the run can be debugged). A capture
is about 100 MB per 0.5 s at 50 MS/s.

The scope's input settling and the TBC's lock time overlap: the scope is
opened first, then the test starts, and the capture waits for whichever is
later. run_suite.py runs many rungs on one scope session.
"""
import argparse, json, os, shutil, time

import numpy as np

import dwf_scope
from gen_cmd import open_gen, request
from observe_tbc import report

RUNS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "runs")


def run_one(s, scope, test, rung, seed=1, settle=2.0, out=RUNS):
    """Start a test rung, wait `settle` s for the TBC to lock, capture, save. Returns the run dir."""
    reply = request(s, f"test {test} {rung} {seed}", until="\n", timeout=5)
    if "ok" not in reply:
        raise RuntimeError(f"generator refused 'test {test} {rung}': {reply.strip()}")
    started = time.time()
    manifest = json.loads(next(l for l in request(s, "manifest", until="}}\n").splitlines() if l.startswith("{")))
    remaining = settle - (time.time() - started)
    if remaining > 0:
        time.sleep(remaining)
    info = json.loads(next(l for l in request(s, "info", until="}\n").splitlines() if l.startswith("{")))

    t0 = time.time()
    cap = scope.capture()
    manifest["capture"] = {
        "rate_hz": cap["rate"], "samples": int(cap["raw"].shape[1]),
        "channels": {"ch1": "result (end of chain, after TBC)", "ch2": "reference (generator output)"},
        "volts": "raw * scale + offset", "started_unix": t0, "frames_at_start": info["frames"],
    }
    manifest["generator_state"] = info

    run = os.path.join(out, time.strftime("%Y%m%d-%H%M%S") + f"_{test}_r{rung}")
    os.makedirs(run, exist_ok=True)
    with open(os.path.join(run, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    np.savez(os.path.join(run, "capture.npz"), ch1=cap["raw"][0], ch2=cap["raw"][1],
             scale=cap["scale"], offset=cap["offset"], rate=cap["rate"])
    return os.path.normpath(run), cap


def finish(run, keep=False):
    """Print the run's report, then delete it unless `keep` (or the report failed)."""
    try:
        report(run)
    except Exception as e:  # keep the evidence when something goes wrong
        print(f"  report failed ({type(e).__name__}: {e}); kept {run}")
        return
    if keep:
        print(f"  kept {run}")
    else:
        shutil.rmtree(run)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("test")
    ap.add_argument("rung", nargs="?", type=int, default=1)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--rate", type=float, default=50e6)
    ap.add_argument("--seconds", type=float, default=0.5)
    ap.add_argument("--settle", type=float, default=2.0, help="seconds for the TBC to lock before capturing")
    ap.add_argument("--out", default=RUNS)
    ap.add_argument("--keep", action="store_true", help="keep the capture and manifest instead of deleting them")
    a = ap.parse_args()

    with dwf_scope.Scope(a.rate, a.seconds) as scope:  # starts settling while the test starts
        s = open_gen()
        run, cap = run_one(s, scope, a.test, a.rung, a.seed, a.settle, a.out)
    for ch in (0, 1):
        v = dwf_scope.volts(cap, ch)
        print(f"ch{ch + 1}: {v.min():.3f} .. {v.max():.3f} V")
    finish(run, a.keep)


if __name__ == "__main__":
    main()
