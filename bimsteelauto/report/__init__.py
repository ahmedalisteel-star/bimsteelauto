"""
bimsteelauto.report
===================

The calculation sheet.  Zero dependencies - it renders inside FreeCAD's
bundled interpreter with nothing installed.
"""

from .calcsheet import (  # noqa: F401
    DISCLAIMER,
    collect_coverage_gaps,
    render_html,
    render_text,
)

__all__ = ["render_html", "render_text", "collect_coverage_gaps", "DISCLAIMER"]
