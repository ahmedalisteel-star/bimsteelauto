"""
BIMSteelAutoWB.bsa_dialogs - the small Qt dialogs the commands put up.

Kept out of ``bsa_commands.py`` because everything in that file is a
FreeCAD command, and a test enforces it: a class there without
``GetResources`` / ``IsActive`` / ``Activated`` is a button that will not
work, so the check cannot be relaxed just to give a dialog somewhere to
live.

Imported inside a command's ``Activated``, never at module scope.
"""

try:
    from PySide import QtWidgets
except ImportError:                                      # pragma: no cover
    from PySide2 import QtWidgets


class ContourDialog(QtWidgets.QDialog):
    """What to colour by, for which combination, and how much to magnify.

    The combination list is read from the file rather than typed, so a
    combination that was never solved cannot be asked for.
    """

    #: Everything that can be drawn.  The six force components were added
    #: when the 3D building model arrived - they are the diagrams people
    #: ask a model for by name.
    _QUANTITIES = (
        ("Resultant displacement  [mm]", "displacement"),
        ("Extreme fibre stress  [MPa]", "stress"),
        ("AISC 360-16 utilisation  [-]", "utilisation"),
        ("Axial force P  [kN]", "axial"),
        ("Major-axis shear Vy  [kN]", "shear"),
        ("Major-axis moment Mz  [kN.m]", "moment"),
        ("Minor-axis shear Vz  [kN]", "shear_z"),
        ("Torsion T  [kN.m]", "torsion"),
        ("Minor-axis moment My  [kN.m]", "moment_y"),
    )

    #: Which of those a PLANE FRAME cannot produce.  They are offered
    #: anyway and they draw a flat zero, because that IS a plane frame's
    #: answer - but the label says so, so nobody reads a flat torsion
    #: diagram as a result about the building.
    _OUT_OF_PLANE = {"shear_z", "torsion", "moment_y"}

    def __init__(self, cs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("BIMSteelAuto - result contours")
        self._cs = cs
        is_3d = bool(getattr(cs, "is_3d", False))

        self.quantity = QtWidgets.QComboBox()
        for label, key in self._QUANTITIES:
            if key in self._OUT_OF_PLANE and not is_3d:
                label += "   (zero - this is a plane frame)"
            elif key == "utilisation" and is_3d:
                label += "   (all six components)"
            self.quantity.addItem(label, key)

        self.combo = QtWidgets.QComboBox()
        for c in cs.combos:
            self.combo.addItem(
                c + ("   (governs)" if c == cs.governing_combo else ""), c
            )
        if cs.governing_combo in cs.combos:
            self.combo.setCurrentIndex(list(cs.combos).index(cs.governing_combo))

        self.auto = QtWidgets.QCheckBox("automatic")
        self.auto.setChecked(True)
        self.scale = QtWidgets.QDoubleSpinBox()
        self.scale.setRange(1.0, 100000.0)
        self.scale.setDecimals(0)
        self.scale.setSingleStep(10.0)
        self.scale.setValue(round(cs.auto_scale(cs.governing_combo or cs.combos[0])))
        self.scale.setEnabled(False)
        self.auto.toggled.connect(lambda on: self.scale.setEnabled(not on))

        self.undeformed = QtWidgets.QCheckBox("show the undeformed frame")
        self.undeformed.setChecked(True)

        form = QtWidgets.QFormLayout()
        form.addRow("Colour by", self.quantity)
        form.addRow("Combination", self.combo)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.scale)
        row.addWidget(self.auto)
        form.addRow("Deflection x", row)
        form.addRow("", self.undeformed)

        what = (
            f"Whole building in 3D - {len(cs.members)} members."
            if is_3d
            else f"One frame line, {cs.frame_y / 1000.0:.1f} m along the "
                 f"building."
        )
        note = QtWidgets.QLabel(
            f"{what}\n"
            "The magnification changes the picture only.\n"
            "Every number printed on it is the real one."
        )
        note.setStyleSheet("color: #666;")

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(note)
        lay.addWidget(buttons)

    def choice(self):
        return (
            self.quantity.currentData(),
            self.combo.currentData(),
            None if self.auto.isChecked() else float(self.scale.value()),
            self.undeformed.isChecked(),
        )
