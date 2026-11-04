"""Edit a shared combined alternative from any of its member components."""
from copy import deepcopy
from uuid import uuid4
import re

from PySide6.QtCore import QLocale
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLineEdit,
    QDoubleSpinBox, QVBoxLayout, QLabel, QPlainTextEdit, QPushButton, QMessageBox)

from assembly_alternates import COMPONENT_FIELDS, local_layout, validate_groups, reanchor_placement
from bom_conversions import validate_cpl_input


class CombinedAlternateDialog(QDialog):
    def __init__(self, project, refs, group=None, parent=None):
        super().__init__(parent)
        self.project = project
        self.original = deepcopy(group)
        self.group_id = group["id"] if group else uuid4().hex
        self.group = deepcopy(group) if group else None
        self.setWindowTitle("Combined alternative")
        self.resize(760, 650)
        outer = QVBoxLayout(self)
        note = QLabel("One purchased component replaces the listed references in BOM and placement output. "
                      "Individual selections are retained. Offsets use the anchor's rotated CPL axes, in mm.")
        note.setWordWrap(True); outer.addWidget(note)
        form = QFormLayout(); outer.addLayout(form)
        g = group or {}
        self.refs = QLineEdit(", ".join(g.get("references", refs)))
        self.anchor = QLineEdit(g.get("anchor", refs[0]))
        self.offset_anchor = self.anchor.text()
        form.addRow("Included references (pin order)", self.refs)
        form.addRow("Output / anchor reference", self.anchor)
        self.fields = {}
        for field in COMPONENT_FIELDS:
            edit = QLineEdit(g.get("component", {}).get(field, ""))
            self.fields[field] = edit; form.addRow(field, edit)
        self.offsets = {}
        for key, label in (("x_mm", "Local X offset (mm)"), ("y_mm", "Local Y offset (mm)"),
                           ("rotation_deg", "Additional rotation (degrees)")):
            spin = QDoubleSpinBox(); spin.setRange(-100000, 100000); spin.setDecimals(4)
            spin.setLocale(QLocale.c())
            spin.setValue(g.get("placement", {}).get(key, 0))
            self.offsets[key] = spin; form.addRow(label, spin)
        preview = QPushButton("Preview placement")
        preview.clicked.connect(self.preview); outer.addWidget(preview)
        self.report = QPlainTextEdit(); self.report.setReadOnly(True); outer.addWidget(self.report)
        self.report.setFont(QFont("Consolas", 10))
        note = QLabel("Preview shows CPL origins, not physical pads. Confirm the replacement's footprint and pin order. "
                      "The verified relative positions are saved; generation stops if members later move apart.")
        note.setWordWrap(True); outer.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Apply")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); outer.addWidget(buttons)
        self.verified = None
        for edit in [self.refs, self.anchor, *self.fields.values()]:
            edit.textChanged.connect(self.invalidate)
        for spin in self.offsets.values():
            spin.valueChanged.connect(self.invalidate)
        self.anchor.editingFinished.connect(self.update_anchor)
        if group:
            self.preview()

    def invalidate(self, *_):
        self.verified = None
        self.report.setPlainText("Placement preview needs updating.")

    def build(self):
        refs = [r.upper() for r in re.split(r"[,;\s]+", self.refs.text().strip()) if r]
        anchor = self.anchor.text().strip().upper()
        self.update_anchor(raise_errors=True)
        g = {"id": self.group_id,
             "references": refs, "anchor": anchor,
             "active": self.original.get("active", False) if self.original else False,
             "component": {f: e.text().strip() for f,e in self.fields.items()},
             "placement": {k:s.value() for k,s in self.offsets.items()}}
        g["component"]["LCSC Part #"] = g["component"]["LCSC Part #"].upper()
        if anchor not in refs:
            raise ValueError("The anchor must be one of the included references.")
        if len(refs) != len(set(refs)) or len(refs) < 2 or set(refs)-set(self.project.records):
            raise ValueError("Include at least two distinct references present in the project.")
        g["layout"] = local_layout(validate_cpl_input(self.project.cpl_path), refs, anchor)
        validate_groups([g], self.project.records)
        return g

    def update_anchor(self, raise_errors=False):
        anchor = self.anchor.text().strip().upper()
        if anchor == self.offset_anchor:
            return
        try:
            offsets = reanchor_placement(validate_cpl_input(self.project.cpl_path), self.offset_anchor, anchor,
                                         {k:s.value() for k,s in self.offsets.items()})
            for k,v in offsets.items():
                self.offsets[k].setValue(v)
            self.offset_anchor = anchor
        except Exception as exc:
            self.invalidate()
            self.report.setPlainText(str(exc))
            if raise_errors:
                raise

    def preview(self):
        try:
            g = self.build()
            lines = ["Member origins relative to " + g["anchor"], "Reference       Local X (mm)   Local Y (mm)   Rotation"]
            for ref, (x,y,r) in g["layout"].items():
                lines.append(f"{ref:<15} {x:>10.4f} {y:>14.4f} {r:>10.2f}")
            p = g["placement"]
            lines += ["", f"Replacement {g['anchor']}: X {p['x_mm']:.4f} mm, Y {p['y_mm']:.4f} mm, rotation +{p['rotation_deg']:.2f} degrees."]
            self.report.setPlainText("\n".join(lines)); self.verified = g
        except Exception as exc:
            self.verified = None; self.report.setPlainText(str(exc))

    def accept(self):
        if self.verified is None:
            self.preview()
            if self.verified:
                QMessageBox.information(self, "Verify placement", "Review the placement preview, then click Apply again.")
            return
        try:
            g = self.build()
            if g != self.verified:
                self.preview()
                QMessageBox.information(self, "Placement changed", "Source placements changed since the preview. Review the updated preview, then Apply again.")
                return
            self.group = g
        except Exception as exc:
            QMessageBox.warning(self, "Combined alternative", str(exc)); return
        super().accept()
