PCB Layout
==========
Python/PySide6 desktop viewer and DipTrace PCB XML replication tool.
Start the platform launcher; Help -> How to use opens the user guide.

layout_core.py parses PCB XML without modifying the source, selects internal
routing using a layer-aware graph and a block-cut forest, and builds validated
replication proposals. Source geometry and unknown XML content are preserved
semantically through lxml. Only proposed output is serialized; source files
are read-only until an explicit export chooses that same path (with backup).

layout_viewer.py uses a QGraphicsView canvas and background preview worker.
The generated preview Board is also the exported Board. Recipe JSON has a
distinct schema and filename chosen with Save As; it never occupies the BOM
editor's sidecar automatically. Source paths are relative where possible.
component_sizes.json is the central body database; settings.json is local
window/recent-file state. No dependency on DSDUC scripts or the temp viewer.

Body rectangles and polygon clearance are adapted from lister_viewer.py.
Replication/topology handling is informed by the DSDUC PCB scripts, generalized
to explicit groups, fresh identities and linear/grid/rotational transforms.

Verification:
python -m unittest discover -s PCB_Layout -p "test_*.py"

The unit fixtures contain no user design files. Desktop dispatch and launchers
are portable; native macOS/Linux desktops have not been tested here.
