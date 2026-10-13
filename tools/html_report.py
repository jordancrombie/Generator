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


def tile(k, v, u=""):
    return f'<div class="tile"><div class="k">{esc(k)}</div><div class="v">{esc(v)} <span class="u">{esc(u)}</span></div></div>'


def run_card(r, idx, base=None):
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
    frames = m["frames"]
    seq = (f'<h3 style="font-size:14px;margin:14px 0 6px">Frame sequence on the output</h3>{frame_strip(frames)}'
           f'<p class="cap">Each cell is one field\'s frame marker. A clean output shows every frame twice, in order.</p>'
           if frames else '<p class="cap">No frame markers decoded.</p>')
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
  <div>{fields_badge(r)} {sequence_badge(r)}{vpos_badge(r, base)}{dma_badge(r)}</div>
  {seq}
  {wave}
  <p class="cap">Run {esc(r['run'])} · {r['seconds']:.2f} s captured</p>
</section>"""


def write(results, path=None, title="TBC test report"):
    """Write the report and return its path."""
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
            f'<td class="num">{r["markers"]["decoded"]}</td></tr>')
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
<p class="note">These are observations of the TBC's output, not grades. "Ideal" describes what a perfect
TBC of each class would output for that test.</p>
<h2>Summary</h2>
<div class="scroll"><table>
<thead><tr><th>Test</th><th>Rung</th><th>Parameters</th><th>Aimed at</th><th>Output fields</th><th>H jitter</th>
<th>Frame sequence</th><th>Markers</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<h2>Runs</h2>
{''.join(run_card(r, i, base) for i, r in enumerate(results))}
<footer>Generated by tools/html_report.py · NTSC test-signal generator</footer>
</main></body></html>
"""
    with open(path, "w") as f:
        f.write(page)
    return os.path.normpath(path)
