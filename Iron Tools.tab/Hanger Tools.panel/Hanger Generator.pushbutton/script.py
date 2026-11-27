# -*- coding: utf-8 -*-
"""Hanger Generator: place pipe hangers along the selected pipes (or where you click),
sized to the pipe and its insulation, with the rod reaching the slab / beam above."""

# ---------------------------------------------------------------
# Steps
# 1) Dialog: mode (along the pipes / click points), hanger family, what to hang from.
#    "+ Add family..." lets you use any hanger family of your own; the tool guesses
#    which parameters to drive and lets you correct them (saved for next time).
# 2) Along the pipes: hangers at distance A from each pipe end, evenly spread in between,
#    never more than B apart.
#    Click points: every click drops an orange marker, Esc places all hangers at once.
# 3) For every hanger a ray goes straight up from the top of the pipe and finds the
#    nearest floor / roof / beam, also in linked models. The rod goes to it.
# 4) Everything is one Ctrl+Z.
# ---------------------------------------------------------------

from pyrevit import revit, DB, script, forms
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException
import irontools_ui as ui
import hangergen as hg
import hangergen_ui as hui

TITLE = "Hanger Generator"
doc = revit.doc
uidoc = revit.uidoc

if doc.IsFamilyDocument:
    ui.alert(TITLE, "Open a project, not a family.", "warning", "Not a project")
    script.exit()


class PipeFilter(ISelectionFilter):
    def AllowElement(self, element):
        return hg.is_pipe(element)

    def AllowReference(self, reference, point):
        return True


# ---------------------------------------------------------------
# 1) Main dialog
# ---------------------------------------------------------------
store = hg.load_profiles()
profiles = store.get("profiles", [])
cfg = script.get_config()

selected = [el for el in revit.get_selection().elements if hg.is_pipe(el)]

groups = [
    {"key": "mode", "label": u"How to place",
     "options": [("auto", u"Along the pipes (A and B below)"), ("manual", u"Click points on pipes")],
     "value": "auto" if selected else cfg.get_option("mode", "auto")},
    {"key": "family", "label": u"Hanger family", "options": hui.family_options(store),
     "value": store.get("last") or (profiles[0]["family"] if profiles else hui.ADD)},
    {"key": "edit", "label": u"Family settings",
     "options": [("no", u"Use saved settings"), ("yes", u"Check / edit the parameters first")],
     "value": "no"},
    {"key": "hang", "label": u"Hang from",
     "options": [("both", u"Floors and beams"), ("floors", u"Floors / roofs only"),
                 ("beams", u"Beams only")],
     "value": cfg.get_option("hang", "both")},
    {"key": "skip", "label": u"Pipes that already have hangers from this tool",
     "options": [("yes", u"Skip spots already done"), ("no", u"Place anyway")],
     "value": cfg.get_option("skip", "yes")},
]
fields = [
    {"key": "edge", "label": u"Distance from pipe ends (A)", "unit": u"mm",
     "value": cfg.get_option("edge_mm", "300")},
    {"key": "step", "label": u"Max spacing between hangers (B)", "unit": u"mm",
     "value": cfg.get_option("step_mm", "2000"), "hint": u"Evenly spread, never more than B"},
    {"key": "norod", "label": u"Rod if nothing is above", "unit": u"mm",
     "value": cfg.get_option("norod_mm", "1000"), "hint": u"Above the top of the pipe"},
]
subtitle = (u"{} pipe(s) selected".format(len(selected)) if selected
            else u"No pipes selected: you pick them next")
values = ui.ask_choices(TITLE, subtitle=subtitle, groups=groups, fields=fields,
                        note=u"Insulated pipes are clamped around the insulation. The rod goes to the "
                             u"structure in this model or any loaded link. Vertical pipes are skipped. "
                             u"Click points: mark all the spots first, then press Esc to place them.",
                        icon=u"hanger", ok_text=u"Place hangers", width=760, columns=2,
                        max_height=560)
if not values:
    script.exit()

for key in ("mode", "hang", "skip"):
    setattr(cfg, key, values[key])
cfg.edge_mm = "%g" % values["edge"]
cfg.step_mm = "%g" % values["step"]
cfg.norod_mm = "%g" % values["norod"]
script.save_config()

if values["edge"] < 0 or values["step"] < 100 or values["norod"] < 0:
    ui.alert(TITLE, "Distance from ends must be 0 or more and max spacing at least 100 mm.",
             "warning", "Check the numbers")
    script.exit()

# ---------------------------------------------------------------
# 2) Family + parameter mapping
# ---------------------------------------------------------------
profile, symbol = hui.resolve_family(doc, store, values["family"], edit=values["edit"] == "yes")
if profile is None:
    script.exit()

# ---------------------------------------------------------------
# 3) Pipes (along-the-pipes mode)
# ---------------------------------------------------------------
mode = values["mode"]
pipes = selected
if mode == "auto" and not pipes:
    try:
        refs = uidoc.Selection.PickObjects(ObjectType.Element, PipeFilter(),
                                           "Hanger Generator: select pipes, then click Finish")
        pipes = [doc.GetElement(r) for r in refs]
    except OperationCanceledException:
        script.exit()
    if not pipes:
        script.exit()

# ---------------------------------------------------------------
# 4) Place
# ---------------------------------------------------------------
edge = hg.mm(values["edge"])
step = hg.mm(values["step"])
use_floors = values["hang"] in ("both", "floors")
use_beams = values["hang"] in ("both", "beams")
existing = hg.existing_stations(doc) if values["skip"] == "yes" else {}

created = []
vertical = short = 0
group = DB.TransactionGroup(doc, TITLE)
group.Start()
placer = None
try:
    if not symbol.IsActive:
        with revit.Transaction("Activate hanger type"):
            symbol.Activate()
    with hui.RefView(doc) as ref_view:
        finder = hg.StructureFinder(ref_view, use_floors, use_beams, use_links=True)
        placer = hg.Placer(doc, profile, symbol, finder, around_insulation=True,
                           no_structure_rod=hg.mm(values["norod"]))

        if mode == "auto":
            plan, vertical, short = hg.plan_auto(pipes, edge, step, skip_existing=existing)
            t = DB.Transaction(doc, "Place hangers")
            t.Start()
            if len(plan) > 40:
                with forms.ProgressBar(title="Placing hangers... ({value} of {max_value})",
                                       cancellable=True) as pb:
                    for i, (pipe, dist) in enumerate(plan):
                        if pb.cancelled:
                            break
                        inst = placer.place(pipe, dist)
                        if inst:
                            created.append(inst.Id)
                        pb.update_progress(i + 1, len(plan))
            else:
                for pipe, dist in plan:
                    inst = placer.place(pipe, dist)
                    if inst:
                        created.append(inst.Id)
            t.Commit()
        else:
            # Click mode: every click drops an orange marker (click it again to remove it).
            # Esc = done: the markers are removed and all hangers are placed at once.
            markers = hg.Markers(doc, uidoc.ActiveView)
            while True:
                prompt = (u"Hanger Generator: click on pipes to mark hanger spots ({} marked). "
                          u"Click a mark again to remove it. Esc = place them".format(len(markers.items)))
                try:
                    ref = uidoc.Selection.PickObject(ObjectType.PointOnElement, PipeFilter(), prompt)
                except OperationCanceledException:
                    break
                pipe = doc.GetElement(ref)
                line = hg.pipe_line(pipe) if hg.is_pipe(pipe) else None
                if line is None:
                    continue
                if hg.is_vertical(line):
                    vertical += 1
                    continue
                res = line.Project(ref.GlobalPoint)
                if res is None:
                    continue
                dist = (res.XYZPoint - line.GetEndPoint(0)).GetLength()
                dist = min(max(dist, 0.0), line.Length)
                t = DB.Transaction(doc, "Mark hanger spot")
                t.Start()
                markers.toggle(pipe, dist)
                t.Commit()

            t = DB.Transaction(doc, "Place hangers")
            t.Start()
            markers.clear()
            for pipe, dist in markers.spots():
                inst = placer.place(pipe, dist)
                if inst:
                    created.append(inst.Id)
            t.Commit()
    group.Assimilate()
except Exception as ex:
    if group.HasStarted() and not group.HasEnded():
        group.RollBack()
    ui.alert(TITLE, u"{}".format(ex), "error", "Nothing was placed")
    script.exit()

# ---------------------------------------------------------------
# 5) Result
# ---------------------------------------------------------------
if created:
    try:
        from System.Collections.Generic import List
        uidoc.Selection.SetElementIds(List[DB.ElementId](created))
    except Exception:
        pass

details = []
if placer.no_structure:
    details.append(u"{} hanger(s) found nothing above within 6 m: rod set to {:g} mm above the pipe."
                   .format(placer.no_structure, values["norod"]))
if vertical:
    details.append(u"{} vertical pipe(s) skipped.".format(vertical))
if short:
    details.append(u"{} very short pipe(s) skipped.".format(short))
if placer.bad_params:
    details.append(u"Could not set: {}. Check the family settings.".format(
        u", ".join(sorted(placer.bad_params))))
if placer.problems:
    details.append(u"{} hanger(s) failed (listed in the pyRevit output window).".format(
        len(placer.problems)))
    out = script.get_output()
    out.print_md("### Hanger Generator - problems")
    for pid, msg in placer.problems:
        out.print_md(u"- {} : {}".format(out.linkify(pid), msg))

if not created:
    status, headline = "error" if mode == "auto" else "warning", "No hangers were placed"
elif details:
    status, headline = "warning", "Hangers placed, with some notes"
else:
    status, headline = "success", "Hangers placed and hung from the structure"

ui.show_result(TITLE, status, headline,
               stats=[(str(len(created)), "Hangers placed"),
                      (str(max(0, len(created) - placer.no_structure)), "Hung from structure"),
                      (profile["family"][:18], "Family")],
               details=details)
