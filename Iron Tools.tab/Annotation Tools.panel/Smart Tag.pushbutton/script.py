# -*- coding: utf-8 -*-
"""Smart Tag: tag the selected elements in one neat, aligned column.
Pick the tag type per category and the leader angle (90 / 45), then click where the tags go."""

# ---------------------------------------------------------------
# Steps
# 1) Use the current selection (or ask you to pick elements).
# 2) Dialog: leader angle, side, a tag type for every category found, gap between tags.
# 3) The real size of every tag is measured (temporary tags, rolled back).
# 4) You move the mouse: a live preview shows the tags and leaders; click to place.
#    Click right / left of the elements -> tags on that side (or force it in the dialog).
#    Click above -> the column grows up, below -> it grows down.
# 5) The order of the tags is searched so leaders do not cross, then all tags are
#    created in one transaction (one Ctrl+Z undoes everything).
# ---------------------------------------------------------------

from pyrevit import revit, DB, script
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException
import irontools_ui as ui
import smarttag as st

TITLE = "Smart Tag"
doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView

# 0) The view must accept tags
problem = st.view_problem(view)
if problem:
    ui.alert(TITLE, problem, "warning", "This view cannot take tags")
    script.exit()


class TaggableFilter(ISelectionFilter):
    def AllowElement(self, element):
        return st.is_taggable(element)

    def AllowReference(self, reference, point):
        return False


# 1) Elements to tag
elements = [el for el in revit.get_selection().elements if st.is_taggable(el)]
if not elements:
    try:
        refs = uidoc.Selection.PickObjects(ObjectType.Element, TaggableFilter(),
                                           "Smart Tag: select the elements to tag, then click Finish")
        elements = [doc.GetElement(r) for r in refs]
    except OperationCanceledException:
        script.exit()
if not elements:
    ui.alert(TITLE, "Nothing that can be tagged was selected.", "warning", "Nothing to tag")
    script.exit()

# 2) Group by category and find the tag types for each one
by_cat = {}
for el in elements:
    by_cat.setdefault(st._id_int(el.Category.Id), []).append(el)

cfg = script.get_config()
last_angle = int(cfg.get_option("angle", 0))
last_gap = cfg.get_option("gap_mm", "0.5")
last_side = cfg.get_option("side", "auto")
last_preview = cfg.get_option("preview", "on")
groups = [{"key": "angle", "label": "Leader angle",
           "options": [(0, u"Free   best angle per tag"), (90, u"90°   straight"),
                       (45, u"45°   diagonal")], "value": last_angle},
          {"key": "side", "label": "Tags side",
           "options": [("auto", u"Auto (from your click)"), ("right", u"Right"), ("left", u"Left")],
           "value": last_side},
          {"key": "preview", "label": "Live preview while picking",
           "options": [("on", u"On"), ("off", u"Off")], "value": last_preview}]
no_tags = []
cat_names = {}
for cat_int, els in sorted(by_cat.items(), key=lambda kv: kv[1][0].Category.Name):
    name = els[0].Category.Name
    cat_names[cat_int] = name
    options = st.tag_types_for(doc, els[0].Category.Id)
    if not options:
        no_tags.append(name)
        continue
    remembered = cfg.get_option("tag_" + str(cat_int), "")
    value = None
    for tid, label in options:
        if label == remembered:
            value = tid
    groups.append({"key": "cat_%d" % cat_int,
                   "label": u"{}  ({})".format(name, len(els)),
                   "options": options, "value": value})

if len(groups) == 3:
    ui.alert(TITLE, "No tag families are loaded for: " + ", ".join(no_tags) +
             ". Load a tag family and try again.", "error", "No tag types found")
    script.exit()

note = u"Next you click where the tag column goes. Click right/left of the elements to choose the side."
if no_tags:
    note += u"  No tag loaded for: " + u", ".join(no_tags) + u" (skipped)."

values = ui.ask_choices(
    TITLE, subtitle=u"{} elements selected".format(len(elements)),
    groups=groups,
    fields=[{"key": "gap", "label": "Gap between tags", "value": last_gap, "unit": "mm",
             "hint": "Paper millimetres"}],
    note=note, icon="tag", ok_text="Place tags")
if not values:
    script.exit()

# Remember the choices for next time
angle = int(values["angle"] or 0)          # 0 = Free
side_mode = values.get("side") or "auto"
use_preview = (values.get("preview") or "on") == "on"
cfg.angle = angle
cfg.side = side_mode
cfg.preview = "on" if use_preview else "off"
cfg.gap_mm = "%g" % values["gap"]
tag_type_by_cat = {}
for cat_int in by_cat:
    tid = values.get("cat_%d" % cat_int)
    if tid is not None:
        tag_type_by_cat[cat_int] = tid
        sym = doc.GetElement(tid)
        if sym is not None:
            setattr(cfg, "tag_" + str(cat_int), st._symbol_label(sym))
script.save_config()

# 3) Make sure the view has a work plane (needed to pick a point in sections / 3D)
if view.SketchPlane is None:
    with revit.Transaction("Smart Tag work plane"):
        plane = DB.Plane.CreateByNormalAndOrigin(view.ViewDirection, view.Origin)
        view.SketchPlane = DB.SketchPlane.Create(doc, plane)

# 4) Measure the real size of every tag (temporary tags, rolled back right away)
frame = st.ViewFrame(view)
paper = float(view.Scale or 100) / st.MM_PER_FT     # 1 paper mm in model feet
gap = float(values["gap"]) * paper
shoulder = (2.0 if angle == 0 else 3.0) * paper
items, skipped = st.collect(view, elements, tag_type_by_cat)
items, failed = st.measure(doc, view, items)
if not items:
    ui.alert(TITLE, "None of the selected elements could be tagged in this view.", "error",
             "No tags were placed")
    script.exit()


def layout_at(uv, optimize):
    return st.compute_layout(items, uv, angle=angle, side_mode=side_mode, gap=gap,
                             shoulder=shoulder, optimize=optimize)


# 5) Pick the point with a live preview that follows the mouse
import smarttag_preview
preview = smarttag_preview.LivePreview(uidoc, view, frame, layout_at,
                                       optimize_on_pause=len(items) <= 8)
if use_preview:
    try:
        preview.start()
    except Exception:
        pass
try:
    point = uidoc.Selection.PickPoint("Smart Tag: move the mouse to see the tags, click to place them")
except OperationCanceledException:
    preview.stop()
    script.exit()
finally:
    preview.stop()

# 6) Final layout (full search for the order with no crossing leaders) and create the tags
layout = layout_at(frame.to2d(point), True)
created_ids, failed2 = st.apply_layout(doc, view, layout, depth=frame.depth(point))
failed = failed + failed2

created = len(created_ids)
details = []
if layout["crossings"]:
    details.append("{} leader crossing(s) could not be avoided with this selection. "
                   "Try the other angle or side.".format(layout["crossings"]))
if skipped:
    details.append("{} element(s) skipped (no tag type for their category).".format(len(skipped)))
if failed:
    details.append("{} element(s) could not be tagged (listed in the pyRevit output window)."
                   .format(len(failed)))
    out = script.get_output()
    out.print_md("### Smart Tag - problems")
    for el, err in failed:
        out.print_md("- {} : {}".format(out.linkify(el.Id), err))

if created == 0:
    status, headline = "error", "No tags were placed"
elif details:
    status, headline = "warning", "Tags placed, with some notes"
else:
    status, headline = "success", "All tags placed and aligned"

ui.show_result(TITLE, status, headline,
               stats=[(str(created), "Tags placed"),
                      (u"Free" if angle == 0 else u"{}°".format(angle), "Leader angle"),
                      (str(layout["crossings"]), "Crossings")],
               details=details)
