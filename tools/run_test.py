"""Run one test rung on the generator, capture CH1 (and CH2) and report on it.

usage: run_test.py TEST [RUNG] [--channels 1|2] [--dut NAME] [--note TEXT] [--keep]
                   [--html [PATH]] [--seed N] [--rate HZ] [--seconds S] [--out DIR]

--channels 2 (the default) captures CH1, the TBC's output, and CH2, the
generator's output teed off before the TBC. CH2 checks that the input was
right, measures the TBC's latency and its gain against the real input.
--channels 1 (or $TBC_CHANNELS=1) needs only CH1: for single-channel scopes,
or rigs without a tee. Pictures are then judged against nominal levels.

--dut names the device under test (default $TBC_DUT) and --note records its
settings (default $TBC_NOTE); both go into the manifest and reports.

Each run is saved to DIR/<timestamp>_<test>_r<rung>/ containing
  manifest.json  the generator's manifest plus capture metadata
  capture.npz    ch1 and, with two channels, ch2 (int16 raw), scale, offset, rate
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
import html_report
from observe_tbc import report

RUNS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "runs")

# Tests whose events recur slowly need longer captures (rate, seconds):
# S10/S11 lose fields once a second, X1's stress-recovery cycle is 3 s.
CAPTURE_HINTS = {"S10": (25e6, 1.3), "S11": (25e6, 1.3), "X1": (12.5e6, 3.3)}


def add_common_args(ap):
    """Options shared by run_test.py and run_suite.py."""
    ap.add_argument("--dut", default=os.environ.get("TBC_DUT", ""), help="device under test (default $TBC_DUT)")
    ap.add_argument("--note", default=os.environ.get("TBC_NOTE", ""), help="DUT settings etc. (default $TBC_NOTE)")
    ap.add_argument("--channels", type=int, choices=(1, 2), default=int(os.environ.get("TBC_CHANNELS", 2)),
                    help="2: CH1 = TBC output, CH2 = generator output (reference); "
                         "1: CH1 only (default $TBC_CHANNELS, else 2)")
    ap.add_argument("--keep", action="store_true", help="keep the capture and manifest instead of deleting them")
    ap.add_argument("--html", nargs="?", const="auto", help="also write an HTML report (default reports/<time>_<dut>.html)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--rate", type=float, default=50e6)
    ap.add_argument("--seconds", type=float, default=0.5)
    ap.add_argument("--settle", type=float, default=2.0, help="seconds for the TBC to lock before capturing")
    ap.add_argument("--out", default=RUNS)


def dut_of(a):
    if not a.dut:
        print("note: no --dut given (set TBC_DUT to name the device under test once per session)")
    return {"name": a.dut, "notes": a.note}


def ask_json(s, cmd, until, tries=3):
    """Send a command whose reply is one JSON line; retry if the reply arrives garbled."""
    for attempt in range(tries):
        reply = request(s, cmd, until=until)
        line = next((l for l in reply.splitlines() if l.startswith("{")), "")
        try:
            return json.loads(line)
        except ValueError:
            if attempt == tries - 1:
                raise RuntimeError(f"no valid JSON from '{cmd}' after {tries} tries: {reply[:200]!r}")


def run_one(s, scope, test, rung, seed=1, settle=2.0, out=RUNS, dut=None, timebase=None):
    """Start a test rung, wait `settle` s for the TBC to lock, capture, save. Returns the run dir.

    `timebase` (rate, seconds) is the capture for ordinary tests; tests in
    CAPTURE_HINTS use their own."""
    if timebase:
        scope.set_timebase(*CAPTURE_HINTS.get(test.upper(), timebase))
    reply = request(s, f"test {test} {rung} {seed}", until="\n", timeout=5)
    if "ok" not in reply:
        raise RuntimeError(f"generator refused 'test {test} {rung}': {reply.strip()}")
    started = time.time()
    manifest = ask_json(s, "manifest", "}}\n")
    remaining = settle - (time.time() - started)
    if remaining > 0:
        time.sleep(remaining)
    info = ask_json(s, "info", "}\n")
    if info["frames"] < 10:  # ~33 ms a frame: after the settle it should be well past this
        raise RuntimeError(f"generator is not advancing frames (frames={info['frames']} after {settle} s); "
                           "check `diag`, or reset the ESP32")

    t0 = time.time()
    cap = scope.capture()
    after = ask_json(s, "info", "}\n")
    restarted = after.get("dma_restarts", 0) - info.get("dma_restarts", 0)
    if restarted:
        print(f"  WARNING: the generator's DMA was restarted {restarted}x during this capture "
              f"({after.get('dma_restart_last', '')}); part of it may be frozen output")
    manifest["capture"] = {
        "rate_hz": cap["rate"], "samples": int(cap["raw"].shape[1]),
        "channels": {"ch1": "result (end of chain, after TBC)", "ch2": "reference (generator output)"}
                    if cap["raw"].shape[0] > 1 else {"ch1": "result (end of chain, after TBC)"},
        "volts": "raw * scale + offset", "started_unix": t0, "frames_at_start": info["frames"],
        "dma_restarts_during": restarted,
    }
    manifest["generator_state"] = info
    manifest["dut"] = dut or {"name": "", "notes": ""}

    run = os.path.join(out, time.strftime("%Y%m%d-%H%M%S") + f"_{test}_r{rung}")
    os.makedirs(run, exist_ok=True)
    with open(os.path.join(run, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    chans = {f"ch{c + 1}": cap["raw"][c] for c in range(cap["raw"].shape[0])}
    np.savez(os.path.join(run, "capture.npz"), **chans, scale=cap["scale"], offset=cap["offset"], rate=cap["rate"])
    return os.path.normpath(run), cap


def finish(run, keep=False):
    """Print the run's report, then delete it unless `keep` (or the report failed).

    Returns the analysis (for HTML reports), or None if it failed.
    """
    try:
        result = report(run)
    except Exception as e:  # keep the evidence when something goes wrong
        print(f"  report failed ({type(e).__name__}: {e}); kept {run}")
        return None
    if keep:
        print(f"  kept {run}")
    else:
        shutil.rmtree(run)
    return result


def write_html(results, dest, grades=None):
    results = [r for r in results if r]
    if dest and results:
        print(f"HTML report: {html_report.write(results, None if dest == 'auto' else dest, grades=grades)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("test")
    ap.add_argument("rung", nargs="?", type=int, default=1)
    add_common_args(ap)
    a = ap.parse_args()
    dut = dut_of(a)

    with dwf_scope.Scope(a.rate, a.seconds, channels=a.channels) as scope:  # settles while the test starts
        s = open_gen()
        run, cap = run_one(s, scope, a.test, a.rung, a.seed, a.settle, a.out, dut, (a.rate, a.seconds))
    for ch in range(cap["raw"].shape[0]):
        v = dwf_scope.volts(cap, ch)
        print(f"ch{ch + 1}: {v.min():.3f} .. {v.max():.3f} V")
    write_html([finish(run, a.keep)], a.html)


if __name__ == "__main__":
    main()
