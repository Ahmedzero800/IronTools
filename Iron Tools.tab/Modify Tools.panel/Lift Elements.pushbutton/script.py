# -*- coding: utf-8 -*-
"""Moves the selected elements up or down (Z) by a value in mm that you type."""

# ---------------------------------------------------------------
# Lift Elements - IronTools
# Select elements -> click the button -> type a value in mm.
# Positive value = up, negative value = down.
# All moves happen in ONE transaction, so Ctrl+Z undoes everything.
# Dialogs come from lib\irontools_ui.py (dark card UI).
# ---------------------------------------------------------------

from pyrevit import revit, DB, script
from System.Collections.Generic import List
import irontools_ui as ui

doc = revit.doc
uidoc = revit.uidoc
TITLE = "Lift Elements"


def fmt_mm(value):
    """Format a mm value without a useless .0"""
    return ("%.1f" % value).rstrip("0").rstrip(".")


# 1) Read the current selection
elements = [el for el in revit.get_selection().elements if el is not None]

if not elements:
    ui.show_result(TITLE, "warning", "Nothing is selected",
                   details=["Select the elements you want to move, then click the button again."])
    script.exit()

# 2) Ask for the offset value in mm (remember the last value used)
cfg = script.get_config()
last_value = cfg.get_option("last_offset_mm", "100")

offset_mm = ui.ask_number(
    TITLE,
    subtitle="Move the selection up or down",
    rows=[("Selected elements", str(len(elements)))],
    default=last_value,
    unit="mm",
    label="Offset",
    hint="Positive = up,  negative = down",
    chips=["-100", "-50", "+50", "+100"],
    icon="lift",
    ok_text="Move",
    allow_zero=False)

if offset_mm is None:
    script.exit()  # user pressed Cancel / closed the dialog

cfg.last_offset_mm = fmt_mm(offset_mm)
script.save_config()

# 3) Convert mm -> Revit internal feet
move_vector = DB.XYZ(0, 0, offset_mm / 304.8)

# 4) Skip pinned elements (Revit refuses to move them)
pinned = [el for el in elements if getattr(el, "Pinned", False)]
movable = [el for el in elements if not getattr(el, "Pinned", False)]

moved_count = 0
failed = []

# 5) Move everything inside a single transaction
with revit.Transaction("Lift Elements {} mm".format(fmt_mm(offset_mm))):
    ids = List[DB.ElementId]([el.Id for el in movable])
    try:
        # Fast path: move all together (keeps connected MEP elements together)
        if ids.Count > 0:
            DB.ElementTransformUtils.MoveElements(doc, ids, move_vector)
            moved_count = ids.Count
    except Exception:
        # Fallback: move one by one and collect the ones that fail
        for el in movable:
            try:
                DB.ElementTransformUtils.MoveElement(doc, el.Id, move_vector)
                moved_count += 1
            except Exception as err:
                failed.append((el, str(err)))

# 6) Decide the status: green / amber / red
direction = "up" if offset_mm > 0 else "down"
details = []
if pinned:
    details.append("{} pinned element(s) were skipped. Unpin them to move.".format(len(pinned)))
if failed:
    details.append("{} element(s) could not be moved (listed in the pyRevit output window)."
                   .format(len(failed)))
    out = script.get_output()
    out.print_md("### Lift Elements - failed elements")
    for el, err in failed:
        out.print_md("- {} : {}".format(out.linkify(el.Id), err))

if moved_count == 0:
    status, headline = "error", "No elements were moved"
elif details:
    status, headline = "warning", "Moved, with some elements skipped"
else:
    status, headline = "success", "All elements moved successfully"

sign = "+" if offset_mm > 0 else "-"
ui.show_result(
    TITLE, status, headline,
    stats=[(str(moved_count), "Elements moved"),
           (sign + fmt_mm(abs(offset_mm)), "mm " + direction)],
    details=details)
