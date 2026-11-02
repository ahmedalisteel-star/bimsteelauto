"""
bimsteelauto.fabdraw — stage 5: IFC -> fabrication drawings, behind gates.
==========================================================================

No dimension, hole position, mark or quantity on a fabrication sheet is
typed by anyone. Every number is computed from IFC geometry, and every sheet
is checked back against the IFC before it may be issued::

    IFC -> G1 clash -> G2 data (IDS) -> generate DXF -> G3 round-trip -> ISSUE

Each gate is a hard stop that writes a report (JSON + HTML) and returns a
:class:`~bimsteelauto.fabdraw.core.GateResult`. A blank is preferred to an
invented value: a constant nobody has supplied is ``None`` and fails its
gate.

Modules:

* ``gate_clash`` (G1), ``gate_ids`` (G2) - steps 1 + 2;
* ``extract`` - IFC -> assemblies / parts / holes / weights (step 3a);
* ``hlr``, ``views`` - orthographic views with hidden lines (no OpenCASCADE);
* ``drafting``, ``sheets`` - assembly sheets (A3), single-part sheets (A4),
  plate NC (DXF + DSTV);
* ``gate_roundtrip`` (G3) - every sheet read back against the IFC;
* ``run`` - the whole stage: ``python -m bimsteelauto.fabdraw.run MODEL.ifc OUT``.

The package imports nothing from the rest of ``bimsteelauto`` (only relative
imports), so it also runs on its own - the ``ifc-fab-drawings`` skill carries
a copy. It needs ``ifcopenshell``, ``ifcclash``, ``ifctester``, ``ezdxf``,
``shapely`` and ``numpy``, so it is NOT imported by ``bimsteelauto/__init__.py``:
the rest of the engine must keep running where those are absent. Import it
explicitly.
"""

from __future__ import annotations

__all__ = ["core", "ifcmodel", "gate_clash", "gate_ids", "extract", "hlr", "views", "drafting", "sheets",
           "gate_roundtrip", "run"]
