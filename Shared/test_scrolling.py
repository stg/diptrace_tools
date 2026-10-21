import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import unittest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QScrollArea, QVBoxLayout, QWidget

from Shared.scrolling import protect_values_from_wheel


class WheelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        protect_values_from_wheel(cls.app)

    def setUp(self):
        self.area = QScrollArea(); self.area.resize(300,180)
        content = QWidget(); content.setMinimumHeight(1200); layout = QVBoxLayout(content)
        self.spin = QDoubleSpinBox(); self.spin.setValue(5); layout.addWidget(self.spin)
        self.combo = QComboBox(); self.combo.addItems([str(i) for i in range(100)])
        self.combo.setCurrentIndex(8); layout.addWidget(self.combo); layout.addStretch()
        self.area.setWidgetResizable(True); self.area.setWidget(content); self.area.show()
        self.app.processEvents()

    def tearDown(self):
        self.area.close(); self.area.deleteLater(); self.app.processEvents()

    def wheel(self,widget):
        position = QPointF(widget.rect().center()); global_position = QPointF(widget.mapToGlobal(position.toPoint()))
        event = QWheelEvent(position,global_position,QPoint(),QPoint(0,-120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
        self.app.sendEvent(widget,event); self.app.processEvents()

    def test_focused_editors_do_not_change_and_their_pane_scrolls(self):
        for control in [self.spin,self.spin.lineEdit(),self.combo]:
            with self.subTest(control=type(control).__name__):
                self.area.verticalScrollBar().setValue(0); control.setFocus(); self.app.processEvents()
                self.wheel(control)
                self.assertEqual(self.spin.value(),5)
                self.assertEqual(self.combo.currentIndex(),8)
                self.assertGreater(self.area.verticalScrollBar().value(),0)

    def test_controls_outside_scroll_panes_ignore_wheel(self):
        spin = QDoubleSpinBox(); spin.setValue(7); spin.show()
        combo = QComboBox(); combo.addItems(['A','B','C']); combo.setCurrentIndex(1); combo.show()
        try:
            self.wheel(spin); self.wheel(combo)
            self.assertEqual(spin.value(),7); self.assertEqual(combo.currentIndex(),1)
        finally:
            spin.close(); combo.close()

    def test_open_combo_list_scrolls_without_committing_another_value(self):
        self.combo.showPopup(); self.app.processEvents()
        view = self.combo.view(); view.verticalScrollBar().setValue(0)
        before = view.verticalScrollBar().value()
        self.wheel(view.viewport())
        self.assertEqual(self.combo.currentIndex(),8)
        self.assertGreater(view.verticalScrollBar().value(),before)
        self.combo.hidePopup()


if __name__ == '__main__':
    unittest.main()
