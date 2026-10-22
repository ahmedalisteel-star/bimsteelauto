"""
report.py — One calculation-sheet renderer for every connection type.
=====================================================================

Because every designer returns a :class:`~.core.ConnectionResult`, a single
renderer serves the whole package. Add a connection type and its sheet
comes for free.

    render_html()   stamped-format sheet, prints to PDF from the browser
    render_text()   plain text, for the terminal and for diffing

The sheet carries an UNVERIFIED banner whenever any formula behind it has
not been cross-checked. That banner is the point: a calculation sheet that
looks finished is exactly how an unchecked formula reaches a drawing.
"""

from __future__ import annotations

import html
from datetime import date
from typing import Iterable, Sequence

from .core import ConnectionResult

_CSS = """
:root{--ink:#111;--mut:#666;--line:#d4d4d4;--bg:#fff;--warn:#8a5a00;
--warnbg:#fff8e6;--fail:#a11;--failbg:#fdf0f0;--pass:#0a6b3d;--head:#f4f4f2;}
*{box-sizing:border-box}
body{margin:0;padding:0;background:#ececeb;color:var(--ink);
font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.sheet{max-width:960px;margin:24px auto;background:var(--bg);padding:32px 40px;
box-shadow:0 1px 3px rgba(0,0,0,.12)}
h1{font-size:19px;margin:0 0 2px;letter-spacing:-.01em}
.sub{color:var(--mut);font-size:12px;margin-bottom:20px}
h2{font-size:14px;margin:26px 0 8px;padding-bottom:5px;
border-bottom:1px solid var(--line);letter-spacing:.02em;text-transform:uppercase}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin:6px 0 14px}
th{text-align:left;background:var(--head);font-weight:600;padding:6px 8px;
border-bottom:1px solid var(--line);font-size:11px;letter-spacing:.04em;
text-transform:uppercase;color:var(--mut)}
td{padding:5px 8px;border-bottom:1px solid #eee}
td.n{text-align:right;font-variant-numeric:tabular-nums}
tr.gov td{font-weight:600;background:#fafaf8}
tr.bad td{background:var(--failbg);color:var(--fail)}
.badge{display:inline-block;padding:2px 9px;border-radius:3px;font-size:11px;
font-weight:600;letter-spacing:.04em}
.ok{background:#e7f4ee;color:var(--pass)}
.no{background:var(--failbg);color:var(--fail)}
.banner{background:var(--warnbg);border-left:3px solid var(--warn);
color:var(--warn);padding:10px 14px;margin:16px 0;font-size:12.5px}
.warn{color:var(--warn);font-size:12px;margin:3px 0 3px 14px}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
gap:2px 20px;font-size:12.5px;margin:6px 0 14px}
.kv div{padding:3px 0;border-bottom:1px solid #f0f0f0}
.kv b{color:var(--mut);font-weight:500}
.cite{color:var(--mut);font-size:11.5px;font-style:italic}
.foot{margin-top:32px;padding-top:12px;border-top:1px solid var(--line);
color:var(--mut);font-size:11px}
@media print{body{background:#fff}.sheet{box-shadow:none;margin:0;max-width:none}}
@media (prefers-color-scheme:dark){:root{--ink:#e8e8e6;--mut:#9a9a97;
--line:#3a3a38;--bg:#1c1c1b;--head:#262625;--warnbg:#2e2612;--failbg:#2e1a1a;
--fail:#f08a8a;--pass:#6fd39f;--warn:#e0b060}body{background:#141413}
tr.gov td{background:#232322}}
"""


def _e(x) -> str:
    return html.escape(str(x))


def render_text(results: Sequence[ConnectionResult],
                title: str = "Connection Design") -> str:
    """Plain-text sheet."""
    out = [title, "=" * len(title), ""]
    for r in results:
        out.append(r.report())
        out.append("")
    return "\n".join(out)


def render_html(
    results: Sequence[ConnectionResult],
    *,
    project: str = "",
    job_no: str = "",
    designer: str = "",
    checker: str = "",
    title: str = "Connection Design Calculations",
    basis: str = "AISC 360-16 (LRFD) · AISC DG 1 & 4 · ACI 318-19 Ch. 17",
) -> str:
    """Stamped-format HTML calculation sheet. Print to PDF from a browser."""
    rs = list(results)
    unverified = [r for r in rs if not r.verified]
    failing = [r for r in rs if not r.passed]

    p: list[str] = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body>",
        "<div class='sheet'>",
        f"<h1>{_e(title)}</h1>",
        f"<div class='sub'>{_e(basis)}</div>",
        "<div class='kv'>",
        f"<div><b>Project</b> {_e(project or '—')}</div>",
        f"<div><b>Job No.</b> {_e(job_no or '—')}</div>",
        f"<div><b>Designer</b> {_e(designer or '—')}</div>",
        f"<div><b>Checker</b> {_e(checker or '—')}</div>",
        f"<div><b>Date</b> {date.today().isoformat()}</div>",
        f"<div><b>Connections</b> {len(rs)}</div>",
        "</div>",
    ]

    if unverified:
        p.append(
            "<div class='banner'><b>NOT FOR ISSUE.</b> "
            f"{len(unverified)} of {len(rs)} connections use formulas that "
            "have not been cross-checked against an independent source. "
            "Verify, flip the module's VERIFIED flag, and re-issue before "
            "this sheet supports a drawing.</div>"
        )
    if failing:
        p.append(
            f"<div class='banner'><b>{len(failing)} connection"
            f"{'s' if len(failing) > 1 else ''} FAIL.</b> "
            "See the governing limit state in each section.</div>"
        )

    # ---- summary --------------------------------------------------
    p += ["<h2>Summary</h2>",
          "<table><thead><tr><th>Mark</th><th>Type</th>"
          "<th>Governing limit state</th><th style='text-align:right'>U</th>"
          "<th>Result</th><th>Verified</th></tr></thead><tbody>"]
    for r in sorted(rs, key=lambda x: x.utilization, reverse=True):
        g = r.governing
        cls = "" if r.passed else " class='bad'"
        badge = ("<span class='badge ok'>PASS</span>" if r.passed
                 else "<span class='badge no'>FAIL</span>")
        p.append(
            f"<tr{cls}><td>{_e(r.mark)}</td><td>{_e(r.kind)}</td>"
            f"<td>{_e(g.name if g else '—')}</td>"
            f"<td class='n'>{r.utilization:.3f}</td><td>{badge}</td>"
            f"<td>{'yes' if r.verified else '<b>NO</b>'}</td></tr>"
        )
    p.append("</tbody></table>")

    # ---- per connection -------------------------------------------
    for r in rs:
        badge = ("<span class='badge ok'>PASS</span>" if r.passed
                 else "<span class='badge no'>FAIL</span>")
        p.append(f"<h2>{_e(r.mark)} — {_e(r.kind)} {badge}</h2>")
        if r.citation:
            p.append(f"<div class='cite'>{_e(r.citation)}</div>")

        if r.forces:
            p.append("<table><thead><tr><th>Action</th>"
                     "<th style='text-align:right'>Design</th>"
                     "<th style='text-align:right'>Analysis</th>"
                     "<th style='text-align:right'>% capacity</th>"
                     "<th>Governed by</th></tr></thead><tbody>")
            for f in r.forces:
                cb = (f"{f.capacity_based:.2f}"
                      if f.capacity_based is not None else "—")
                p.append(
                    f"<tr><td>{_e(f.label)} ({_e(f.unit)})</td>"
                    f"<td class='n'>{f.value:.2f}</td>"
                    f"<td class='n'>{f.analysis:.2f}</td>"
                    f"<td class='n'>{cb}</td>"
                    f"<td>{_e(f.criterion.value)}</td></tr>"
                )
            p.append("</tbody></table>")

        if r.selections:
            p.append("<div class='kv'>")
            p += [f"<div><b>{_e(k)}</b> {_e(v)}</div>"
                  for k, v in r.selections.items()]
            p.append("</div>")

        if r.geometry:
            p.append("<div class='kv'>")
            p += [f"<div><b>{_e(k)}</b> "
                  f"{v:.2f}</div>" if isinstance(v, (int, float))
                  else f"<div><b>{_e(k)}</b> {_e(v)}</div>"
                  for k, v in r.geometry.items()]
            p.append("</div>")

        gov = r.governing
        p.append("<table><thead><tr><th>Limit state</th>"
                 "<th style='text-align:right'>Capacity</th>"
                 "<th style='text-align:right'>Demand</th>"
                 "<th style='text-align:right'>U</th>"
                 "<th>Result</th></tr></thead><tbody>")
        for ls in sorted(r.limit_states,
                         key=lambda x: x.utilization, reverse=True):
            cls = (" class='bad'" if not ls.passed
                   else (" class='gov'" if ls is gov else ""))
            p.append(
                f"<tr{cls}><td>{_e(ls.name)}</td>"
                f"<td class='n'>{ls.capacity:.2f} {_e(ls.unit)}</td>"
                f"<td class='n'>{ls.demand:.2f} {_e(ls.unit)}</td>"
                f"<td class='n'>{ls.utilization:.3f}</td>"
                f"<td>{'OK' if ls.passed else '<b>NO GOOD</b>'}</td></tr>"
            )
        p.append("</tbody></table>")

        p += [f"<div class='warn'>! {_e(w)}</div>" for w in r.warnings]

    p += [
        "<div class='foot'>",
        "Generated by BIMSteelAuto · connection design layer. ",
        "Every capacity is traced to its clause. Formulas flagged ",
        "<b>unverified</b> have not been checked against an independent ",
        "source and must not support an issued drawing.",
        "</div></div></body></html>",
    ]
    return "\n".join(p)
