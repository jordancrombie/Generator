"""Dropout watch: does the TBC's output go dead, how often, and for how long?

usage: dropout_watch.py [--stimulus S11:2] [--baseline 60] [--stress 180] [--recovery 60]
                        [--interval 1.0] [--dut NAME] [--note TEXT] [--html [PATH]]

Three phases: clean V0 (control), a stimulus test that repeatedly cuts the
input (default S11 rung 2: two fields of snow every second), then clean V0
again. Once a second the scope takes a short capture of CH1 and classifies
the TBC's output:

  ours   video sync and our frame marker
  alive  video sync but no marker (black, or the TBC's own pattern)
  dead   no valid video sync at all (the TBC isn't outputting video)

Dead samples in a row form an episode; the report gives each episode's start
(time and phase) and length, and the share of dead samples per phase. Saves
reports/<time>_dropout_<dut>.json (and .html with --html).
"""
import argparse, json, os, re, time

import numpy as np

import dwf_scope
import perturb_model as pm
from gen_cmd import open_gen, request
from observe_tbc import sync_pulses
from run_test import ask_json

REPORTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports")
FH = pm.FH_HZ


def quick_state(x, fs, want):
    """Classify a short CH1 capture: 'ours', 'alive' or 'dead' (see module doc)."""
    us = fs / 1e6
    out = {"hsyncs": 0, "vsyncs": 0, "ptp_v": float(np.ptp(x))}
    if out["ptp_v"] < 0.2:
        return dict(out, state="dead")
    ft, w = sync_pulses(x, us)
    hs = ft[(w > 3.5) & (w < 6)]
    broad = ft[w > 20]
    vs = broad[np.concatenate([[True], np.diff(broad) > 100 * us])] if len(broad) else broad
    out["hsyncs"], out["vsyncs"] = int(len(hs)), int(len(vs))
    period_ok = len(hs) > 2 and abs(np.median(np.diff(hs)) / us - 63.556) < 1.0
    if len(hs) < 0.5 * len(x) / fs * FH or not period_ok or len(vs) == 0:
        return dict(out, state="dead")
    # Our marker: first 40 lines of each field, offsets within +-2 us.
    tip = np.median([x[int(s + 1.5 * us):int(s + 3.5 * us)].mean() for s in hs[:300]])
    blank = np.median([x[int(s + 8.2 * us):int(s + 9.1 * us)].mean() for s in hs[:300]])
    thr = blank + 38.75 * (blank - tip) / 40.0
    k = max(1, int(0.8 * us))
    lp = np.convolve(x, np.ones(k, np.float32) / k, mode="same")
    cell_t = (10.5 + 1.4 * (np.arange(36) + 0.5)) * us
    shifts = np.arange(-2.0, 2.01, 0.1) * us
    for v0 in vs:
        for s in hs[hs > v0][:40]:
            pos = (s + cell_t[None, :] + shifts[:, None]).astype(int)
            if pos.max() >= len(lp):
                break
            bits = (lp[pos] > thr).astype(np.int8)
            cand = (bits[:, 0] == 1) & (bits[:, 1] == 0) & (bits[:, -2] == 0) & (bits[:, -1] == 1)
            for j in np.where(cand)[0]:
                d = pm.decode_marker(bits[j])
                if d and d[:2] == want:
                    return dict(out, state="ours", frame=int(d[2]))
    return dict(out, state="alive")


def episodes(samples):
    """Runs of consecutive dead samples."""
    eps, cur = [], None
    for s in samples:
        if s["state"] == "dead":
            if cur is None:
                cur = {"start_s": s["t"], "phase": s["phase"], "samples": 0}
            cur["samples"] += 1
            cur["end_s"] = s["t"]
        elif cur is not None:
            cur["ended_by"] = s["t"]
            eps.append(cur)
            cur = None
    if cur is not None:
        cur["ended_by"] = None   # still dead when the watch ended
        eps.append(cur)
    for e in eps:
        # Length: from the first dead sample to the first live one after it
        # (or the last dead one, if it never came back).
        e["length_s"] = round((e["ended_by"] if e["ended_by"] is not None else e["end_s"]) - e["start_s"], 2)
    return eps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stimulus", default="S11:2", help="TEST:RUNG that cuts the input (default S11:2)")
    ap.add_argument("--baseline", type=float, default=60, help="seconds of clean V0 first (control)")
    ap.add_argument("--stress", type=float, default=180, help="seconds of the stimulus")
    ap.add_argument("--recovery", type=float, default=60, help="seconds of clean V0 after")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between samples")
    ap.add_argument("--window", type=float, default=0.05, help="capture length per sample, s (0.05 = 3 fields)")
    ap.add_argument("--rate", type=float, default=10e6)
    ap.add_argument("--dut", default=os.environ.get("TBC_DUT", ""))
    ap.add_argument("--note", default=os.environ.get("TBC_NOTE", ""))
    ap.add_argument("--html", nargs="?", const="auto")
    a = ap.parse_args()
    stim_id, _, stim_rung = a.stimulus.partition(":")
    phases = [("baseline", "V0", 1, a.baseline), ("stress", stim_id.upper(), int(stim_rung or 1), a.stress),
              ("recovery", "V0", 1, a.recovery)]

    samples, t0 = [], None
    with dwf_scope.Scope(a.rate, a.window, channels=1) as scope:  # CH1 only: works on any rig
        s = open_gen()
        for phase, tid, rung, dur in phases:
            if dur <= 0:
                continue
            if "ok" not in request(s, f"test {tid} {rung} 1", until="\n", timeout=5):
                raise SystemExit(f"generator refused 'test {tid} {rung}'")
            man = ask_json(s, "manifest", "}}\n")
            want = (int(man["test"]["code"]), int(man["test"]["rung"]))
            if t0 is None:
                t0 = time.time()
            end = time.time() + dur
            print(f"{phase}: {tid} r{rung} for {dur:g} s")
            while time.time() < end:
                tick = time.time()
                cap = scope.capture()
                st = quick_state(dwf_scope.volts(cap, 0), cap["rate"], want)
                st.update(t=round(tick - t0, 2), phase=phase, test=f"{tid} r{rung}")
                samples.append(st)
                if st["state"] == "dead" or (len(samples) > 1 and samples[-2]["state"] == "dead"):
                    print(f"  {st['t']:7.1f} s  {st['state']}")
                time.sleep(max(0.0, a.interval - (time.time() - tick)))
        info = ask_json(s, "info", "}\n")
        request(s, "test V0", until="\n")

    eps = episodes(samples)
    per_phase = {}
    for ph, *_ in phases:
        ss = [x for x in samples if x["phase"] == ph]
        if ss:
            per_phase[ph] = {"samples": len(ss), "dead": sum(x["state"] == "dead" for x in ss),
                             "alive_no_marker": sum(x["state"] == "alive" for x in ss),
                             "ours": sum(x["state"] == "ours" for x in ss),
                             "episodes_started": sum(e["phase"] == ph for e in eps)}
            per_phase[ph]["dead_pct"] = round(100.0 * per_phase[ph]["dead"] / len(ss), 1)
    result = {"kind": "dropout_watch", "dut": {"name": a.dut, "notes": a.note},
              "stimulus": a.stimulus.upper(), "phases": [{"name": p, "test": f"{t} r{r}", "seconds": d} for p, t, r, d in phases],
              "interval_s": a.interval, "window_s": a.window, "started": time.strftime("%Y-%m-%d %H:%M", time.localtime(t0)),
              "generator": {"sample_hz": info.get("sample_hz"), "dma_restarts": info.get("dma_restarts"),
                            "levels": [info.get("sync_code"), info.get("white_code")]},
              "samples": samples, "episodes": eps, "per_phase": per_phase}

    print(f"\n{len(eps)} dead episode(s):")
    for e in eps:
        print(f"  at {e['start_s']:.1f} s ({e['phase']}), {e['length_s']:.1f} s"
              f"{'' if e['ended_by'] is not None else ', still dead at the end'}")
    for ph, d in per_phase.items():
        print(f"  {ph:8s}: {d['dead']}/{d['samples']} samples dead ({d['dead_pct']} %), "
              f"{d['episodes_started']} episode(s) started")

    os.makedirs(REPORTS, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", a.dut).strip("-").lower() or "dut"
    base = os.path.join(REPORTS, time.strftime("%Y%m%d-%H%M%S") + f"_dropout_{slug}")
    with open(base + ".json", "w") as f:
        json.dump(result, f, indent=1)
    print(f"saved {os.path.normpath(base)}.json")
    if a.html:
        import html_report
        path = html_report.write_watch(result, None if a.html == "auto" else a.html, base + ".html")
        print(f"HTML report: {path}")


if __name__ == "__main__":
    main()
