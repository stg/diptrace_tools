"""Desktop integration with the existing Windows behavior preserved."""
import os
import sys
from pathlib import Path


def configure_app_icon(app, tool):
    """Use the shared icon on every desktop and a distinct Windows taskbar identity."""
    from PySide6.QtGui import QIcon
    extension = "ico" if sys.platform == "win32" else "png"
    icon = Path(__file__).with_name("icons") / f"{tool}.{extension}"
    app.setWindowIcon(QIcon(str(icon)))
    app.setDesktopFileName("diptrace-tools-" + tool.replace("_", "-"))
    if sys.platform == "win32":
        import ctypes
        set_identity = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_identity.argtypes = [ctypes.c_wchar_p]
        set_identity.restype = ctypes.c_long
        set_identity("DevSound.DipTraceTools." + tool)


def open_file(path):
    """Open a file with the operating system's default application."""
    if sys.platform == "win32":
        os.startfile(str(path))
        return
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve()))):
        raise OSError(f"Could not open {path} with its default application")


def show_startup_error(title, message):
    """Display an error even before the main window can be created."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
        return
    print(f"{title}\n{message}", file=sys.stderr)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, title, message)
    except Exception:
        # Missing Qt dependencies still leave the traceback in the terminal/log.
        pass
