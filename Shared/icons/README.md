# Application icons

The SVG originals are designed on a 32 px grid, using one bold symbol and two
colors each. Cyan identifies JLCPCB BOM (document), violet identifies PCB Layout
(placement grid), and amber identifies Part DB (storage drawers). The shared
background is a plain rounded tile; there are no outlines, pins or overlays.

Each SVG has derived resources:

- PNG: 256 px, used by the Qt applications on macOS and Linux.
- ICO: 16, 20, 24, 32, 40, 48, 64, 128 and 256 px, for Windows apps and shortcuts.
- ICNS: macOS application-icon resource, ready for packaging in an app bundle.

Rebuild with `python Shared/icons/build_icons.py` from the repository root.
Rebuilding requires PySide6 and Pillow; running the tools needs no new dependency.

Windows shortcuts should target the existing launchers and use the matching ICO
as their icon (index 0). A `.cmd` file has no individual icon; use a shortcut.
Local Start-menu shortcuts are machine-specific and are not stored in Git.
The ICNS files alone do not change the Finder icons of `.command` launchers.
