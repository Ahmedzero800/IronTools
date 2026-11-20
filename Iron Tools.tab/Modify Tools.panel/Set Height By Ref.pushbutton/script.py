# -*- coding: utf-8 -*-
"""Pick one reference element from the selection, type its new height from its level (mm), and the whole selection moves by the same difference."""

# ---------------------------------------------------------------
# Set Height By Ref - IronTools
# 1) Select a group of elements
# 2) Click the button and pick ONE element from that group (the reference)
# 3) The dialog shows its current height from its level -> type the new one
# 4) Every selected element moves up/down by (new - current)
# All moves happen in ONE transaction, so Ctrl+Z undoes everything.
# Dialogs come from lib\irontools_ui.py (dark card UI).
# ---------------------------------------------------------------

from pyrevit import revit, DB, script
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException
from System.Collections.Generic import List
import irontools_ui as ui

doc = revit.doc
uidoc = revit.uidoc
FT_TO_MM = 304.8
TITLE = "Set Height By Ref"


def fmt_mm(value):
    """Format a mm value without a useless .0"""
    return ("%.1f" % value).rstrip("0").rstrip(".")


# Only allow picking elements that are part of the current selection
class InSelectionFilter(ISelectionFilter):
    def __init__(self, allowed_ids):
        self.allowed = set(allowed_ids)

    def AllowElement(self, el):
        return el.Id.IntegerValue in self.allowed

    def AllowReference(self, ref, pt):
        return False


def get_level(el):
    """Return the level the element's height is measured from (or None)."""
    # MEP curves (duct / pipe / cable tray / conduit) have a ReferenceLevel
    ref_level = getattr(el, "ReferenceLevel", None)
    if ref_level is not None:
        return ref_level
    # Most other elements store the level in LevelId
    level_id = getattr(el, "LevelId", None)
    if level_id is not None and level_id != DB.ElementId.InvalidElementId:
        lvl = doc.GetElement(level_id)
        if isinstance(lvl, DB.Level):
            return lvl
    # Some families keep it in a parameter instead
    for bip in (DB.BuiltInParameter.FAMILY_LEVEL_PARAM,
                DB.BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM,
                DB.BuiltInParameter.SCHEDULE_LEVEL_PARAM):
        p = el.get_Parameter(bip)
        if p is not None and p.AsElementId() != DB.ElementId.InvalidElementId:
            lvl = doc.GetElement(p.AsElementId())
            if isinstance(lvl, DB.Level):
                return lvl
    return None


def get_z(el):
    """Return the element's reference Z in internal feet, and how it was measured."""
    loc = el.Location
    # Line-based elements: centerline midpoint (= Middle Elevation for pipes/ducts)
    if isinstance(loc, DB.LocationCurve):
        return loc.Curve.Evaluate(0.5, True).Z, "Centerline"
    # Point-based elements: the insertion point
    if isinstance(loc, DB.LocationPoint):
        return loc.Point.Z, "Insertion point"
    # Anything else: bottom of its bounding box
    bb = el.get_BoundingBox(None)
    if bb is not None:
        return bb.Min.Z, "Bounding box bottom"
    return None, None


# 1) Read the current selection
selected = [el for el in revit.get_selection().elements if el is not None]
if not selected:
    ui.show_result(TITLE, "warning", "Nothing is selected",
                   details=["Select the group of elements first, then click the button again."])
    script.exit()

selected_ids = [el.Id.IntegerValue for el in selected]

# 2) Pick the reference element (only from the selected group)
if len(selected) == 1:
    ref_el = selected[0]
else:
    try:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element,
            InSelectionFilter(selected_ids),
            "Pick the reference element from the selection (ESC to cancel)")
        ref_el = doc.GetElement(ref.ElementId)
    except OperationCanceledException:
        script.exit()

# Restore the original selection (PickObject can clear it)
uidoc.Selection.SetElementIds(List[DB.ElementId]([el.Id for el in selected]))

# 3) Measure the reference element's current height
ref_z, measured_at = get_z(ref_el)
if ref_z is None:
    ui.show_result(TITLE, "error", "Cannot read this element's height",
                   details=["Pick another element as the reference."])
    script.exit()

level = get_level(ref_el)
if level is not None:
    base_z = level.ProjectElevation
    level_name = level.Name
else:
    base_z = 0.0
    level_name = "Internal Origin (no level)"

current_mm = round((ref_z - base_z) * FT_TO_MM, 1)

# 4) Ask for the new height in mm
new_mm = ui.ask_number(
    TITLE,
    subtitle="Move the group so the reference sits at a new height",
    rows=[("Reference", u"{}  (Id {})".format(ref_el.Name, ref_el.Id.IntegerValue)),
          ("Level", level_name),
          ("Measured at", measured_at),
          ("Current height", fmt_mm(current_mm) + " mm"),
          ("Elements in group", str(len(selected)))],
    default=fmt_mm(current_mm),
    unit="mm",
    label="New height from level",
    hint="The whole group moves by (new height - current height).",
    icon="height",
    ok_text="Apply")

if new_mm is None:
    script.exit()  # user pressed Cancel / closed the dialog

# 5) Difference to apply to the whole group (feet)
delta_ft = (new_mm / FT_TO_MM + base_z) - ref_z
delta_mm = round(delta_ft * FT_TO_MM, 1)
if abs(delta_ft) < 1e-9:
    ui.show_result(TITLE, "warning", "Nothing to move",
                   details=["The new height equals the current height."])
    script.exit()

move_vector = DB.XYZ(0, 0, delta_ft)

# 6) Skip pinned elements (Revit refuses to move them)
pinned = [el for el in selected if getattr(el, "Pinned", False)]
movable = [el for el in selected if not getattr(el, "Pinned", False)]

moved_count = 0
failed = []

# 7) Move everything inside a single transaction
with revit.Transaction("Set Height By Ref ({} mm)".format(fmt_mm(delta_mm))):
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

# 8) Decide the status: green / amber / red
details = [u"Reference is now at {} mm from {}.".format(fmt_mm(new_mm), level_name)]
problems = False
if pinned:
    problems = True
    note = "{} pinned element(s) were skipped.".format(len(pinned))
    if ref_el.Id.IntegerValue in [el.Id.IntegerValue for el in pinned]:
        note += " The reference itself is pinned and did not move!"
    details.append(note)
if failed:
    problems = True
    details.append("{} element(s) could not be moved (listed in the pyRevit output window)."
                   .format(len(failed)))
    out = script.get_output()
    out.print_md("### Set Height By Ref - failed elements")
    for el, err in failed:
        out.print_md("- {} : {}".format(out.linkify(el.Id), err))

if moved_count == 0:
    status, headline = "error", "No elements were moved"
elif problems:
    status, headline = "warning", "Moved, with some elements skipped"
else:
    status, headline = "success", "Group moved successfully"

direction = "up" if delta_mm > 0 else "down"
sign = "+" if delta_mm > 0 else "-"
ui.show_result(
    TITLE, status, headline,
    stats=[(str(moved_count), "Elements moved"),
           (sign + fmt_mm(abs(delta_mm)), "mm " + direction),
           (fmt_mm(new_mm), "New height mm")],
    details=details)
