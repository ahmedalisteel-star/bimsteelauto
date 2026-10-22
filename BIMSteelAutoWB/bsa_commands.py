"""
BIMSteelAutoWB.bsa_commands - what the buttons do.

Five commands:

    Build model     design_result.json  ->  model + IFC + MTO + drawings
    Result contours contours*.json      ->  the deflected structure, coloured by
                                            displacement, stress or utilisation
    Open output     show the folder that was written
    Design status   is the design behind this model issuable?
    Settings        where the engine package lives

Everything expensive is imported inside ``Activated``.  FreeCAD loads this
module when the workbench is first selected, and an import of the adapter at
that moment would drag FreeCAD's start-up into the engine's dependencies for
no reason.

Where the engine lives
----------------------
The adapter is ``bimsteelauto_fc`` inside the ``peb`` package folder.  The
path is a PREFERENCE, not a constant, so moving the package does not mean
editing code - and the same preference is shared with the standalone
``BIMSteelAuto.FCMacro``.
"""

import os
import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui

try:
    from PySide import QtCore, QtGui, QtWidgets
except ImportError:                                      # pragma: no cover
    from PySide2 import QtCore, QtGui, QtWidgets

PARAM = "User parameter:BaseApp/Preferences/Mod/BIMSteelAuto"

def _default_peb() -> str:
    """Find the package WITHOUT a hardcoded path.

    The workbench lives at ``<root>\\BIMSteelAutoWB`` with the package at
    ``<root>\\peb``, so its own location gives the answer. It is installed
    into FreeCAD's Mod folder as a junction, and ``realpath`` follows that
    back to the real folder - which is what lets the whole thing move to
    another PC, or another drive, with no code edit at all.

    Falls back to this PC's usual location; after that the saved
    preference wins, and after that the user is asked.
    """
    here = os.path.dirname(os.path.realpath(__file__))
    for guess in (os.path.dirname(here),                       # repo layout
                  os.path.join(os.path.dirname(here), "peb")):  # peb sibling
        if os.path.isdir(os.path.join(guess, "bimsteelauto_fc")):
            return guess
    return r"<your-folder>\bimsteelauto"


#: Only a starting point - the saved preference wins, and the Settings
#: button writes it.
DEFAULT_PEB = _default_peb()

COMMANDS = (
    "BSA_BuildModel",
    "BSA_Contours",
    "BSA_DesignStatus",
    "BSA_OpenOutput",
    "BSA_Settings",
)


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


def _p():
    return App.ParamGet(PARAM)


def peb_root() -> str:
    return _p().GetString("PebRoot", DEFAULT_PEB)


def set_peb_root(path: str) -> None:
    _p().SetString("PebRoot", path)


def last_json() -> str:
    return _p().GetString("LastJson", os.path.join(peb_root(), "design_result.json"))


def set_last_json(path: str) -> None:
    _p().SetString("LastJson", path)


def last_out() -> str:
    return _p().GetString("LastOut", "")


def _ensure_path() -> str:
    """Put the engine package on ``sys.path`` and return its root.

    Raises with a message a user can act on rather than an ImportError six
    frames deep.
    """
    root = peb_root()
    if not os.path.isdir(root):
        raise RuntimeError(
            f"The BIMSteelAuto package folder was not found:\n  {root}\n\n"
            "Use BIMSteelAuto -> Settings to point at the 'peb' folder."
        )
    if not os.path.isdir(os.path.join(root, "bimsteelauto_fc")):
        raise RuntimeError(
            f"{root}\n\ndoes not contain bimsteelauto_fc - that is not the "
            "'peb' package folder."
        )
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


def _warn(title: str, text: str) -> None:
    App.Console.PrintWarning(text + "\n")
    QtWidgets.QMessageBox.warning(None, title, text)


def _error(title: str, text: str) -> None:
    App.Console.PrintError(text + "\n")
    box = QtWidgets.QMessageBox()
    box.setIcon(QtWidgets.QMessageBox.Critical)
    box.setWindowTitle(title)
    box.setText(text[:400])
    if len(text) > 400:
        box.setDetailedText(text)
    box.exec_()


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


class BuildModel:
    """Read a design result and build everything from it."""

    def GetResources(self):
        return {
            "Pixmap": "Arch_Structure",
            "MenuText": "Build model",
            "ToolTip": (
                "Read the engine's design_result.json and build the BIM "
                "model, IFC, member schedule and 2D drawings"
            ),
            "Accel": "B, M",
        }

    def IsActive(self):
        return True

    def Activated(self):
        try:
            _ensure_path()
        except RuntimeError as exc:
            _warn("BIMSteelAuto", str(exc))
            return

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "BIMSteelAuto - the engine's design result",
            last_json(), "Design result (*.json);;All files (*)",
        )
        if not path:
            return
        set_last_json(path)
        out_dir = os.path.join(os.path.dirname(path), "out")

        from bimsteelauto_fc import build_all

        App.Console.PrintMessage("BIMSteelAuto: building...\n")
        QtWidgets.QApplication.setOverrideCursor(
            QtGui.QCursor(QtCore.Qt.WaitCursor)
        )
        try:
            run = build_all(path, out_dir)
        except Exception:                                # noqa: BLE001
            _error("BIMSteelAuto - build failed", traceback.format_exc())
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        _p().SetString("LastOut", out_dir)
        App.Console.PrintMessage(run.summary() + "\n")

        doc = run.build.document
        if doc is not None:
            try:
                Gui.ActiveDocument = Gui.getDocument(doc.Name)
                Gui.activeDocument().activeView().viewAxonometric()
                Gui.SendMsgToActiveView("ViewFit")
            except Exception:                            # noqa: BLE001
                pass
        _show_report(run, out_dir)


def _show_report(run, out_dir: str) -> None:
    from bimsteelauto_fc.schedule import schedule_totals

    r = run.result
    t = schedule_totals(run.schedule)
    g = r.spec.geometry
    plan = g.width * g.length * 1e-6

    ready = r.ready_for_issue()
    html = [f"<h3>{r.spec.project_name}</h3>",
            f"<p>Job <b>{r.spec.job_number}</b></p>"]
    if ready:
        html.append("<p style='color:#1d6b3f'><b>READY FOR ISSUE</b></p>")
    else:
        html.append(
            "<p style='color:#a3121f'><b>NOT FOR ISSUE</b> - every drawing "
            "written carries this stamp</p><ul>"
        )
        for b in r.issue_blockers()[:8]:
            html.append(f"<li>{b}</li>")
        html.append("</ul>")
    html += [
        f"<p><b>{run.build.n_built}</b> members, "
        f"<b>{t['total_kg']:,.0f} kg</b> "
        f"({t['total_kg'] / plan:,.1f} kg/m&sup2;)<br>",
        f"main {t['main_kg']:,.0f} kg &middot; secondary "
        f"{t['secondary_kg']:,.0f} kg</p>",
        f"<p><b>Written to</b><br><tt>{out_dir}</tt></p><ul>",
    ]
    if run.ifc:
        html.append(f"<li>IFC4, {run.ifc.n_elements} elements</li>")
    html.append("<li>schedule.csv</li>")
    if run.ga:
        html.append(f"<li>{len(run.ga.sheets)} GA sheets</li>")
    if run.parts:
        html.append(f"<li>{len(run.parts.sheets)} single-part drawings</li>")
    if run.dxf:
        html.append(f"<li>{len(run.dxf.sheets)} DXF files</li>")
    html.append("</ul>")

    notes = list(run.build.warnings) + list(run.warnings)
    if run.build.skipped:
        notes += [f"NOT BUILT - {m}: {w}" for m, w in run.build.skipped]
    if notes:
        html.append("<p style='color:#8a5a00'><b>Notes</b><ul>")
        for n in notes[:8]:
            html.append(f"<li>{n}</li>")
        html.append("</ul></p>")

    box = QtWidgets.QMessageBox()
    box.setWindowTitle("BIMSteelAuto")
    box.setTextFormat(QtCore.Qt.RichText)
    box.setText("".join(html))
    box.exec_()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class DesignStatus:
    """Say whether the design behind the open model is issuable."""

    def GetResources(self):
        return {
            "Pixmap": "Std_DlgParameter",
            "MenuText": "Design status",
            "ToolTip": (
                "Read a design result and report its blockers, unverified "
                "relations and coverage gaps - without building anything"
            ),
        }

    def IsActive(self):
        return True

    def Activated(self):
        try:
            _ensure_path()
        except RuntimeError as exc:
            _warn("BIMSteelAuto", str(exc))
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "BIMSteelAuto - design result to check",
            last_json(), "Design result (*.json);;All files (*)",
        )
        if not path:
            return

        from bimsteelauto_fc.builder import load_result

        try:
            r = load_result(path)
        except Exception:                                # noqa: BLE001
            _error("BIMSteelAuto", traceback.format_exc())
            return

        html = [f"<h3>{r.spec.project_name}</h3>",
                f"<p>Job {r.spec.job_number} &middot; engine "
                f"{r.engine_version} &middot; schema {r.schema_version}</p>"]
        if r.ready_for_issue():
            html.append("<p style='color:#1d6b3f'><b>READY FOR ISSUE</b></p>")
        else:
            html.append("<p style='color:#a3121f'><b>NOT FOR ISSUE</b></p><ul>")
            for b in r.issue_blockers():
                html.append(f"<li>{b}</li>")
            html.append("</ul>")
        html.append(
            f"<p>max utilisation <b>{r.max_utilisation:.3f}</b> &middot; "
            f"{len(r.members)} members &middot; "
            f"{len(r.connections)} connections</p>"
        )
        if r.unverified:
            html.append(
                f"<p style='color:#a3121f'>{len(r.unverified)} UNVERIFIED "
                "relations</p>"
            )
        else:
            html.append(
                "<p style='color:#1d6b3f'>every VERIFIED flag in the engine "
                "is closed</p>"
            )
        html.append(
            f"<p>{len(r.coverage_gaps)} declared coverage gaps and "
            f"{len(r.assumptions)} assumptions - both print on the "
            "calculation sheet</p>"
        )

        box = QtWidgets.QMessageBox()
        box.setWindowTitle("BIMSteelAuto - design status")
        box.setTextFormat(QtCore.Qt.RichText)
        box.setText("".join(html))
        if r.coverage_gaps:
            box.setDetailedText(
                "COVERAGE GAPS\n\n" + "\n\n".join(r.coverage_gaps)
                + "\n\nASSUMPTIONS\n\n" + "\n\n".join(r.assumptions)
            )
        box.exec_()


# ---------------------------------------------------------------------------
# Open output / settings
# ---------------------------------------------------------------------------


class OpenOutput:
    def GetResources(self):
        return {
            "Pixmap": "Std_ViewHome",
            "MenuText": "Open output folder",
            "ToolTip": "Show the folder the last build wrote to",
        }

    def IsActive(self):
        return bool(last_out())

    def Activated(self):
        out = last_out()
        if not out or not os.path.isdir(out):
            _warn("BIMSteelAuto", "Nothing has been built yet.")
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(out))


class Settings:
    def GetResources(self):
        return {
            "Pixmap": "Std_DlgMacroExecute",
            "MenuText": "Settings",
            "ToolTip": "Where the BIMSteelAuto 'peb' package folder lives",
        }

    def IsActive(self):
        return True

    def Activated(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            None, "Pick the BIMSteelAuto 'peb' package folder", peb_root()
        )
        if not path:
            return
        if not os.path.isdir(os.path.join(path, "bimsteelauto_fc")):
            _warn(
                "BIMSteelAuto",
                f"{path}\n\ndoes not contain bimsteelauto_fc, so it is not "
                "the 'peb' package folder.",
            )
            return
        set_peb_root(path)
        QtWidgets.QMessageBox.information(
            None, "BIMSteelAuto", f"Package folder set to:\n{path}"
        )


# ---------------------------------------------------------------------------
# Result contours
# ---------------------------------------------------------------------------


class Contours:
    """Draw the analysis result on the frame, the way STAAD draws it."""

    def GetResources(self):
        return {
            "Pixmap": "Std_ViewIsometric",
            "MenuText": "Result contours",
            "ToolTip": (
                "Colour the deflected structure by displacement, stress, "
                "utilisation or any force component - P, Vy, Vz, Mz, My, T "
                "- from the engine's contours.json (one frame line) or "
                "contours3d.json (the whole building)"
            ),
            "Accel": "B, C",
        }

    def IsActive(self):
        return True

    def Activated(self):
        try:
            _ensure_path()
        except RuntimeError as exc:
            _warn("BIMSteelAuto", str(exc))
            return

        start = _p().GetString("LastContours", "") or os.path.join(
            os.path.dirname(last_json()), "contours.json"
        )
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "BIMSteelAuto - the engine's contour file", start,
            "Contours (contours3d.json contours.json *.json);;"
            "All files (*)",
        )
        if not path:
            return
        _p().SetString("LastContours", path)

        from bimsteelauto.analysis.results import load_contours
        from bsa_dialogs import ContourDialog

        try:
            cs = load_contours(path)
        except Exception:                                # noqa: BLE001
            _error("BIMSteelAuto - contour file", traceback.format_exc())
            return

        dlg = ContourDialog(cs)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        quantity, combo, scale, undeformed = dlg.choice()

        from bimsteelauto_fc.contours import draw_contours

        QtWidgets.QApplication.setOverrideCursor(
            QtGui.QCursor(QtCore.Qt.WaitCursor)
        )
        try:
            drawn = draw_contours(
                cs, quantity=quantity, combo=combo, scale=scale,
                show_undeformed=undeformed,
            )
        except Exception:                                # noqa: BLE001
            _error("BIMSteelAuto - contour", traceback.format_exc())
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        App.Console.PrintMessage(drawn.summary() + "\n")
        # Draft leaves what it creates selected, and FreeCAD paints a
        # selected object in the highlight colour - which on a contour means
        # every label comes out green and the colours you are reading are
        # not the ones the legend explains.
        Gui.Selection.clearSelection()
        try:
            Gui.activeDocument().activeView().viewFront()
            Gui.SendMsgToActiveView("ViewFit")
        except Exception:                                # noqa: BLE001
            pass

        html = [
            f"<h3>{cs.project_name}</h3>",
            f"<p>job {cs.job_number} &middot; frame line "
            f"{cs.frame_index + 1}"
            + ("  &middot; second-order (P-Delta)" if cs.p_delta else "")
            + "</p>",
            f"<p><b>{drawn.quantity}</b> &middot; {drawn.combo}</p>",
            f"<p>range <b>{drawn.vmin:,.4g}</b> to <b>{drawn.vmax:,.4g}</b> "
            f"{drawn.unit}<br>peak on <b>{drawn.peak_member}</b> at "
            f"x = {drawn.peak_x:,.0f} mm</p>",
            f"<p>deflected shape magnified <b>x {drawn.scale:,.0f}</b> - "
            "the picture only; the numbers are unscaled</p>",
        ]
        if drawn.notes:
            html.append(
                "<p style='color:#a3121f'><b>the engine's build-time checks "
                "did not all pass</b><ul>"
            )
            for n in drawn.notes[:6]:
                html.append(f"<li>{n}</li>")
            html.append("</ul></p>")

        box = QtWidgets.QMessageBox()
        box.setWindowTitle("BIMSteelAuto - result contours")
        box.setTextFormat(QtCore.Qt.RichText)
        box.setText("".join(html))
        box.exec_()


_CLASSES = {
    "BSA_BuildModel": BuildModel,
    "BSA_Contours": Contours,
    "BSA_DesignStatus": DesignStatus,
    "BSA_OpenOutput": OpenOutput,
    "BSA_Settings": Settings,
}


def register_commands() -> None:
    """Add every command to FreeCAD, once.

    Re-registering a name is harmless in FreeCAD but noisy, so it is
    guarded - the workbench can be Initialize()d again after a reload.
    """
    existing = set(Gui.listCommands())
    for name, cls in _CLASSES.items():
        if name not in existing:
            Gui.addCommand(name, cls())
