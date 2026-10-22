"""
bimsteelauto_connections — connection design for the PEB pipeline.
==================================================================

Six design modules over :mod:`pyaisc360`, one result contract, one
calculation-sheet renderer.

    bolts       bolt tension/shear, AISC Manual Pt 9 prying, 358 bolt rows
    endplate    DG 4 / 358 yield-line Yp, thick-plate thickness rule
    welds       J2 fillet weld sizing + J10 column-side checks
    baseplate   DG 1 bearing, thickness, anchor tension (incl. net uplift)
    anchors     ACI 318-19 Ch. 17, all seven modes + interaction
    secondary   purlin cleats and bracing gussets

    core        DesignForce, LimitState, ConnectionResult, Connection
    report      render_html() / render_text()

Units
-----
SI everywhere: **kN, mm, MPa, kNm**.  Imperial exists only inside the
private boundary functions that call ``pyaisc360``, which is imperial-only.
Nothing crosses that boundary in user code.

Verification
------------
Every module carries a ``VERIFIED`` dict.  Design entry points refuse to
run under ``strict=True`` until the relations behind them have been
cross-checked against an independent source.

Current state:: **ALL SIXTEEN FLAGS CLOSED, 2026-08-31.**

    endplate  4E/4ES/8ES  VERIFIED  CET.SteelConnDesign V2.0
    baseplate all four    VERIFIED  AISC Design Guide 1, 2nd ed., itself
    anchors   all eight   VERIFIED  Hilti PROFIS 3.1.34
    secondary cleat       VERIFIED  CET, single plate on a W18X50 web
    secondary gusset      VERIFIED  CET, HSS brace to gusset (see below)

``ready_for_issue()`` now returns True. Read that precisely: every
relation that IS implemented has been checked against an independent
source. It does NOT mean every check a real connection needs is
implemented. The live coverage gaps are written into each module's
``VERIFIED`` comment - notably no plate flexure or weld check on the
cleat, and no gusset welds or Thornton interface forces on the gusset.

That gate is not bureaucracy. It caught eight real defects that every
relative invariant test passed straight over:

1. 4E ``Yp`` missing a ``1/s`` term and a ``-1/2``      (CET)
2. ACI 17.6.2.1.2 substitute ``hef'`` absent entirely   (PROFIS)
3. ``psi_c,V`` ignoring edge reinforcement, 20% low
4. ``AVc`` not capped by member depth - a thin slab computed STRONGER
5. imperial thread areas shifted a row: 1-1/4" read 806.5 mm2 instead
   of the 625.2 mm2 tensile stress area, so steel ran 29% high (PROFIS
   printed ``Ase = 625`` and exposed it)
6. 4ES ``Yp`` missing ``1/s``; 8ES missing a ``pb^2`` term    (CET)
7. base plate: Eq. 3.3.15 absent, and the tension-interface moment
   halved - 29% LOW and unconservative                       (DG 1)
8. cleat: bolt-group eccentricity ignored, and the B4.3b net-area
   hole allowance missing                                    (CET)

and it found errors in the reference sources themselves, which is the
part worth remembering when a tool and this package disagree:

* AISC DG 1 Example 4.7 substitutes ``f = 9.5`` where it computed
  ``f = 8.5``, and drops the ``tf/2`` of Eq. 3.4.6. Its anchor tension
  is 156 kips where its own geometry gives 181.2, and the rod it
  selects would not check out.
* CET's gusset compression line prints the correct ``Fcr`` and then
  multiplies by ``Fy``. Its 78.54 kips is ``0.9*Fy*Ag``; the correct
  ``0.9*Fcr*Ag`` is 71.89. **Do not take CET's printed gusset
  compression capacity - recompute it from the Fcr it prints.**

Design basis
------------
``CAPACITY_FRACTION = 0.50`` - connections develop not less than 50% of
the connected member's design capacity (client spec, confirmed
2026-08-30). See :data:`CAPACITY_FRACTION_SOURCE`.

Quick start
-----------
    >>> from bimsteelauto_connections import (
    ...     CAPACITY_FRACTION, design_force, design_endplate,
    ...     EndPlateGeometry, EndPlateConfig, render_html)
    >>> Mu = design_force(analysis=250.0, member_capacity=520.0,
    ...                   fraction=CAPACITY_FRACTION, label="Mu",
    ...                   unit="kNm")
    >>> Mu.value
    260.0
"""

from __future__ import annotations

__version__ = "0.1.0"

# ---- core contract ---------------------------------------------------
from .core import (  # noqa: F401
    CAPACITY_FRACTION,
    CAPACITY_FRACTION_SOURCE,
    Connection,
    ConnectionResult,
    DesignForce,
    ForceCriterion,
    LimitState,
    design_force,
    frame_summary,
    from_checks,
)

# ---- errors ----------------------------------------------------------
from .bolts import (  # noqa: F401
    ConnectionError_,
    GeometryError,
    GradeNotFound,
)

# ---- materials -------------------------------------------------------
from .bolts import (  # noqa: F401
    BOLT_GRADES,
    BoltGrade,
    get_bolt,
    get_grade,
)

# ---- bolts / prying --------------------------------------------------
from .bolts import (  # noqa: F401
    BoltRowCheck,
    BoltRowForces,
    PryingResult,
    bolt_row_forces,
    bolt_shear_capacity,
    bolt_tension_capacity,
    check_bolt_prying,
    evaluate_bolt_row,
)

# ---- end plate -------------------------------------------------------
from .endplate import (  # noqa: F401
    EndPlateConfig,
    EndPlateDesign,
    EndPlateGeometry,
    YieldLineResult,
    design_endplate,
    endplate_moment_strength,
    no_prying_moment_strength,
    required_bolt_diameter,
    required_thickness,
    required_thickness_dg4,
    yield_line_Yp,
)

# ---- welds and column side ------------------------------------------
from .welds import (  # noqa: F401
    ColumnSideCheck,
    WeldDesign,
    check_column_side,
    flange_force_from_moment,
    max_weld_size,
    min_weld_size,
    size_fillet_weld,
)

# ---- base plate ------------------------------------------------------
from .baseplate import (  # noqa: F401
    BasePlateDesign,
    BasePlateGeometry,
    BearingCase,
    concrete_bearing,
    design_base_plate,
    lambda_factor,
)

# ---- anchors ---------------------------------------------------------
from .anchors import (  # noqa: F401
    ANCHOR_GRADES,
    AnchorCheck,
    AnchorGrade,
    AnchorGroup,
    check_anchors,
    required_embedment,
)

# ---- secondary connections ------------------------------------------
from .secondary import (  # noqa: F401
    SecondaryCheck,
    check_gusset,
    check_purlin_cleat,
    whitmore_width,
)

# ---- reporting -------------------------------------------------------
from .report import render_html, render_text  # noqa: F401


def verification_status() -> dict[str, dict[str, bool]]:
    """Every module's VERIFIED flags, for a pre-issue check.

    >>> any(v for m in verification_status().values() for v in m.values())
    True
    """
    from . import anchors, baseplate, endplate, secondary
    return {
        "endplate": dict(endplate.VERIFIED),
        "baseplate": dict(baseplate.VERIFIED),
        "anchors": dict(anchors.VERIFIED),
        "secondary": dict(secondary.VERIFIED),
    }


def unverified() -> list[str]:
    """``module.item`` for every relation still unchecked."""
    return [
        f"{mod}.{item}"
        for mod, flags in verification_status().items()
        for item, ok in flags.items()
        if not ok
    ]


def ready_for_issue() -> bool:
    """True only when nothing is left unverified."""
    return not unverified()
