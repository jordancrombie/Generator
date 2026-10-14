"""Compare several suite runs (the JSON run_suite.py saves) test by test.

usage: compare_suites.py SUITE.json [SUITE.json ...] [--html PATH]

For each test rung: whether the TBC output was dead, the frame sequence,
lost-field and fault-hunt verdicts, and key picture figures, side by side.
Then a timeline of dead-output runs across all suites (for finding what
precedes a dropout), and the lost-field tolerance of each suite.
"""
import argparse, json, os, sys, time
from collections import OrderedDict

from run_suite import loss_tolerance


def key_of(r):
    return f"{r['test']['id']} r{r['test']['rung']}"


def cell(r):
    """A short verdict for one run of one test."""
    if r is None:
        return "—"
    if r.get("dead"):
        return "DEAD"
    parts = []
    L, H = r.get("loss"), r.get("hunt")
    if L:
        parts.append("no gap" if not L["gap_captured"] else "clean" if L["clean"] else
                     L["how"] if L["handled"] else "FAIL: " + L["how"])
    elif H:
        parts.append("after clean" if H["after_clean"] else "after NOT clean" if H["after_clean"] is False else "n/a")
        parts.append(f"{H['stuck_or_ghost']} stuck/ghost")
    else:
        ev = r["markers"]["events"]
        bad = [f"{ev[k]} {n}" for k, n in (("backward_steps", "back"), ("skipped_frames", "skip"),
                                          ("repeats", "rep"), ("single_field_frames", "single")) if ev.get(k)]
        parts.append(" ".join(bad) if bad else "in order" if r["markers"]["decoded"] else "no markers")
    fs = r.get("field_summary") or {}
    extra = [f"{fs[k]} {k}" for k in ("garbled", "fallback", "blend", "ghost") if fs.get(k)]
    if extra:
        parts.append(", ".join(extra))
    pc = r.get("picture")
    if pc and not L and not H:
        parts.append(f"gray {pc['gray_ire']:.0f}, hue {pc['yellow_hue_deg']:.0f}±{pc['yellow_hue_jitter_deg']:.0f}")
    return "; ".join(parts)


def verdict(r):
    """The categorical outcome of a run, for spotting real differences between suites."""
    if r is None:
        return None
    if r.get("dead"):
        return "dead"
    if r.get("loss"):
        L = r["loss"]
        return ("no gap",) if not L["gap_captured"] else (L["handled"], L["how"])
    if r.get("hunt"):
        return ("after_clean", r["hunt"]["after_clean"])
    ev = r["markers"]["events"]
    return tuple(k for k in ("backward_steps", "skipped_frames", "repeats", "single_field_frames") if ev.get(k))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("suites", nargs="+")
    ap.add_argument("--html")
    a = ap.parse_args()
    suites = [json.load(open(p)) for p in a.suites]
    names = [os.path.basename(p).split("_suite_")[0] for p in a.suites]

    tests = OrderedDict()
    for i, s in enumerate(suites):
        for r in s["runs"]:
            tests.setdefault(key_of(r), [None] * len(suites))[i] = r

    rows = []
    for k, rs in tests.items():
        cells = [cell(r) for r in rs]
        # Differs = the verdict changed (dead, pass/fail, how, frame-sequence events),
        # not just the measured numbers or counts of garbled fields.
        verdicts = [verdict(r) for r in rs]
        differs = len(set(verdicts)) > 1
        rows.append((k, cells, differs))

    # Dead-output timeline: which runs were dead, and what ran just before.
    dead = []
    for i, s in enumerate(suites):
        runs = s["runs"]
        streak = None
        for j, r in enumerate(runs):
            if r.get("dead") and streak is None:
                streak = {"suite": names[i], "first": key_of(r), "before": key_of(runs[j - 1]) if j else "(start)",
                          "t": r.get("started_unix"), "runs": 1}
            elif r.get("dead"):
                streak["runs"] += 1
                streak["last"] = key_of(r)
                streak["t_last"] = r.get("started_unix")
            elif streak is not None:
                streak["recovered_at"] = key_of(r)
                streak["t_back"] = r.get("started_unix")
                dead.append(streak)
                streak = None
        if streak is not None:
            streak["recovered_at"] = None
            dead.append(streak)

    tol = []
    for i, s in enumerate(suites):
        rs = s["runs"]
        # Output sync depth (V) over the suite's live runs: a sag here preceded dead output.
        sd = sorted(r["level"]["sync_depth_v"] for r in rs
                    if r.get("level") and r["level"].get("sync_depth_v") and not r.get("dead"))
        lvl = f"{sd[len(sd) // 2]:.3f} (min {sd[0]:.3f})" if sd else "—"
        lat = sorted(r["input"]["latency"]["median_ms"] for r in rs if (r.get("input") or {}).get("latency"))
        lvl += f"; latency {lat[0]:.2f}–{lat[-1]:.2f} ms" if lat else ""
        tol.append((names[i], loss_tolerance(rs, "S10"), loss_tolerance(rs, "S11"), lvl))

    # ---- console
    w = max(len(k) for k in tests) + 2
    print("Lost-field tolerance (no signal / snow); output sync depth, V (median, min); latency (two-channel runs):")
    for n, a10, a11, lvl in tol:
        print(f"  {n}: {a10} / {a11};  {lvl}")
    print(f"\nDead-output episodes: {len(dead)}")
    for d in dead:
        span = (f"{d['runs']} run(s), about {d['t_back'] - d['t']:.0f} s" if d.get("t_back") and d.get("t")
                else f"{d['runs']} run(s), not recovered by the end")
        print(f"  {d['suite']}: started at {d['first']} (after {d['before']}), {span}; "
              f"recovered at {d['recovered_at'] or '—'}")
    print(f"\nTests whose verdict differs between suites ({sum(r[2] for r in rows)} of {len(rows)}):")
    for k, cells, differs in rows:
        if differs:
            print(f"  {k:<{w}}" + " | ".join(c[:46] for c in cells))

    if a.html:
        import html_report as hr
        head = "".join(f"<th>{hr.esc(n)}</th>" for n in names)
        body = "".join(
            f'<tr{" class=diff" if d else ""}><td>{"<b>" if d else ""}{hr.esc(k)}{"</b>" if d else ""}</td>'
            + "".join(f'<td style="{"color:var(--bad);font-weight:600" if c == "DEAD" or c.startswith("FAIL") else ""}">{hr.esc(c)}</td>' for c in cells)
            + "</tr>" for k, cells, d in rows)
        dead_rows = "".join(
            f"<tr><td>{hr.esc(d['suite'])}</td><td>{hr.esc(d['first'])}</td><td>{hr.esc(d['before'])}</td>"
            f"<td class=num>{d['runs']}</td><td class=num>{(f'{d['t_back'] - d['t']:.0f} s') if d.get('t_back') and d.get('t') else '—'}</td>"
            f"<td>{hr.esc(d['recovered_at'] or 'not by the end')}</td></tr>" for d in dead)
        tol_rows = "".join(f"<tr><td>{hr.esc(n)}</td><td>{hr.esc(a10)}</td><td>{hr.esc(a11)}</td><td class=num>{hr.esc(lvl)}</td></tr>" for n, a10, a11, lvl in tol)
        dut = suites[0].get("dut", {}).get("name") or "unspecified device"
        page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Suite comparison · {hr.esc(dut)}</title>
<style>{hr.CSS} tr.diff td {{ background: var(--card); }}</style></head><body><main>
<h1>Suite comparison</h1><p class="sub">Device under test: <b style="color:var(--fg)">{hr.esc(dut)}</b> ·
{len(suites)} suites · {time.strftime('%Y-%m-%d %H:%M')}</p>
<h2>Dead output</h2>
{'<div class="scroll"><table><thead><tr><th>Suite</th><th>First dead run</th><th>Run before it</th><th>Dead runs</th><th>Lasted</th><th>Back at</th></tr></thead><tbody>' + dead_rows + '</tbody></table></div>' if dead else '<p><span class="badge b-ok">no dead output in any suite</span></p>'}
<h2>Lost-field tolerance and output level</h2>
<div class="scroll"><table><thead><tr><th>Suite</th><th>No signal (S10)</th><th>Snow (S11)</th><th>Sync depth, V (median, min); latency</th></tr></thead><tbody>{tol_rows}</tbody></table></div>
<h2>Test by test</h2><p class="cap">Shaded rows differ between suites.</p>
<div class="scroll"><table><thead><tr><th>Test</th>{head}</tr></thead><tbody>{body}</tbody></table></div>
<footer>Generated by tools/compare_suites.py</footer></main></body></html>"""
        with open(a.html, "w") as f:
            f.write(page)
        print(f"\nHTML: {a.html}")


if __name__ == "__main__":
    main()
