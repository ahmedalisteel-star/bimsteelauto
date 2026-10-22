"""
bimsteelauto_fc.ifc
===================

IFC4 export.

The model built by :mod:`bimsteelauto_fc.builder` is made of ``Arch``
objects carrying an ``IfcType``, which is what makes an IFC export possible
at all - a bare ``Part::Feature`` exports as geometry with no class, and a
model of unclassified solids is not a BIM model.

The spatial tree
----------------
IFC wants a containment hierarchy, and a viewer that gets a flat list of
elements will show them but will not let anyone navigate the building::

    Site  ->  Building  ->  Storey  ->  the elements

``Arch.makeSite/makeBuilding/makeFloor`` build that.  One trap, learned the
hard way on an earlier project and worth restating: ``Arch.makeFloor``
REPARENTS the objects it is given, so the group they came from ends up
empty.  Count and report from the objects themselves, never from
``group.Group`` afterwards.

Which exporter
--------------
FreeCAD 1.1 removed the importable ``exportIFC`` module that older recipes
call directly; the working route is ``nativeifc.ifc_export``.  Both are
tried, newest first, and the one that answers is recorded on the result -
so if a future FreeCAD changes it again, the failure names the thing that
moved instead of an AttributeError.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import FreeCAD as App

__all__ = ["IfcError", "IfcExport", "export_ifc", "build_spatial_tree"]


class IfcError(RuntimeError):
    """The model could not be exported."""


@dataclass
class IfcExport:
    """What was exported, how, and to where."""

    path: str = ""
    exporter: str = ""
    n_elements: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"exported {self.n_elements} elements to {self.path}",
            f"  via {self.exporter}",
        ]
        lines += [f"  ! {w}" for w in self.warnings]
        return "\n".join(lines)


def build_spatial_tree(doc, objects, *, site_name="Site",
                       building_name="Building", storey_name="Ground"):
    """Wrap the elements in Storey -> Building -> Site and return the Site.

    Returns ``None`` when Arch is unavailable, so a caller on a stripped
    FreeCAD still gets a flat export rather than an exception.
    """
    try:
        import Arch
    except ImportError:                                  # pragma: no cover
        return None

    storey = Arch.makeFloor(list(objects))
    storey.Label = storey_name
    building = Arch.makeBuilding([storey])
    building.Label = building_name
    site = Arch.makeSite([building])
    site.Label = site_name
    doc.recompute()
    return site


def _export_nativeifc(objects, path) -> str:
    from nativeifc import ifc_export        # FreeCAD 1.1+

    ifc_export.export(list(objects), path)
    return "nativeifc.ifc_export.export"


def _export_legacy(objects, path) -> str:
    import importFreeCAD  # noqa: F401  - probe; not used directly
    raise IfcError("no legacy exporter on this build")


def _export_arch(objects, path) -> str:
    from nativeifc import ifc_export

    # Some builds expose the old module through the new one.
    ifc_export.exportIFC.export(list(objects), path)
    return "nativeifc.ifc_export.exportIFC.export"


def export_ifc(
    doc,
    path: str,
    *,
    objects=None,
    spatial: bool = True,
    project_name: str = "",
) -> IfcExport:
    """Write the model to an IFC4 file.

    ``objects`` defaults to every element carrying a ``MemberType``, i.e.
    everything the builder made - not every object in the document. The
    builder no longer leaves a ``Part::Feature`` base behind each Arch object
    (see ``builder._structure``), but a document opened from an older build
    still has them, and exporting those as well would duplicate the whole
    building.
    """
    if objects is None:
        objects = [o for o in doc.Objects if hasattr(o, "MemberType")]
    objects = list(objects)
    if not objects:
        raise IfcError(
            "there is nothing to export: no object in this document carries "
            "a MemberType, so the model was not built by this adapter"
        )

    out = IfcExport(path=path, n_elements=len(objects))

    export_set = objects
    if spatial:
        site = build_spatial_tree(
            doc, objects,
            site_name=(project_name or doc.Name) + " site",
            building_name=project_name or doc.Name,
        )
        if site is None:
            out.warnings.append(
                "Arch is not available, so the export has no spatial tree - "
                "a viewer will show the elements but not the building"
            )
        else:
            export_set = [site]

    errors = []
    for fn in (_export_nativeifc, _export_arch, _export_legacy):
        try:
            out.exporter = fn(export_set, path)
            break
        except Exception as exc:                        # noqa: BLE001
            errors.append(f"{fn.__name__}: {type(exc).__name__}: {exc}")
    else:
        raise IfcError(
            "every IFC exporter this adapter knows about failed. FreeCAD "
            "moves this API between releases, so the list below is the "
            "thing to update:\n  " + "\n  ".join(errors)
        )

    import os

    if not os.path.exists(path) or os.path.getsize(path) == 0:
        raise IfcError(
            f"{out.exporter} reported success but {path} is missing or empty"
        )
    return out


def summarise_ifc(path: str) -> dict[str, int]:
    """Count the IFC entities in a written file, by class.

    Used to CHECK an export rather than to trust it: an exporter that
    silently writes a file containing a site and no members would otherwise
    look like a success.
    """
    try:
        import ifcopenshell
    except ImportError:                                  # pragma: no cover
        return {}
    f = ifcopenshell.open(path)
    counts: dict[str, int] = {}
    for entity in f:
        name = entity.is_a()
        if name.startswith("Ifc"):
            counts[name] = counts.get(name, 0) + 1
    return counts
