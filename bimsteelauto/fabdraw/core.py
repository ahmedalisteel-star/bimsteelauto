"""
The result contract every fabdraw gate returns.

Same shape of thinking as ``bimsteelauto_connections.core.ConnectionResult``:
a gate's verdict is DERIVED from what it found, never stored beside it, so a
result cannot say PASS while carrying a hard finding.

Two kinds of reason a gate fails, kept apart on purpose:

* a **finding** is something wrong in the model - a clash, a missing mark;
  it names the elements by GlobalId and mark so it can be fixed;
* a **blocker** is something the gate could not decide - a tolerance
  nobody has given, a schema the rules do not cover. Fixing the model
  will not clear it.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["Finding", "GateResult", "write_report", "NOT_FOR_ISSUE"]

NOT_FOR_ISSUE = "NOT FOR ISSUE"

#: Severities. Only "hard" fails a gate; "warning" is reported and never
#: blocks, which is the point of a warning set.
SEVERITIES = ("hard", "warning")


@dataclass(frozen=True)
class Finding:
    """One thing wrong, on named elements."""

    rule: str                               # "H1", "FR-03", "S1", ...
    severity: str                           # "hard" | "warning"
    message: str
    global_ids: tuple[str, ...] = ()
    marks: tuple[str, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity {self.severity!r} is not one of {SEVERITIES}")

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "severity": self.severity, "message": self.message,
                "global_ids": list(self.global_ids), "marks": list(self.marks), "data": self.data}

    def line(self) -> str:
        who = ", ".join(f"{m or '(no mark)'} [{g}]" for g, m in zip(self.global_ids, self.marks))
        return f"{self.rule:6s} {self.severity.upper():7s} {self.message}" + (f"  -- {who}" if who else "")


@dataclass(frozen=True)
class GateResult:
    """What a gate returns. ``passed`` is derived, never set."""

    gate: str                               # "G1 clash", "G2 data"
    ifc_path: str
    findings: tuple[Finding, ...] = ()
    blockers: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()             # what was / was NOT evaluated
    summary: dict[str, Any] = field(default_factory=dict)
    citation: str = ""

    @property
    def hard(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "hard")

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "warning")

    @property
    def passed(self) -> bool:
        return not self.blockers and not self.hard

    def status(self) -> str:
        return "PASS" if self.passed else "FAIL"

    def to_dict(self) -> dict[str, Any]:
        return {"gate": self.gate, "status": self.status(), "passed": self.passed,
                "ifc_path": self.ifc_path, "citation": self.citation,
                "blockers": list(self.blockers), "notes": list(self.notes),
                "summary": self.summary,
                "n_hard": len(self.hard), "n_warnings": len(self.warnings),
                "findings": [f.to_dict() for f in self.findings]}

    def report(self) -> str:
        out = [f"{self.gate}  [{self.status()}]  {self.ifc_path}"]
        if self.citation:
            out.append(f"  {self.citation}")
        if not self.passed:
            out.append(f"  ** {NOT_FOR_ISSUE} - this gate is open **")
        for b in self.blockers:
            out.append(f"  BLOCKER  {b}")
        for k, v in self.summary.items():
            out.append(f"  {k:<28s} : {v}")
        for n in self.notes:
            out.append(f"  note: {n}")
        if self.findings:
            out.append("")
            out += ["  " + f.line() for f in self.findings]
        return "\n".join(out)


def write_report(result: GateResult, out_dir, stem: str) -> tuple[Path, Path]:
    """Write ``<stem>.json`` and ``<stem>.html``; return both paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jp = out_dir / f"{stem}.json"
    hp = out_dir / f"{stem}.html"
    jp.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")

    e = html.escape
    rows = "".join(
        f"<tr class='{e(f.severity)}'><td>{e(f.rule)}</td><td>{e(f.severity)}</td><td>{e(f.message)}</td>"
        f"<td>{'<br>'.join(e(m or '(no mark)') for m in f.marks)}</td>"
        f"<td><code>{'<br>'.join(e(g) for g in f.global_ids)}</code></td></tr>"
        for f in result.findings)
    banner = "" if result.passed else f"<div class='banner'><b>{NOT_FOR_ISSUE}.</b> {e(result.gate)} is open.</div>"
    blockers = "".join(f"<li>{e(b)}</li>" for b in result.blockers)
    notes = "".join(f"<li>{e(n)}</li>" for n in result.notes)
    summary = "".join(f"<tr><th>{e(str(k))}</th><td>{e(str(v))}</td></tr>" for k, v in result.summary.items())
    hp.write_text(f"""<!doctype html><html><head><meta charset="utf-8"><title>{e(result.gate)}</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222}}
.banner{{background:#b00020;color:#fff;padding:10px 14px;margin:12px 0}}
table{{border-collapse:collapse;margin:8px 0}}td,th{{border:1px solid #ccc;padding:4px 8px;text-align:left;vertical-align:top}}
tr.hard td{{background:#fde8ea}}tr.warning td{{background:#fff6dd}}code{{font-size:12px}}</style></head><body>
<h1>{e(result.gate)} &mdash; {result.status()}</h1><p><code>{e(result.ifc_path)}</code></p>{banner}
<p>{e(result.citation)}</p>
{'<h2>Blockers</h2><ul>' + blockers + '</ul>' if blockers else ''}
<h2>Summary</h2><table>{summary}</table>
{'<h2>Notes</h2><ul>' + notes + '</ul>' if notes else ''}
<h2>Findings ({len(result.hard)} hard, {len(result.warnings)} warnings)</h2>
<table><tr><th>Rule</th><th>Severity</th><th>Message</th><th>Mark</th><th>GlobalId</th></tr>{rows}</table>
</body></html>""", encoding="utf-8")
    return jp, hp
