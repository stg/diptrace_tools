"""Let wheel scrolling pass over value editors without editing their values."""
from PySide6.QtCore import QObject, QEvent, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QAbstractScrollArea, QAbstractSpinBox, QComboBox, QLineEdit


class ValueWheelGuard(QObject):
    def eventFilter(self, receiver, event):
        if event.type() != QEvent.Wheel:
            return False
        control = receiver
        if isinstance(control,QLineEdit) and isinstance(control.parentWidget(),(QAbstractSpinBox,QComboBox)):
            control = control.parentWidget()
        if not isinstance(control,(QAbstractSpinBox,QComboBox)):
            return False
        # Popup lists receive their own viewport events and scroll normally.
        # Events over the editor itself belong to its surrounding scroll pane.
        area = control.parentWidget()
        while area is not None and not isinstance(area,QAbstractScrollArea):
            area = area.parentWidget()
        if area is not None:
            viewport = area.viewport()
            position = QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint()))
            forwarded = QWheelEvent(position,event.globalPosition(),event.pixelDelta(),event.angleDelta(),
                event.buttons(),event.modifiers(),event.phase(),event.inverted(),event.source())
            QApplication.sendEvent(viewport,forwarded)
        event.accept()
        return True


def protect_values_from_wheel(app):
    if not hasattr(app,'_value_wheel_guard'):
        app._value_wheel_guard = ValueWheelGuard(app)
        app.installEventFilter(app._value_wheel_guard)
