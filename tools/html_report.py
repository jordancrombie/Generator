"""Write a self-contained HTML report from observe_tbc.analyze() results.

Used by run_test.py / run_suite.py / observe_tbc.py with --html. The page has
no external dependencies, follows the viewer's light/dark setting, and is
an observation report, not a grade.
"""
import datetime as dt
import html
import os
import re
from collections import Counter

REPORTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports")

CSS = """
:root { --bg:#ffffff; --fg:#1f2328; --muted:#59636e; --line:#d1d9e0; --card:#f6f8fa;
        --ok:#1a7f37; --warn:#9a6700; --bad:#cf222e; --info:#0969da; --cell:#e7ecf0; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --line:#3d444d; --card:#151b23;
          --ok:#3fb950; --warn:#d29922; --bad:#f85149; --info:#4493f8; --cell:#262c36; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
       font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 32px 20px 64px; }
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 40px 0 12px; }
h3 { font-size: 16px; margin: 0; }
.sub { color: var(--muted); margin: 0 0 16px; }
.meta { display: flex; flex-wrap: wrap; gap: 8px 24px; color: var(--muted); font-size: 13px; }
.meta b { color: var(--fg); font-weight: 600; }
.note { border-left: 3px solid var(--info); padding: 6px 12px; margin: 16px 0; color: var(--muted); }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; white-space: nowrap; }
td.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
a { color: var(--info); text-decoration: none; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; font-weight: 600;
         border: 1px solid currentColor; white-space: nowrap; }
.b-ok { color: var(--ok); } .b-warn { color: var(--warn); } .b-bad { color: var(--bad); } .b-info { color: var(--info); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 18px 20px; margin: 16px 0; }
.card header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px; margin-bottom: 10px; }
.ideal { display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; font-size: 13px; margin: 6px 0 14px; }
.ideal dt { color: var(--muted); } .ideal dd { margin: 0; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 14px; }
.tile { background: var(--bg); border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; }
.tile .k { color: var(--muted); font-size: 12px; }
.tile .v { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
.tile .u { color: var(--muted); font-size: 12px; font-weight: 400; }
.cap { color: var(--muted); font-size: 12px; margin: 4px 0 12px; }
svg { display: block; max-width: 100%; height: auto; }
svg text { fill: var(--muted); font-size: 11px; font-family: inherit; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: var(--muted); margin-top: 4px; }
.sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
footer { margin-top: 48px; color: var(--muted); font-size: 12px; }
"""

KIND_FILL = {"ok": "var(--cell)", "single": "var(--info)", "repeat": "var(--warn)", "back": "var(--warn)", "skip": "var(--bad)"}
FIELD_FILL = {"ours": "var(--cell)", "unmarked": "var(--cell)", "blank": "#57606a", "garbled": "var(--bad)",
              "fallback": "#8250df", "blend": "var(--warn)"}


def field_strip(log, cols=36):
    """SVG of every output field: whose picture it carried, its frame, ghosting, field pairing."""
    if not log:
        return ""
    cw, ch, gap = 26, 22, 3
    rows = (len(log) + cols - 1) // cols
    w, h = cols * (cw + gap), rows * (ch + gap)
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="What each output field carried">']
    prev = None
    for i, f in enumerate(log):
        x, y = (i % cols) * (cw + gap), (i // cols) * (ch + gap)
        fill = FIELD_FILL.get(f["class"], "var(--bad)")
        if f["class"] == "ours" and f["ghost"]:
            fill = "var(--warn)"
        elif f["class"] == "ours" and prev is not None and f["frame"] < prev:
            fill = "var(--warn)"           # went back to an older frame
        stroke = ' style="stroke:var(--fg);stroke-width:1.5"' if not f["standard"] else ""
        label = f["frame"] % 1000 if f["frame"] is not None else {"blank": "—", "garbled": "×", "fallback": "B",
                                                                    "unmarked": "·", "blend": "≈"}.get(f["class"], "?")
        txt = "var(--fg)" if fill == "var(--cell)" else "#ffffff"
        out.append(f'<rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="3" style="fill:{fill}"{stroke}>'
                   f'<title>field {i + 1} (parity {f["parity"]}, {f["lines"]} lines): {f["class"]}'
                   f'{", frame " + str(f["frame"]) if f["frame"] is not None else ""}{", ghosted" if f["ghost"] else ""}</title></rect>')
        out.append(f'<text x="{x + cw / 2}" y="{y + 15}" text-anchor="middle" style="fill:{txt};font-size:9.5px">{label}</text>')
        if f["class"] == "ours":
            prev = f["frame"]
    out.append("</svg>")
    legend = ('<div class="legend">'
              '<span><i class="sw" style="background:var(--cell)"></i>our picture (frame no.)</span>'
              '<span><i class="sw" style="background:var(--warn)"></i>ghosted / went back</span>'
              '<span><i class="sw" style="background:#57606a"></i>blank</span>'
              '<span><i class="sw" style="background:var(--bad)"></i>garbled</span>'
              '<span><i class="sw" style="background:#8250df"></i>TBC\'s own pattern (B)</span></div>')
    return "".join(out) + legend


def esc(s):
    return html.escape(str(s), quote=True)


def params_text(t):
    return ", ".join(f"{k} {v:g}" if isinstance(v, (int, float)) else f"{k} {v}" for k, v in t["params"].items()) or "—"


def targets_text(t):
    names = {"line_tbc": "line TBC", "frame_tbc": "frame TBC"}
    return " + ".join(names.get(x, x) for x in t.get("targets", [])) or "—"


def sequence_badge(r):
    m = r["markers"]
    if not m["decoded"]:
        return '<span class="badge b-bad">no markers</span>'
    ev = m["events"]
    parts = []
    if ev["backward_steps"]:
        parts.append(f"{ev['backward_steps']} back")
    if ev["repeats"]:
        parts.append(f"{ev['repeats']} repeated")
    if ev["skipped_frames"]:
        parts.append(f"{ev['skipped_frames']} skipped")
    if ev["single_field_frames"]:
        parts.append(f"{ev['single_field_frames']} single-field")
    if not parts:
        return '<span class="badge b-ok">in order</span>'
    cls = "b-bad" if ev["skipped_frames"] else "b-warn"
    return f'<span class="badge {cls}">{esc(" · ".join(parts))}</span>'


def vpos_badge(r, base):
    """Vertical position of the picture vs the suite's V0 baseline (if any)."""
    lv = r["markers"].get("line_after_vsync")
    if not lv or base is None:
        return ""
    d = lv["median"] - base
    if lv["max"] - lv["min"] > 2:
        return f' <span class="badge b-bad">picture moving vertically ({lv["min"]}–{lv["max"]})</span>'
    if abs(d) > 2:
        return f' <span class="badge b-warn">picture shifted {d:+.0f} lines</span>'
    return ""


def dma_badge(r):
    n = r.get("dma_restarts_during", 0)
    return f' <span class="badge b-bad">generator DMA restarted {n}× during capture</span>' if n else ""


def fields_badge(r):
    if r.get("dead"):
        return '<span class="badge b-bad">output dead</span>'
    fl = r["fields"]
    if fl["all_standard"]:
        return '<span class="badge b-ok">262.5 lines</span>'
    if not fl["lengths"]:
        return '<span class="badge b-bad">no V-sync</span>'
    common = ", ".join(f"{k}×{v}" for k, v in sorted(fl["lengths"].items(), key=lambda kv: -kv[1])[:3])
    return f'<span class="badge b-warn">{esc(common)}</span>'


def frame_strip(frames, cols=36):
    """SVG of the decoded frame counters, one cell per field, coloured by what happened."""
    if not frames:
        return ""
    count = Counter(frames)
    kinds = []
    for i, f in enumerate(frames):
        step = f - frames[i - 1] if i else 0
        if i and step < 0:
            k = "back"
        elif i and step > 1:
            k = "skip"
        elif 0 < i < len(frames) - 1 and count[f] == 1:
            k = "single"
        elif count[f] > 2:
            k = "repeat"
        else:
            k = "ok"
        kinds.append(k)
    cw, ch, gap = 26, 22, 3
    rows = (len(frames) + cols - 1) // cols
    w, h = cols * (cw + gap), rows * (ch + gap)
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Decoded frame counter per field">']
    for i, (f, k) in enumerate(zip(frames, kinds)):
        x, y = (i % cols) * (cw + gap), (i // cols) * (ch + gap)
        fill = KIND_FILL[k]
        txt = "var(--fg)" if k == "ok" else "#ffffff"
        out.append(f'<rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="3" style="fill:{fill}"><title>field {i + 1}: frame {f} ({k})</title></rect>')
        out.append(f'<text x="{x + cw / 2}" y="{y + 15}" text-anchor="middle" style="fill:{txt};font-size:9.5px">{f % 1000}</text>')
    out.append("</svg>")
    legend = ('<div class="legend">'
              '<span><i class="sw" style="background:var(--cell)"></i>field as expected</span>'
              '<span><i class="sw" style="background:var(--info)"></i>frame shown once (a field missing)</span>'
              '<span><i class="sw" style="background:var(--warn)"></i>repeated / went back</span>'
              '<span><i class="sw" style="background:var(--bad)"></i>frames skipped</span></div>')
    return "".join(out) + legend


def waveform(ex):
    """SVG of one captured line as a min/max envelope."""
    if not ex:
        return ""
    lo, hi = ex["min"], ex["max"]
    vmin, vmax = min(lo), max(hi)
    pad = 0.05 * (vmax - vmin or 1)
    vmin, vmax = vmin - pad, vmax + pad
    W, H, L, R, T, B = 760, 210, 44, 12, 10, 26
    n = len(lo)
    X = lambda i: L + (W - L - R) * i / (n - 1)
    Y = lambda v: T + (H - T - B) * (vmax - v) / (vmax - vmin)
    top = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(hi))
    bot = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in reversed(list(enumerate(lo))))
    t0, t1 = ex["t0_us"], ex["t1_us"]
    xt = lambda us: L + (W - L - R) * (us - t0) / (t1 - t0)
    g = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="One line of the output, voltage against time">']
    for us in range(0, 61, 10):
        g.append(f'<line x1="{xt(us):.1f}" x2="{xt(us):.1f}" y1="{T}" y2="{H - B}" style="stroke:var(--line)"/>')
        g.append(f'<text x="{xt(us):.1f}" y="{H - 8}" text-anchor="middle">{us} µs</text>')
    step = 0.25 if vmax - vmin < 1.5 else 0.5
    v = step * round(vmin / step)
    while v <= vmax:
        if v >= vmin:
            g.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" style="stroke:var(--line)"/>')
            g.append(f'<text x="{L - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:.2f} V</text>')
        v += step
    g.append(f'<polygon points="{top} {bot}" style="fill:var(--info);fill-opacity:.35;stroke:var(--info);stroke-width:.8"/>')
    g.append("</svg>")
    return "".join(g)


def picture_cells(r):
    pc = r.get("picture")
    if not pc:
        return '<td class="num">—</td><td class="num">—</td><td class="num">—</td>'
    return (f'<td class="num">{pc["burst_pp_ire"]:.0f} IRE</td>'
            f'<td class="num">{pc["yellow_hue_deg"]:.1f}° ±{pc["yellow_hue_jitter_deg"]:.1f}</td>'
            f'<td class="num">{pc["gray_ire"]:.1f} IRE</td>')


def fault_html(r):
    """Lost-field and fault-hunt findings for one run."""
    parts = []
    L = r.get("loss")
    if L:
        verdict = ("no gap captured" if not L["gap_captured"] else
                   ("clean" if L["clean"] else "handled" if L["handled"] else "NOT handled") + f" — {L['how']}")
        cls = "b-ok" if L.get("clean") else "b-warn" if L.get("handled") else "b-bad"
        parts.append(f'<p><span class="badge {cls}">{esc(L["fields_lost"])} lost field(s): {esc(verdict)}</span> '
                     f'blank {L["blank_fields"]} · garbled {L["garbled_fields"]} · own pattern {L["fallback_fields"]} · '
                     f'non-standard {L["nonstandard_fields"]}</p>')
    H = r.get("hunt")
    if H:
        rows = "".join(
            f'<tr><td>{w}</td><td class="num">{d["fields"]}</td><td class="num">{d["not_ours"]}</td>'
            f'<td class="num">{d["ghost"]}</td><td class="num">{d["mismatch"]}</td><td class="num">{d["back"]}</td>'
            f'<td class="num">{d["repeat"]}</td></tr>'
            for w in ("before", "stress", "settling", "after") for d in [H["windows"].get(w)] if d)
        state = ("clean after stress" if H["after_clean"] else "NOT clean after stress"
                 if H["after_clean"] is False else "recovery not captured")
        cls = "b-ok" if H["after_clean"] else "b-bad"
        parts.append(f'<p><span class="badge {cls}">{esc(state)}</span></p><div class="scroll"><table>'
                     f'<thead><tr><th>Window</th><th>Fields</th><th>Not ours</th><th>Ghosted</th><th>Stuck/torn</th>'
                     f'<th>Back</th><th>Repeated</th></tr></thead><tbody>{rows}</tbody></table></div>')
    return "".join(parts)


def tile(k, v, u=""):
    return f'<div class="tile"><div class="k">{esc(k)}</div><div class="v">{esc(v)} <span class="u">{esc(u)}</span></div></div>'


def input_badge(r):
    """CH2: was the generator's output right (two-channel runs only)."""
    i = r.get("input")
    if i is None:
        return '<span class="badge">CH1 only</span>'
    if not i["present"] and r["test"]["id"] != "V1":
        return '<span class="badge b-warn">no CH2 video</span>'
    if not i["ok"]:
        return f'<span class="badge b-bad" title="{esc("; ".join(i["issues"]))}">input wrong</span>'
    if i.get("verified") is False:
        return '<span class="badge b-info">input not verified</span>'
    return '<span class="badge b-ok">input ok</span>'


def latency_text(r):
    L = (r.get("input") or {}).get("latency")
    return f"{L['median_ms']:.2f} ms" if L else "—"


def run_card(r, idx, base=None, g=None):
    t, h, m = r["test"], r["h"], r["markers"]
    anchor = f"run-{idx}"
    ideal = r.get("ideal", {})
    tiles = [
        tile("H jitter (output)", f"{h['jitter_ns_rms']:.2f}" if h["jitter_ns_rms"] is not None else "—", "ns rms"),
        tile("Line period", f"{h['period_ns']:.1f}" if h["period_ns"] is not None else "—", "ns"),
        tile("Fields captured", r["fields"]["count"]),
        tile("Markers decoded", m["decoded"]),
        tile("Marker offset", f"{round(m['offset_us']['median'], 1) + 0.0:+.1f}" if m["offset_us"] else "—", "µs"),
        tile("Marker line after V-sync", f"{m['line_after_vsync']['median']:.0f}" if m.get("line_after_vsync") else "—",
             f"({m['line_after_vsync']['min']}–{m['line_after_vsync']['max']})" if m.get("line_after_vsync") else ""),
    ]
    inp = r.get("input") or {}
    if inp.get("latency"):
        L = inp["latency"]
        tiles.append(tile("Latency", f"{L['median_ms']:.2f}", f"ms ({L['fields']:.2f} fields)"))
    if inp.get("rate_ppm") is not None:
        tiles.append(tile("Output rate vs input", f"{inp['rate_ppm']:+.1f}", "ppm (0 = locked)"))
    v = inp.get("vs_input")
    if v:
        times = lambda x: f"×{x:.2f}" if x is not None else "—"
        tiles += [tile("Burst vs input", times(v["burst"])), tile("Chroma vs input", times(v["chroma"])),
                  tile("Gray vs input", times(v["gray"])), tile("Hue vs input", f"{v['hue_deg']:+.1f}", "°")]
    pc = r.get("picture")
    if pc:
        tiles += [
            tile("Burst", f"{pc['burst_pp_ire']:.1f}", "IRE p-p (40)"),
            tile("Yellow hue", f"{pc['yellow_hue_deg']:.1f}", f"° ±{pc['yellow_hue_jitter_deg']:.1f} (167.1)"),
            tile("Yellow chroma", f"{pc['yellow_chroma_pp_ire']:.1f}", "IRE p-p (62)"),
            tile("Gray bar", f"{pc['gray_ire']:.1f}", "IRE (76.9)"),
            tile("Blanking wander", f"{pc['blanking_wander_ire']:.2f}", "IRE rms"),
            tile("Noise on gray", f"{pc['gray_noise_ire_rms']:.2f}", "IRE rms"),
            *([tile("Picture displacement", f"{pc['displacement']['rms_ns']:.1f}",
                    f"ns rms ({pc['displacement']['peak_ns']:.0f} peak)")] if pc.get("displacement") else []),
        ]
    frames = m["frames"]
    if r.get("field_log"):
        seq = (f'<h3 style="font-size:14px;margin:14px 0 6px">Output fields</h3>{field_strip(r["field_log"])}'
               f'<p class="cap">Each cell is one output field. A clean output shows every frame twice, in order; '
               f'a dark outline marks a field of non-standard length.</p>')
    elif frames:
        seq = (f'<h3 style="font-size:14px;margin:14px 0 6px">Frame sequence on the output</h3>{frame_strip(frames)}'
               f'<p class="cap">Each cell is one field\'s frame marker. A clean output shows every frame twice, in order.</p>')
    else:
        seq = '<p class="cap">No frame markers decoded.</p>'
    seq = fault_html(r) + seq
    wave = (f'<h3 style="font-size:14px;margin:14px 0 6px">One picture line (CH{r["channel"]})</h3>{waveform(r["excerpt"])}'
            f'<p class="cap">Min/max envelope of a line about 60 lines into a field; the shading is the colour subcarrier.</p>'
            if r.get("excerpt") else "")
    return f"""
<section class="card" id="{anchor}">
  <header><h3>{esc(t['id'])} · {esc(t['name'])} · rung {esc(t['rung'])} of {esc(t.get('rungs', '?'))}</h3>
    <span class="sub" style="margin:0">{esc(params_text(t))} · aimed at {esc(targets_text(t))} · {esc(t.get('chroma', ''))}</span></header>
  <dl class="ideal"><dt>Ideal, line TBC</dt><dd>{esc(ideal.get('line_tbc', '—'))}</dd>
    <dt>Ideal, frame TBC</dt><dd>{esc(ideal.get('frame_tbc', '—'))}</dd></dl>
  <div class="tiles">{''.join(tiles)}</div>
  <div>{fields_badge(r)} {sequence_badge(r)}{vpos_badge(r, base)}{dma_badge(r)} {input_badge(r)}</div>
  {checks_html(g)}
  {seq}
  {wave}
  <p class="cap">Run {esc(r['run'])} · {r['seconds']:.2f} s captured</p>
</section>"""


def tolerance_html(results):
    from run_suite import tolerance_lines
    lines = tolerance_lines(results)
    hunt = [r for r in results if r.get("hunt")]
    for r in hunt:
        H = r["hunt"]
        state = ("clean after stress" if H["after_clean"] else "NOT clean after stress"
                 if H["after_clean"] is False else "recovery window not captured")
        lines.append(f"X1 stress {r['test']['params']['stress']:g}: {state}; "
                     f"{H['stuck_or_ghost']} stuck/torn or ghosted field(s)")
    if not lines:
        return ""
    items = "".join(f"<li>{esc(l)}</li>" for l in lines)
    return f'<h2>Fault tests</h2><ul class="note" style="list-style:none;padding-left:12px">{items}</ul>'


VERDICT_BADGE = {"pass": "b-ok", "marginal": "b-warn", "fail": "b-bad", "invalid": "b-bad", "info": "b-info"}


def verdict_badge(g):
    if not g:
        return ""
    return f'<span class="badge {VERDICT_BADGE.get(g["verdict"], "")}">{esc(g["verdict"])}</span>'


def checks_html(g):
    if not g or not g["checks"]:
        return f'<p class="cap">{esc("; ".join(g["notes"]))}</p>' if g and g["notes"] else ""
    rows = "".join(
        f'<tr><td>{"✓" if c["ok"] else ("~" if c["minor"] else "✗")}</td><td>{esc(c["name"])}</td>'
        f'<td>{esc(c["value"])}</td><td>{esc(c["limit"])}</td></tr>' for c in g["checks"])
    notes = f'<p class="cap">{esc("; ".join(g["notes"]))}</p>' if g["notes"] else ""
    return (f'<h3 style="font-size:14px;margin:14px 0 6px">Grade: {verdict_badge(g)}</h3>'
            f'<div class="scroll"><table><thead><tr><th></th><th>Check</th><th>Measured</th><th>Limit</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>{notes}')


def grades_html(G):
    if not G:
        return ""
    groups = " · ".join(f"{k} {100 * v:.0f}" for k, v in sorted(G["groups"].items()))
    score = '<p class="note"><b>Not scored.</b></p>' if G["score"] is None else ""
    score = score or (f'<p style="font-size:28px;font-weight:700;margin:6px 0">{G["score"]:.0f}<span style="font-size:15px;'
             f'color:var(--muted)"> / 100</span></p><p class="cap">By group: {esc(groups)} (weights: H 3, S 2, '
             f'F and C 1.5, others 1)</p>') if G["score"] is not None else ""
    rows = "".join(f'<tr><td>{esc(tid)}</td><td>{esc(t["summary"])}</td><td>'
                   + " ".join(f'<span class="badge {VERDICT_BADGE.get(v, "")}">r{r} {esc(v)}</span>' for r, v in t["rungs"])
                   + "</td></tr>" for tid, t in G["tests"].items())
    notes = "".join(f"<li>{esc(n)}</li>" for n in G["notes"])
    return (f'<h2>Grade (provisional)</h2><p class="note">Thresholds are first proposals (docs/design.md, Scoring), '
            f'not yet checked against a known-good TBC. Judged against {esc(G["class"])} TBC ideals.'
            + (f' Displacement is the excess over V0 ({G["floor_ns"]:.1f} ns floor).' if G["floor_ns"] is not None else "")
            + f'</p>{score}<div class="scroll"><table><thead><tr><th>Test</th><th>Grade</th><th>Rungs</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>' + (f'<ul class="cap">{notes}</ul>' if notes else ""))


def write(results, path=None, title="TBT field trial", grades=None):
    """Write the report and return its path. `grades` is grade.grade()'s result, if any."""
    gmap = {(g["test"], g["rung"]): g for g in grades["runs"]} if grades else {}
    if not results:
        raise ValueError("no results to report")
    first = results[0]
    dut = first.get("dut", {}) or {}
    name = dut.get("name") or "unspecified device"
    now = dt.datetime.now()
    if path is None:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower() or "dut"
        os.makedirs(REPORTS, exist_ok=True)
        path = os.path.join(REPORTS, f"{now:%Y%m%d-%H%M%S}_{slug}.html")
    gen = first.get("generator", {})
    lv = gen.get("levels", {})
    # The V0 run, if present, is the reference for where the picture should sit vertically.
    v0 = next((r for r in results if r["test"]["id"] == "V0" and r["markers"].get("line_after_vsync")), None)
    base = v0["markers"]["line_after_vsync"]["median"] if v0 else None
    rows = []
    for i, r in enumerate(results):
        t, h = r["test"], r["h"]
        jitter = f"{h['jitter_ns_rms']:.2f} ns" if h["jitter_ns_rms"] is not None else "—"
        rows.append(
            f'<tr><td><a href="#run-{i}">{esc(t["id"])}</a></td><td class="num">{esc(t["rung"])}</td>'
            f'<td>{esc(params_text(t))}</td><td>{esc(targets_text(t))}</td><td>{fields_badge(r)}</td>'
            f'<td class="num">{jitter}</td>'
            f'<td>{sequence_badge(r)}{vpos_badge(r, base)}{dma_badge(r)}</td>'
            f'{picture_cells(r)}<td class="num">{r["markers"]["decoded"]}</td>'
            f'<td>{input_badge(r)}</td><td class="num">{latency_text(r)}</td>'
            + (f'<td>{verdict_badge(gmap.get((t["id"], t["rung"])))}</td>' if grades else "") + '</tr>')
    notes = f'<p class="note">{esc(dut["notes"])}</p>' if dut.get("notes") else ""
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · {esc(name)}</title><style>{CSS}</style></head>
<body><main>
<h1>{esc(title)}</h1>
<p class="sub">Device under test: <b style="color:var(--fg)">{esc(name)}</b></p>
{notes}
<div class="meta">
  <span><b>{now:%Y-%m-%d %H:%M}</b></span>
  <span>{len(results)} run{'s' if len(results) != 1 else ''}</span>
  <span>firmware <b>{esc(gen.get('firmware', '?'))}</b></span>
  <span>sample clock <b>{gen.get('sample_hz', 0) / 1e6:.6f} MHz</b></span>
  <span>levels <b>{esc(lv.get('sync_code', '?'))} / {esc(lv.get('white_code', '?'))}</b> (sync/white code)</span>
  <span>colour <b>{esc(first['test'].get('chroma', '?'))}</b></span>
</div>
<p class="note">{"Observations of the TBC's output, with provisional grades above." if grades else "These are observations of the TBC's output, not grades."} "Ideal" describes what a perfect
TBC of each class would output for that test. {"Input (CH2) is the generator's output, teed off before the TBC: it checks each run's input and gives the TBC's latency and its gain against the real input." if any(r.get("input") for r in results) else "Captured on CH1 only: picture figures are against nominal levels, and there is no input check or latency."}</p>
{grades_html(grades)}
{tolerance_html(results)}
<h2>Summary</h2>
<div class="scroll"><table>
<thead><tr><th>Test</th><th>Rung</th><th>Parameters</th><th>Aimed at</th><th>Output fields</th><th>H jitter</th>
<th>Frame sequence</th><th>Burst</th><th>Hue</th><th>Gray</th><th>Markers</th><th>Input (CH2)</th><th>Latency</th>{"<th>Grade</th>" if grades else ""}</tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<h2>Runs</h2>
{''.join(run_card(r, i, base, gmap.get((r["test"]["id"], r["test"]["rung"]))) for i, r in enumerate(results))}
<footer>Generated by tools/html_report.py · TBT (Time Base Torture)</footer>
</main></body></html>
"""
    with open(path, "w") as f:
        f.write(page)
    return os.path.normpath(path)


# ---- Dropout watch ------------------------------------------------------------
STATE_FILL = {"ours": "var(--ok)", "alive": "var(--warn)", "dead": "var(--bad)"}


def watch_timeline(res):
    """SVG timeline: one mark per sample, coloured by state, phases shaded."""
    S = res["samples"]
    if not S:
        return ""
    T = max(s["t"] for s in S) + res["interval_s"]
    W, L, R, top, band = 760, 70, 16, 34, 44
    X = lambda t: L + (W - L - R) * t / T
    g = [f'<svg viewBox="0 0 {W} {top + band + 40}" role="img" aria-label="TBC output state over time">']
    # phase bands
    t_start = 0.0
    for ph in res["phases"]:
        ss = [s for s in S if s["phase"] == ph["name"]]
        if not ss:
            continue
        a, b = ss[0]["t"], ss[-1]["t"] + res["interval_s"]
        g.append(f'<rect x="{X(a):.1f}" y="{top - 22}" width="{X(b) - X(a):.1f}" height="{band + 22}" '
                 f'style="fill:var(--card);stroke:var(--line)"/>')
        g.append(f'<text x="{(X(a) + X(b)) / 2:.1f}" y="{top - 8}" text-anchor="middle">{esc(ph["name"])} · {esc(ph["test"])}</text>')
    for s in S:
        x0 = X(s["t"])
        w = max(1.0, X(s["t"] + res["interval_s"]) - x0 - 0.5)
        g.append(f'<rect x="{x0:.1f}" y="{top}" width="{w:.1f}" height="{band}" style="fill:{STATE_FILL[s["state"]]}">'
                 f'<title>{s["t"]:.1f} s: {s["state"]} ({s["phase"]})</title></rect>')
    g.append(f'<text x="{L - 8}" y="{top + band / 2 + 4}" text-anchor="end">output</text>')
    step = 30 if T > 120 else 10
    for t in range(0, int(T) + 1, step):
        g.append(f'<line x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{top + band}" y2="{top + band + 5}" style="stroke:var(--muted)"/>')
        g.append(f'<text x="{X(t):.1f}" y="{top + band + 18}" text-anchor="middle">{t} s</text>')
    g.append("</svg>")
    legend = ('<div class="legend"><span><i class="sw" style="background:var(--ok)"></i>our picture</span>'
              '<span><i class="sw" style="background:var(--warn)"></i>video, no marker (black or its own pattern)</span>'
              '<span><i class="sw" style="background:var(--bad)"></i>dead: no video sync</span></div>')
    return "".join(g) + legend


def write_watch(res, path=None, default_path=None, title="TBT dropout watch"):
    """Write the dropout-watch report and return its path."""
    path = path or default_path
    dut = res.get("dut", {}) or {}
    name = dut.get("name") or "unspecified device"
    eps = res["episodes"]
    dead_total = sum(e["length_s"] for e in eps)
    longest = max((e["length_s"] for e in eps), default=0)
    tiles = [tile("Dead episodes", len(eps)), tile("Total dead", f"{dead_total:.0f}", "s"),
             tile("Longest", f"{longest:.0f}", "s")]
    for ph, d in res["per_phase"].items():
        tiles.append(tile(f"Dead, {ph}", f"{d['dead_pct']:.0f}", f"% of {d['samples']} samples"))
    rows = "".join(
        f'<tr><td class="num">{i + 1}</td><td class="num">{e["start_s"]:.1f} s</td><td>{esc(e["phase"])}</td>'
        f'<td class="num">{e["length_s"]:.1f} s</td>'
        f'<td>{"recovered" if e["ended_by"] is not None else "still dead at the end"}</td></tr>'
        for i, e in enumerate(eps))
    table = (f'<div class="scroll"><table><thead><tr><th>#</th><th>Started</th><th>Phase</th><th>Length</th>'
             f'<th>Outcome</th></tr></thead><tbody>{rows}</tbody></table></div>' if eps
             else '<p><span class="badge b-ok">no dropouts</span></p>')
    phase_rows = "".join(
        f'<tr><td>{esc(ph)}</td><td class="num">{d["samples"]}</td><td class="num">{d["ours"]}</td>'
        f'<td class="num">{d["alive_no_marker"]}</td><td class="num">{d["dead"]}</td><td class="num">{d["dead_pct"]} %</td>'
        f'<td class="num">{d["episodes_started"]}</td></tr>' for ph, d in res["per_phase"].items())
    notes = f'<p class="note">{esc(dut["notes"])}</p>' if dut.get("notes") else ""
    phases = " → ".join(f'{esc(p["name"])} {esc(p["test"])} {p["seconds"]:g} s' for p in res["phases"])
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · {esc(name)}</title><style>{CSS}</style></head>
<body><main>
<h1>{esc(title)}</h1>
<p class="sub">Device under test: <b style="color:var(--fg)">{esc(name)}</b></p>
{notes}
<div class="meta"><span><b>{esc(res["started"])}</b></span><span>{phases}</span>
<span>one {res["window_s"] * 1000:.0f} ms sample every {res["interval_s"]:g} s</span>
<span>generator DMA restarts so far <b>{esc(res["generator"].get("dma_restarts"))}</b></span></div>
<p class="note">Does the TBC's output go dead, and for how long? Clean V0 before and after is the control;
the stimulus repeatedly cuts the input. "Dead" means no valid video sync on the TBC's output.</p>
<div class="tiles" style="margin-top:16px">{''.join(tiles)}</div>
<h2>Timeline</h2>
{watch_timeline(res)}
<h2>Dead episodes</h2>
{table}
<h2>By phase</h2>
<div class="scroll"><table><thead><tr><th>Phase</th><th>Samples</th><th>Our picture</th><th>Video, no marker</th>
<th>Dead</th><th>Dead %</th><th>Episodes started</th></tr></thead><tbody>{phase_rows}</tbody></table></div>
<footer>Generated by tools/dropout_watch.py · TBT (Time Base Torture)</footer>
</main></body></html>
"""
    with open(path, "w") as f:
        f.write(page)
    return os.path.normpath(path)
