"""Rebuild PNG, Windows ICO and macOS ICNS resources from the SVG originals.

Run: python Shared/icons/build_icons.py
Requires PySide6 and Pillow (Pillow is only needed for rebuilding resources).
"""
import os
from io import BytesIO
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

HERE = Path(__file__).resolve().parent
TOOLS = ("jlcpcb_bom", "pcb_layout", "part_db")
ICO_SIZES = [(size, size) for size in (16, 20, 24, 32, 40, 48, 64, 128, 256)]


def render(svg, size):
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise ValueError(f"Invalid SVG: {svg}")
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return Image.open(BytesIO(bytes(buffer.data()))).convert("RGBA")


def main():
    app = QApplication.instance() or QApplication([])
    for name in TOOLS:
        svg = HERE / f"{name}.svg"
        large = render(svg, 1024)
        render(svg, 256).save(HERE / f"{name}.png")
        # Render every small frame directly from vectors, rather than scaling a bitmap.
        frames = [render(svg, size) for size, _ in ICO_SIZES]
        frames[-1].save(HERE / f"{name}.ico", sizes=ICO_SIZES, append_images=frames[:-1])
        large.save(HERE / f"{name}.icns")
        print(f"Built {name}: PNG, ICO, ICNS")
    return app


if __name__ == "__main__":
    main()
