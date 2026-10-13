"""Run one test rung on the generator and capture CH1/CH2 with its manifest.

usage: run_test.py TEST [RUNG] [--seed N] [--rate HZ] [--seconds S] [--out DIR]

Writes DIR/<timestamp>_<test>_r<rung>/ containing
  manifest.json  the generator's manifest plus capture metadata
  capture.npz    ch1, ch2 (int16 raw), scale, offset, rate
"""
import argparse, json, os, time

import numpy as np
import serial

import dwf_scope
from gen_cmd import port


def gen(s, cmd, until="\n", timeout=10):
    s.reset_input_buffer()
    s.write((cmd + "\n").encode())
    buf, t = "", time.time()
    while until not in buf and time.time() - t < timeout:
        buf += s.read(65536).decode(errors="replace")
    return buf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("test")
    ap.add_argument("rung", nargs="?", type=int, default=1)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--rate", type=float, default=50e6)
    ap.add_argument("--seconds", type=float, default=0.5)
    ap.add_argument("--settle", type=float, default=2.0, help="seconds for the TBC to lock before capturing")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "runs"))
    a = ap.parse_args()

    s = serial.Serial(port(), 115200, timeout=0.2)
    time.sleep(0.2)
    reply = gen(s, f"test {a.test} {a.rung} {a.seed}", until="\n", timeout=5)
    if "ok" not in reply:
        raise SystemExit(f"generator refused test: {reply.strip()}")
    time.sleep(a.settle)
    manifest = json.loads(next(l for l in gen(s, "manifest", until="}}\n").splitlines() if l.startswith("{")))
    info = json.loads(next(l for l in gen(s, "info", until="}\n").splitlines() if l.startswith("{")))

    t0 = time.time()
    cap = dwf_scope.capture(rate=a.rate, seconds=a.seconds)
    manifest["capture"] = {
        "rate_hz": cap["rate"], "samples": int(cap["raw"].shape[1]),
        "channels": {"ch1": "result (end of chain, after TBC)", "ch2": "reference (generator output)"},
        "volts": "raw * scale + offset", "started_unix": t0, "frames_at_start": info["frames"],
    }
    manifest["generator_state"] = info

    run = os.path.join(a.out, time.strftime("%Y%m%d-%H%M%S") + f"_{a.test}_r{a.rung}")
    os.makedirs(run, exist_ok=True)
    with open(os.path.join(run, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    np.savez(os.path.join(run, "capture.npz"), ch1=cap["raw"][0], ch2=cap["raw"][1],
             scale=cap["scale"], offset=cap["offset"], rate=cap["rate"])
    for ch in (0, 1):
        v = dwf_scope.volts(cap, ch)
        print(f"ch{ch + 1}: {v.min():.3f} .. {v.max():.3f} V")
    print(f"saved {run}")


if __name__ == "__main__":
    main()
