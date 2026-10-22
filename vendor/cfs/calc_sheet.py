"""
calc_sheet.py -- render an auditable engineering calculation sheet for
AISI S100-16 cold-formed steel member checks.

Same pattern as the AISC 360 calc-sheet renderer: accumulate Results and
Checks, then render Markdown (or minimal HTML) that a reviewing engineer
can follow line by line -- every intermediate value, every AISI clause
reference, every limit state, and an explicit verdict with a declared
scope. The verdict is deliberately conservative: ADEQUATE only when
every check performed passes, and the sheet always states what was NOT
checked.
"""

from __future__ import annotations

import datetime
from typing import List, Optional

from aisi_s100 import Result, Check


class CalcSheet:
    """Accumulates calculations and checks, then renders a sheet."""

    def __init__(self, title: str, project: str = "", member: str = "",
                 engineer: str = "", method: str = "ASD",
                 spec: str = "AISI S100-16"):
        self.title = title
        self.project = project
        self.member = member
        self.engineer = engineer
        self.method = method.upper()
        self.spec = spec
        self.date = datetime.date.today().isoformat()
        self.sections: List[tuple] = []      # (heading, body_lines)
        self.checks: List[Check] = []
        self.assumptions: List[str] = []
        self.not_checked: List[str] = []
        self.warnings: List[str] = []

    # -- input -------------------------------------------------------

    def assume(self, text: str):
        self.assumptions.append(text)
        return self

    def exclude(self, text: str):
        """Declare a limit state that was NOT evaluated."""
        self.not_checked.append(text)
        return self

    def warn(self, text: str):
        self.warnings.append(text)
        return self

    def heading(self, text: str):
        self.sections.append((text, []))
        return self

    def text(self, line: str):
        self._body().append(("text", line))
        return self

    def given(self, label: str, value, unit: str = "", ref: str = ""):
        self._body().append(("row", (label, value, unit, ref)))
        return self

    def result(self, res: Result, show_design: bool = True):
        """Append a Result object with its full audit trail."""
        body = self._body()
        body.append(("text", "**%s**" % res.name))
        for step in res.steps:
            body.append(("row", step))
        if show_design and res.phi != 1.0:
            avail = res.design(self.method)
            sym = "phi*Rn" if self.method == "LRFD" else "Rn/Omega"
            body.append(("row", ("AVAILABLE STRENGTH %s" % sym, avail,
                                 res.unit, self.method)))
        for n in res.notes:
            body.append(("note", n))
        return self

    def check(self, chk: Check):
        self.checks.append(chk)
        return self

    def check_result(self, res: Result, demand: float, label: str = ""):
        """Convenience: register a Check straight from a Result."""
        c = Check(label or res.name, demand, res.design(self.method),
                  res.unit, "")
        self.checks.append(c)
        return c

    def _body(self):
        if not self.sections:
            self.sections.append(("Calculations", []))
        return self.sections[-1][1]

    # -- verdict -----------------------------------------------------

    @property
    def max_ratio(self) -> float:
        return max((c.ratio for c in self.checks), default=0.0)

    @property
    def governing(self) -> Optional[Check]:
        return max(self.checks, key=lambda c: c.ratio) if self.checks else None

    @property
    def adequate(self) -> bool:
        return bool(self.checks) and all(c.passes for c in self.checks)

    # -- render ------------------------------------------------------

    def to_markdown(self) -> str:
        L: List[str] = []
        L.append("# %s" % self.title)
        L.append("")
        meta = [("Project", self.project), ("Member", self.member),
                ("Specification", self.spec), ("Design method", self.method),
                ("Engineer", self.engineer), ("Date", self.date)]
        L.append("| | |")
        L.append("|---|---|")
        for k, v in meta:
            if v:
                L.append("| **%s** | %s |" % (k, v))
        L.append("")

        if self.assumptions:
            L.append("## Assumptions")
            L.append("")
            for a in self.assumptions:
                L.append("- %s" % a)
            L.append("")

        for heading, body in self.sections:
            L.append("## %s" % heading)
            L.append("")
            rows = []
            for kind, payload in body:
                if kind == "row":
                    rows.append(payload)
                else:
                    L.extend(self._flush(rows)); rows = []
                    if kind == "note":
                        L.append("> %s" % payload)
                    else:
                        L.append(payload)
                    L.append("")
            L.extend(self._flush(rows))
            L.append("")

        if self.checks:
            L.append("## Limit state summary")
            L.append("")
            L.append("| Limit state | Demand | Capacity | Ratio | Status |")
            L.append("|---|---:|---:|---:|:---:|")
            for c in sorted(self.checks, key=lambda x: -x.ratio):
                L.append("| %s | %s | %s | %.3f | **%s** |" % (
                    c.limit_state, _fmt(c.demand), _fmt(c.capacity),
                    c.ratio, c.status))
            L.append("")

        L.append("## Conclusion")
        L.append("")
        if not self.checks:
            L.append("**NO CHECKS PERFORMED.** No conclusion can be drawn.")
        elif self.adequate:
            g = self.governing
            L.append("All %d limit states checked are satisfied. "
                     "Maximum utilisation **%.3f** (%.1f%%), governed by "
                     "**%s**." % (len(self.checks), self.max_ratio,
                                  100 * self.max_ratio, g.limit_state))
            L.append("")
            L.append("**The member is ADEQUATE for the limit states checked "
                     "below, under the stated assumptions.**")
            if self.max_ratio < 0.55:
                L.append("")
                L.append("> Utilisation is low. A lighter/thinner section is "
                         "likely available -- rerun with the next size down.")
        else:
            failed = [c for c in self.checks if not c.passes]
            L.append("**INADEQUATE.** %d of %d limit states are not "
                     "satisfied:" % (len(failed), len(self.checks)))
            L.append("")
            for c in failed:
                L.append("- **%s** -- demand %s exceeds capacity %s "
                         "(ratio %.3f, over by %.1f%%)" % (
                             c.limit_state, _fmt(c.demand), _fmt(c.capacity),
                             c.ratio, 100 * (c.ratio - 1)))
            L.append("")
            L.append("Increase the thickness/depth, reduce the unbraced "
                     "length or span, or revise the load path, then rerun.")
        L.append("")

        L.append("### Scope of this check")
        L.append("")
        if self.not_checked:
            L.append("The following were **NOT** evaluated and remain the "
                     "engineer's responsibility:")
            L.append("")
            for n in self.not_checked:
                L.append("- %s" % n)
        else:
            L.append("No exclusions were declared. Confirm that every "
                     "applicable limit state was in fact covered.")
        L.append("")

        if self.warnings:
            L.append("### Warnings")
            L.append("")
            for w in self.warnings:
                L.append("- %s" % w)
            L.append("")

        L.append("---")
        L.append("")
        L.append("*Computer-generated calculation sheet. Values must be "
                 "independently verified against the %s Specification "
                 "(and applicable Supplements/errata) before use in "
                 "construction. This sheet is not a substitute for review "
                 "and sealing by a licensed structural engineer. Several "
                 "functions in this library use simplified or "
                 "engineer-supplied coefficients (see aisi_s100.py module "
                 "docstring, \"VERIFICATION STATUS\") -- confirm those "
                 "against the current edition before relying on this "
                 "sheet.*" % self.spec)
        return "\n".join(L)

    @staticmethod
    def _flush(rows):
        if not rows:
            return []
        out = ["| Quantity | Value | Unit | Reference |",
               "|---|---:|---|---|"]
        for label, value, unit, ref in rows:
            out.append("| %s | %s | %s | %s |" %
                       (label, _fmt(value), unit or "", ref or ""))
        out.append("")
        return out

    def to_html(self) -> str:
        """Minimal self-contained HTML, for a browser or FreeCAD's web view."""
        try:
            import markdown  # optional
            body = markdown.markdown(self.to_markdown(), extensions=["tables"])
        except Exception:
            body = "<pre>%s</pre>" % (self.to_markdown()
                                      .replace("&", "&amp;")
                                      .replace("<", "&lt;"))
        colour = "#1a7f37" if self.adequate else "#b3261e"
        return """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>%s</title><style>
body{font:14px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;max-width:900px;
margin:2rem auto;padding:0 1.5rem;color:#1a1a1a}
h1{border-bottom:3px solid %s;padding-bottom:.4rem}
h2{margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.2rem}
table{border-collapse:collapse;width:100%%;margin:.8rem 0;font-size:13px}
th,td{border:1px solid #ccc;padding:5px 9px}
th{background:#f4f4f4;text-align:left}
blockquote{border-left:3px solid #f0a500;background:#fffbf0;margin:.6rem 0;
padding:.5rem .9rem;color:#5a4500}
strong{color:%s}code{background:#f4f4f4;padding:1px 4px}
</style></head><body>%s</body></html>""" % (self.title, colour, colour, body)

    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_html() if path.endswith(".html")
                    else self.to_markdown())
        return path


def _fmt(v):
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e6:
            return "%d" % int(v)
        if abs(v) >= 1000:
            return "%,.1f".replace(",", "") % v
        if abs(v) >= 1:
            return "%.3f" % v
        return "%.4f" % v
    return str(v)
