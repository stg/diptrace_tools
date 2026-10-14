DipTrace Part Database
==========================

Windows: double-click Launch Part Database.cmd.
macOS: use Launch Part Database.command.
Linux: run sh "Launch Part Database.sh".
See the root README.md for dependency installation.
Alternatively: python db_browser.py
Install dependencies: python -m pip install -r requirements.txt

Tools -> Settings opens the configuration dialog. Choose a base directory,
search fields and the default expansion preference; Save applies and remembers
these choices. Cancel leaves them unchanged. Then Refresh database (F5). Only binary .dch
schematics are scanned, recursively. XML exports and backups with other
extensions are excluded. Symlinks and common development cache directories
are skipped. Source schematics are never modified.

Search
------
Name, Value, Pattern and Filename each have a checkbox in Tools -> Settings. Search ignores case;
all whitespace-separated words must match the checked fields. RefDes is shown
but never searched. With Filename checked, words can match part fields
and the filename of the same occurrence. Only matching file rows are shown.
An empty search shows all parts. A nonempty search with no boxes checked
shows no results. Filename displays the path from the scanned base directory.

Results start collapsed. Expand a part to show an inset table containing
Filename and exact RefDes, with its own column widths and a subtle background.
Long lists scroll inside the inset table. These controls are created on demand.
Tools -> Settings remembers an optional expanded default;
Edit also expands/collapses the current results. Detail column widths are
remembered separately from the part columns.
Matching multipart occurrences of the same RefDes are counted once within a
part group. Groups with different Name/Value/Pattern remain distinct.
The extractor follows the original DB behavior: blank designators and NetPort
designators are excluded; empty Name/Value/Pattern are preserved.

Double-click a file row, use File -> Open selected schematic (Ctrl+O), or
right-click -> Open schematic. This uses the normal default file handler.
Header clicks sort results. Edit expands/collapses results. Right-click also
copies filenames/RefDes; Ctrl+C copies selected rows as tab-separated text.
Ctrl+F focuses and selects the search text.

Storage and refresh
-------------------
parts.json, settings.json, part_locations.json and normalization_rules.json
live beside this program. Location labels are saved separately from scans.
The DB stores only base directory, refresh time, rule/version cache signature,
per-file timestamps/sizes and part groups with RefDes, plus scan errors
and the most recent read/reuse counts. The search view is derived in memory.

Refresh discovers files again, reuses unchanged files by modification time
(nanoseconds) and byte size, parses new/changed files, and removes deleted files.
Changing the base directory or normalization rules invalidates the cache. Tools ->
Rebuild database reads every file, including files whose timestamps/sizes
were preserved by an external tool. Stop refresh retains the previous DB;
the current decoder call finishes before cancellation is handled.

Individual unreadable/unsupported files are listed in the log and in the DB
failures list, and are omitted from current search results. They are retried
on the next refresh. Last refreshed is the time the scan completed, including
scans with errors. A cancelled or failed refresh preserves the previous DB.
The JSON is replaced atomically only after a completed scan.

Base directory, search text, search checkboxes, window position/size, column
widths/order/sorting, detail column widths, the default expansion preference,
and the table/log split are remembered in settings.json.
Tools -> Open program settings JSON opens this file in the default editor. Exit the
program before editing it externally; changes load at the next launch.

Normalization rules
-------------------
Help -> How to use opens HELP.txt, a short user guide.
Help -> Normalization rule reference opens Shared/NORMALIZATION.txt, which
fully documents the ordered rule language, validation and compatibility modes.

Tools -> Open normalization rules opens normalization_rules.json. Rules are
validated and re-read on refresh. Names, values and optionally patterns are
normalized before grouping; RefDes and schematic files cannot be changed.
A rule change invalidates cached rows; descriptions alone do not.

The shipped Part_DB rules remove exact RES/CAP/IND names, format numeric resistor,
capacitor and inductor values, simplify recognized package patterns and keep DNP/DNM parts. All local conventions
live in JSON. regex_replace supports captures and optional conditions; set
assigns text; format_quantity handles electronic shorthand. See the shared
reference for complete syntax and examples. JLCPCB_BOM uses the same engine,
with its own default file to preserve its established output behavior.

Parser provenance
-----------------
dch_parser is a standalone copy of structural decoder/schema modules and
TreeQuery from DipTrace-ReverseEngineer/apps/lib. Imports were renamed to
the local package. The reverse-engineering project is not needed at runtime.
Shared/value_normalization.py adapts the discrete-value formatter and SI symbols
from JLCPCB_BOM/bom_conversions.py and bom_values.py. It guards against changing
non-numeric text. JLCPCB_BOM is not needed at runtime. Keep the Shared sibling folder with Part_DB.
