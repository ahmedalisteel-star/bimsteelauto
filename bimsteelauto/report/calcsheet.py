"""
bimsteelauto.report.calcsheet
=============================

The calculation sheet: one HTML document covering the whole frame.

    DesignResult  ->  render_html()  ->  a sheet a reviewer can check

Design rules this renderer follows, in order of importance
----------------------------------------------------------
1. **A sheet that cannot be issued says so, at the top, in red.**  The
   banner is driven by ``DesignResult.ready_for_issue()`` and lists every
   blocker.  It is not suppressible.

2. **Every number carries its clause.**  A utilisation with no equation
   number behind it is not a calculation, it is an assertion.

3. **What was NOT checked is printed as prominently as what was.**  The
   coverage gaps and the stated assumptions get their own page, before the
   results, not a footnote after them.

4. **No dependency.**  Plain string building, no template engine.  This has
   to render inside FreeCAD's bundled interpreter with nothing installed,
   and a missing Jinja2 must never be the reason a sheet cannot be produced.

The output is a self-contained HTML file: styles inline, no images, no
scripts.  It prints to A4 and opens in any browser.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from ..spec import (
    ConnectionDesign,
    DesignResult,
    MemberCheck,
    MemberDesign,
)

__all__ = [
    "render_html",
    "render_text",
    "collect_coverage_gaps",
]

#: Printed at the foot of every page.  Non-negotiable, per the same rule the
#: connection layer already follows.
DISCLAIMER = (
    "This sheet is a design aid produced by BIMSteelAuto. It is not a "
    "professional seal. Every limit state, assumption and coverage gap "
    "listed here must be reviewed by the responsible engineer before issue."
)


def collect_coverage_gaps() -> tuple[str, ...]:
    """Every module's declared coverage gaps, in one tuple.

    Imported lazily and defensively: a module that is not installed in this
    environment contributes nothing rather than breaking the sheet.
    """
    gaps: list[str] = []
    for module, label in (
        ("bimsteelauto.loads.wind", "wind loads"),
        ("bimsteelauto.loads.cladding", "components and cladding"),
        ("bimsteelauto.design.members", "member design"),
        ("bimsteelauto.design.purlins", "purlin design"),
        ("bimsteelauto.design.bracing", "bracing design"),
        ("bimsteelauto.connections", "connection bridge"),
        ("bimsteelauto.detailing", "shop detailing"),
        ("bimsteelauto.analysis.space", "3D building model"),
    ):
        try:
            mod = __import__(module, fromlist=["COVERAGE_GAPS"])
        except Exception:  # noqa: BLE001 - absence is not an error here
            continue
        for g in getattr(mod, "COVERAGE_GAPS", ()):
            gaps.append(f"[{label}] {g}")
    return tuple(gaps)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_CSS = """
:root{--ink:#16181d;--muted:#5b6270;--rule:#c9cdd6;--soft:#f4f5f8;
      --fail:#a3121f;--failbg:#fdecee;--warn:#8a5a00;--warnbg:#fdf5e3;
      --ok:#1d6b3f;--headbg:#eceef3;}
*{box-sizing:border-box}
body{margin:0;font:11pt/1.45 "Segoe UI",system-ui,-apple-system,sans-serif;
     color:var(--ink);background:#fff}
.sheet{max-width:210mm;margin:0 auto;padding:14mm 14mm 18mm}
h1{font-size:19pt;margin:0 0 2mm;letter-spacing:-.01em}
h2{font-size:13pt;margin:9mm 0 3mm;padding-bottom:1.5mm;
   border-bottom:1.5px solid var(--ink)}
h3{font-size:11pt;margin:6mm 0 2mm}
p{margin:0 0 2.5mm}
.muted{color:var(--muted)}
.small{font-size:9pt}
.mono{font-family:"Cascadia Mono",Consolas,monospace;font-size:9.5pt}

.banner{border:2px solid var(--fail);background:var(--failbg);color:var(--fail);
        padding:4mm 5mm;margin:0 0 6mm;border-radius:2px}
.banner h2{border:0;margin:0 0 2mm;font-size:15pt;letter-spacing:.06em}
.banner ul{margin:0;padding-left:5mm}
.banner li{margin:.6mm 0}
.banner.ok{border-color:var(--ok);background:#eef7f1;color:var(--ok)}

table{width:100%;border-collapse:collapse;margin:0 0 4mm;font-size:9.5pt}
th,td{text-align:left;padding:1.6mm 2mm;border-bottom:1px solid var(--rule);
      vertical-align:top}
th{background:var(--headbg);font-weight:600;font-size:8.5pt;
   text-transform:uppercase;letter-spacing:.04em}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;
          font-family:"Cascadia Mono",Consolas,monospace}
tr.fail td{background:var(--failbg)}
tr.gov td{font-weight:600}
.cite{color:var(--muted);font-size:8.5pt}

.kv{display:grid;grid-template-columns:auto 1fr;gap:.8mm 5mm;
    margin:0 0 4mm;font-size:10pt}
.kv dt{color:var(--muted)}
.kv dd{margin:0}

.tag{display:inline-block;padding:.3mm 2mm;border-radius:2px;
     font-size:8pt;font-weight:600;letter-spacing:.04em}
.tag.pass{background:#e6f3ec;color:var(--ok)}
.tag.fail{background:var(--failbg);color:var(--fail)}

.note{border-left:3px solid var(--warn);background:var(--warnbg);
      padding:2.5mm 4mm;margin:0 0 4mm;font-size:9.5pt}
.gap{border-left:3px solid var(--muted);background:var(--soft);
     padding:2.5mm 4mm;margin:0 0 3mm;font-size:9.5pt}
.member{page-break-inside:avoid;margin:0 0 7mm}
footer{margin-top:8mm;padding-top:3mm;border-top:1px solid var(--rule);
       font-size:8pt;color:var(--muted)}
@media print{.sheet{padding:0}h2{page-break-after:avoid}}
"""


def _e(v: Any) -> str:
    return html.escape(str(v), quote=True)


def _num(v: float, dp: int = 2) -> str:
    try:
        return f"{float(v):,.{dp}f}"
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return _e(v)


def _ratio_cell(ratio: float) -> str:
    return f'<td class="n">{_num(ratio, 3)}</td>'


def render_html(
    result: DesignResult,
    *,
    designer: str = "",
    checker: str = "",
    title: str = "PEB Frame Design Calculations",
) -> str:
    """The whole sheet, as one self-contained HTML string."""
    spec = result.spec
    ready = result.ready_for_issue()
    blockers = result.issue_blockers()

    parts: list[str] = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>{_e(spec.job_number)} - {_e(title)}</title>",
        f"<style>{_CSS}</style></head><body><div class='sheet'>",
    ]

    parts.append(_header(spec, title, designer, checker, result))
    parts.append(_banner(ready, blockers))
    parts.append(_basis(spec, result))
    parts.append(_assumptions_and_gaps(result))
    parts.append(_geometry(spec))
    parts.append(_loads(result))
    parts.append(_member_summary(result.members))
    for m in result.members:
        parts.append(_member_page(m))
    if result.connections:
        parts.append(_connection_summary(result.connections))
        for c in result.connections:
            parts.append(_connection_page(c))
    parts.append(_reactions(result))
    parts.append(f"<footer>{_e(DISCLAIMER)}</footer></div></body></html>")
    return "".join(parts)


# -- sections ----------------------------------------------------------------


def _header(spec, title, designer, checker, result) -> str:
    stamp = result.generated_at or datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    return (
        f"<h1>{_e(title)}</h1>"
        f"<dl class='kv'>"
        f"<dt>Project</dt><dd>{_e(spec.project_name)}</dd>"
        f"<dt>Job number</dt><dd>{_e(spec.job_number)}</dd>"
        f"<dt>Designed by</dt><dd>{_e(designer) or '&mdash;'}</dd>"
        f"<dt>Checked by</dt><dd>{_e(checker) or '&mdash;'}</dd>"
        f"<dt>Generated</dt><dd class='mono'>{_e(stamp)}</dd>"
        f"</dl>"
    )


def _banner(ready: bool, blockers: Sequence[str]) -> str:
    if ready:
        return (
            "<div class='banner ok'><h2>READY FOR ISSUE</h2>"
            "<p class='small'>Every check passed, every design relation used "
            "has been verified against an independent source, and the "
            "specification validated. Review the assumptions and coverage "
            "gaps below before signing.</p></div>"
        )
    items = "".join(f"<li>{_e(b)}</li>" for b in blockers) or "<li>unknown</li>"
    return (
        "<div class='banner'><h2>NOT FOR ISSUE</h2>"
        f"<p class='small'>This design is blocked for the following "
        f"{len(blockers)} reason(s):</p><ul class='small'>{items}</ul></div>"
    )


def _basis(spec, result) -> str:
    s = spec.settings
    m = spec.materials
    rows = [
        ("Design method", s.design_method.value),
        ("Member code", s.member_code),
        ("Cold-formed code", s.cold_formed_code),
        ("Loading code", s.load_code.value),
        ("Connection capacity rule", f"{s.connection_capacity_fraction:.0%} of member capacity"),
        ("Second-order analysis", "yes (P-Delta)" if s.include_p_delta else "no"),
        ("Built-up plate", f"Fy {_num(m.Fy_builtup,0)} / Fu {_num(m.Fu_builtup,0)} MPa"),
        ("Cold-formed", f"Fy {_num(m.Fy_cold_formed,0)} / Fu {_num(m.Fu_cold_formed,0)} MPa"),
        ("Bolts", m.bolt_grade),
        ("Anchors", m.anchor_grade),
        ("Concrete", f"f'c {_num(m.fc_concrete,0)} MPa"),
        ("Engine", result.engine_version or "&mdash;"),
    ]
    body = "".join(
        f"<tr><td>{_e(k)}</td><td>{v if k=='Engine' else _e(v)}</td></tr>"
        for k, v in rows
    )
    return f"<h2>Design basis</h2><table><tbody>{body}</tbody></table>"


def _assumptions_and_gaps(result) -> str:
    gaps = result.coverage_gaps or collect_coverage_gaps()
    out = ["<h2>Assumptions and scope of checking</h2>"]

    if result.assumptions:
        out.append("<h3>Stated assumptions</h3>")
        for a in result.assumptions:
            out.append(f"<div class='note'>{_e(a)}</div>")
    else:
        out.append(
            "<p class='small muted'>No project-specific assumptions were "
            "recorded.</p>"
        )

    out.append("<h3>Checks NOT performed by this sheet</h3>")
    out.append(
        "<p class='small muted'>Listed so a reviewer knows what still has to "
        "be covered elsewhere. A member described as passing has passed the "
        "checks below it, not every check that exists.</p>"
    )
    for g in gaps:
        out.append(f"<div class='gap'>{_e(g)}</div>")

    if result.unverified:
        out.append("<h3>Design relations not yet independently verified</h3>")
        for u in result.unverified:
            out.append(f"<div class='note'>{_e(u)}</div>")

    if result.warnings:
        out.append("<h3>Warnings</h3>")
        for w in result.warnings:
            out.append(f"<div class='note'>{_e(w)}</div>")
    return "".join(out)


def _geometry(spec) -> str:
    g = spec.geometry
    bays = " + ".join(f"{b/1000:g}" for b in g.bay_spacings)
    spans = " + ".join(f"{s/1000:g}" for s in g.span_widths)
    rows = [
        ("Frame type", g.frame_type.value),
        ("Roof type", g.roof_type.value),
        ("Span(s)", f"{spans} m  (total {g.width/1000:g} m)"),
        ("Bay spacing", f"{bays} m  (total {g.length/1000:g} m)"),
        ("Eave height", f"{g.eave_height/1000:g} m"),
        ("Roof slope", f"{g.roof_slope:g} ({g.slope_deg:.2f}&deg;)"),
        ("Ridge height", f"{g.ridge_height/1000:g} m"),
        ("Mean roof height h", f"{g.mean_roof_height/1000:g} m"),
        ("Frame lines", f"{g.n_frames} over {g.n_bays} bays"),
        ("Purlin / girt spacing", f"{g.purlin_spacing/1000:g} / {g.girt_spacing/1000:g} m"),
        ("End wall", g.endwall_type.value),
    ]
    body = "".join(f"<tr><td>{_e(k)}</td><td>{v}</td></tr>" for k, v in rows)

    s = spec.site
    site_rows = [
        ("Basic wind speed V", f"{s.basic_wind_speed:g} m/s (3-sec gust)"),
        ("Exposure category", s.exposure_category.value),
        ("Enclosure", s.enclosure_classification.value),
        ("Risk category", s.risk_category.value),
        ("Topographic factor Kzt", f"{s.Kzt:g}"),
        ("Directionality Kd", f"{s.Kd:g}"),
        ("GCpi", f"{s.GCpi[0]:+g} / {s.GCpi[1]:+g}"),
    ]
    site_body = "".join(f"<tr><td>{_e(k)}</td><td>{v}</td></tr>" for k, v in site_rows)

    return (
        f"<h2>Geometry</h2><table><tbody>{body}</tbody></table>"
        f"<h2>Site and wind inputs</h2>"
        f"<p class='small muted'>The five variable inputs; every other wind "
        f"quantity is derived from the geometry above.</p>"
        f"<table><tbody>{site_body}</tbody></table>"
    )


def _loads(result) -> str:
    L = result.loads
    if not L.cases and not L.combinations:
        return ""
    out = ["<h2>Loads</h2>"]
    if L.qh:
        out.append(
            f"<p class='small'>Velocity pressure at mean roof height "
            f"q<sub>h</sub> = {_num(L.qh, 3)} kN/m&sup2;; end-zone width "
            f"a = {_num(L.end_zone_a, 0)} mm.</p>"
        )
    if L.cases:
        rows = "".join(
            f"<tr><td class='mono'>{_e(c.name)}</td><td>{_e(c.kind)}</td>"
            f"<td>{_e(c.description)}</td>"
            f"<td class='cite'>{_e(c.reference)}</td></tr>"
            for c in L.cases
        )
        out.append(
            "<h3>Load cases</h3><table><thead><tr><th>Case</th><th>Kind</th>"
            f"<th>Description</th><th>Reference</th></tr></thead><tbody>{rows}"
            "</tbody></table>"
        )
    if L.combinations:
        rows = "".join(
            f"<tr><td class='mono'>{_e(c.name)}</td>"
            f"<td class='mono'>{_e(_factors(c.factors))}</td>"
            f"<td class='cite'>{_e(c.reference)}</td></tr>"
            for c in L.combinations
        )
        out.append(
            "<h3>Load combinations</h3><table><thead><tr><th>Combination</th>"
            f"<th>Factors</th><th>Reference</th></tr></thead><tbody>{rows}"
            "</tbody></table>"
        )
    return "".join(out)


def _factors(factors: Mapping[str, float]) -> str:
    return "  ".join(f"{v:g}{k}" for k, v in factors.items())


def _member_summary(members: Sequence[MemberDesign]) -> str:
    if not members:
        return ""
    rows = []
    for m in sorted(members, key=lambda x: x.utilisation, reverse=True):
        gov = m.governing_check
        cls = "fail" if not m.passed else ""
        rows.append(
            f"<tr class='{cls}'><td class='mono'>{_e(m.mark)}</td>"
            f"<td>{_e(m.member_type.value)}</td>"
            f"<td class='mono'>{_e(m.section_id)}</td>"
            f"<td class='n'>{_num(m.length/1000, 3)}</td>"
            f"<td class='mono'>{_e(m.governing_combination)}</td>"
            f"<td>{_e(gov.name) if gov else '&mdash;'}</td>"
            f"{_ratio_cell(m.utilisation)}"
            f"<td>{_status(m.passed)}</td></tr>"
        )
    total = sum(m.weight for m in members)
    return (
        "<h2>Member summary</h2><table><thead><tr><th>Mark</th><th>Type</th>"
        "<th>Section</th><th class='n'>Length m</th><th>Governing combo</th>"
        "<th>Governing limit state</th><th class='n'>U</th><th>Result</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        f"<p class='small muted'>Main frame steel in this frame line: "
        f"{_num(total, 1)} kg.</p>"
    )


def _status(passed: bool) -> str:
    return (
        "<span class='tag pass'>PASS</span>"
        if passed
        else "<span class='tag fail'>FAIL</span>"
    )


def _member_page(m: MemberDesign) -> str:
    f = m.forces
    gov = m.governing_check
    rows = []
    for c in sorted(m.checks, key=lambda x: x.ratio, reverse=True):
        cls = " ".join(
            x for x in ("fail" if not c.passed else "", "gov" if c is gov else "") if x
        )
        rows.append(
            f"<tr class='{cls}'><td>{_e(c.name)}</td>"
            f"<td class='n'>{_num(c.demand)}</td>"
            f"<td class='n'>{_num(c.capacity)}</td>"
            f"<td>{_e(c.unit)}</td>{_ratio_cell(c.ratio)}"
            f"<td>{_status(c.passed)}</td>"
            f"<td class='cite'>{_e(c.reference)}"
            + (f"<br>{_e(c.note)}" if c.note else "")
            + "</td></tr>"
        )
    return (
        f"<div class='member'><h2>Member {_e(m.mark)} "
        f"&mdash; {_e(m.member_type.value)}</h2>"
        f"<dl class='kv small'>"
        f"<dt>Section</dt><dd class='mono'>{_e(m.section_id)}</dd>"
        f"<dt>Length</dt><dd>{_num(m.length,0)} mm</dd>"
        f"<dt>Governing combination</dt><dd class='mono'>{_e(m.governing_combination)}</dd>"
        f"<dt>Design actions</dt><dd class='mono'>"
        f"P = {_num(f.P)} kN &nbsp; V = {_num(f.Vy)} kN &nbsp; "
        f"M = {_num(f.Mz)} kN&middot;m &nbsp; at x = {_num(f.station,0)} mm</dd>"
        + (f"<dt>Note</dt><dd>{_e(m.note)}</dd>" if m.note else "")
        + "</dl>"
        "<table><thead><tr><th>Limit state</th><th class='n'>Demand</th>"
        "<th class='n'>Capacity</th><th>Unit</th><th class='n'>U</th>"
        "<th>Result</th><th>Reference</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _connection_summary(conns: Sequence[ConnectionDesign]) -> str:
    rows = []
    for c in sorted(conns, key=lambda x: x.utilisation, reverse=True):
        states = c.detail.get("limit_states", [])
        gov = max(states, key=lambda s: s["utilisation"], default=None)
        rows.append(
            f"<tr class='{'fail' if not c.passed else ''}'>"
            f"<td class='mono'>{_e(c.mark)}</td>"
            f"<td>{_e(c.connection_type.value)}</td>"
            f"<td class='mono'>{_e(', '.join(c.member_marks))}</td>"
            f"<td>{_e(gov['name']) if gov else '&mdash;'}</td>"
            f"{_ratio_cell(c.utilisation)}"
            f"<td>{_e(c.design_force_basis)}</td>"
            f"<td>{_status(c.passed)}</td>"
            f"<td>{'yes' if c.verified else '<b>NO</b>'}</td></tr>"
        )
    return (
        "<h2>Connection summary</h2><table><thead><tr><th>Mark</th>"
        "<th>Type</th><th>Members</th><th>Governing limit state</th>"
        "<th class='n'>U</th><th>Force basis</th><th>Result</th>"
        f"<th>Verified</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _connection_page(c: ConnectionDesign) -> str:
    d = c.detail
    rows = []
    for s in sorted(d.get("limit_states", []), key=lambda x: x["utilisation"], reverse=True):
        passed = s["utilisation"] <= 1.0
        rows.append(
            f"<tr class='{'fail' if not passed else ''}'><td>{_e(s['name'])}</td>"
            f"<td class='n'>{_num(s['demand'])}</td>"
            f"<td class='n'>{_num(s['capacity'])}</td>"
            f"<td>{_e(s['unit'])}</td>{_ratio_cell(s['utilisation'])}"
            f"<td>{_status(passed)}</td>"
            f"<td class='cite'>{_e(s['clause'])}</td></tr>"
        )

    out = [
        f"<div class='member'><h2>Connection {_e(c.mark)} "
        f"&mdash; {_e(c.connection_type.value)}</h2>"
    ]

    joint = d.get("joint", {})
    if joint:
        out.append("<dl class='kv small'>")
        for k, v in joint.items():
            if isinstance(v, dict):
                v = ", ".join(f"{kk}: {vv}" for kk, vv in v.items())
            label = k.replace("_", " ")
            out.append(f"<dt>{_e(label)}</dt><dd class='mono'>{_e(_fmt(v))}</dd>")
        out.append("</dl>")

    sel = d.get("selections", {})
    if sel:
        out.append("<h3>Selected</h3><dl class='kv small'>")
        for k, v in sel.items():
            out.append(f"<dt>{_e(k)}</dt><dd class='mono'>{_e(_fmt(v))}</dd>")
        out.append("</dl>")

    for w in d.get("warnings", []):
        out.append(f"<div class='note'>{_e(w)}</div>")

    out.append(
        "<table><thead><tr><th>Limit state</th><th class='n'>Demand</th>"
        "<th class='n'>Capacity</th><th>Unit</th><th class='n'>U</th>"
        "<th>Result</th><th>Reference</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )
    return "".join(out)


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def _reactions(result) -> str:
    if not result.reactions:
        return ""
    rows = "".join(
        f"<tr><td class='mono'>{_e(r.node)}</td>"
        f"<td class='mono'>{_e(r.combination)}</td>"
        f"<td class='n'>{_num(r.Fx)}</td><td class='n'>{_num(r.Fy)}</td>"
        f"<td class='n'>{_num(r.Mz)}</td></tr>"
        for r in result.reactions
    )
    return (
        "<h2>Support reactions</h2>"
        "<p class='small muted'>Factored. Positive F<sub>y</sub> acts upward "
        "on the structure, so a NEGATIVE value is net uplift on the "
        "holding-down bolts.</p>"
        "<table><thead><tr><th>Node</th><th>Combination</th>"
        "<th class='n'>Fx kN</th><th class='n'>Fy kN</th>"
        f"<th class='n'>Mz kN&middot;m</th></tr></thead><tbody>{rows}"
        "</tbody></table>"
    )


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------


def render_text(result: DesignResult) -> str:
    """A terminal-readable summary, for a quick look without a browser."""
    spec = result.spec
    out = [
        "=" * 78,
        f"{spec.project_name}  [{spec.job_number}]",
        f"{spec.settings.member_code} {spec.settings.design_method.value}  |  "
        f"{spec.settings.load_code.value}",
        "=" * 78,
        "",
    ]
    if result.ready_for_issue():
        out.append("READY FOR ISSUE")
    else:
        out.append("** NOT FOR ISSUE **")
        out += [f"   - {b}" for b in result.issue_blockers()]
    out.append("")

    if result.members:
        out.append(f"{'MARK':<8}{'SECTION':<26}{'COMBO':<18}{'U':>7}  RESULT")
        out.append("-" * 78)
        for m in sorted(result.members, key=lambda x: x.utilisation, reverse=True):
            out.append(
                f"{m.mark:<8}{m.section_id:<26}{m.governing_combination:<18}"
                f"{m.utilisation:7.3f}  {'PASS' if m.passed else 'FAIL'}"
            )
        out.append("")

    if result.connections:
        out.append(f"{'MARK':<8}{'TYPE':<20}{'BASIS':<20}{'U':>7}  RESULT")
        out.append("-" * 78)
        for c in sorted(result.connections, key=lambda x: x.utilisation, reverse=True):
            out.append(
                f"{c.mark:<8}{c.connection_type.value:<20}"
                f"{c.design_force_basis:<20}{c.utilisation:7.3f}  "
                f"{'PASS' if c.passed else 'FAIL'}"
            )
        out.append("")

    out.append(f"max utilisation {result.max_utilisation:.3f}   "
               f"steel {result.total_weight:,.0f} kg")
    out.append("")
    out.append(DISCLAIMER)
    return "\n".join(out)
