# -*- coding: utf-8 -*-
"""Replace Support: swap supports for another support family in one go. The new supports
sit on the same pipes at the same spots, sized and hung from the structure again."""

# ---------------------------------------------------------------
# Steps
# 1) Which supports: the selected ones (or the ones on selected pipes), all of them,
#    or pick them now. Only supports placed by Support Generator can be replaced.
# 2) Choose the new family (or "+ Add family..." for one of your own).
# 3) Every support is rebuilt with the new family at the same spot on its pipe: size,
#    rotation and rod length are worked out again. Comments, Mark and workset are kept.
# 4) Everything is one Ctrl+Z.
# ---------------------------------------------------------------

from pyrevit import revit, DB, script, forms
import irontools_ui as ui
import hangergen as hg
import hangergen_ui as hui

TITLE = "Replace Support"
doc = revit.doc
uidoc = revit.uidoc

if doc.IsFamilyDocument:
    ui.alert(TITLE, "Open a project, not a family.", "warning", "Not a project")
    script.exit()

everything = hg.all_hangers(doc)
if not everything:
    ui.alert(TITLE, "There are no supports from Support Generator in this model yet.",
             "warning", "Nothing to replace")
    script.exit()

selected = hui.hangers_from_selection(doc, uidoc)
store = hg.load_profiles()
cfg = script.get_config()
in_use = sorted(set(h.Symbol.Family.Name for h in (selected or everything)))
values = ui.ask_choices(
    TITLE, subtitle=u"Now in use: " + u", ".join(in_use),
    groups=[hui.scope_group(selected, len(everything)),
            {"key": "family", "label": u"Replace with", "options": hui.family_options(store, doc),
             "value": cfg.get_option("family", u"") or store.get("last")},
            {"key": "edit", "label": u"Family settings",
             "options": [("no", u"Use saved settings"), ("yes", u"Check / edit the parameters first")],
             "value": "no"},
            {"key": "hang", "label": u"Hang from (underside of concrete only)",
             "options": [("both", u"Concrete slabs + beams"), ("floors", u"Concrete slabs only"),
                         ("beams", u"Concrete beams only")],
             "value": cfg.get_option("hang", "both")}],
    fields=[{"key": "norod", "label": u"Rod if nothing is above", "unit": u"mm",
             "value": cfg.get_option("norod_mm", "1000"), "hint": u"Above the top of the pipe"}],
    note=u"Each support is rebuilt at the same spot on its pipe. Comments, Mark and workset are kept.",
    icon=u"hanger", ok_text=u"Replace", width=760, columns=2, max_height=560)
if not values:
    script.exit()

profile, symbol = hui.resolve_family(doc, store, values["family"], edit=values["edit"] == "yes")
if profile is None:
    script.exit()
cfg.family = profile["family"]
cfg.hang = values["hang"]
cfg.norod_mm = "%g" % values["norod"]
script.save_config()

hangers = hui.hangers_for_scope(doc, uidoc, values["scope"], selected,
                                "Replace: select supports, then click Finish")
if not hangers:
    script.exit()

todo = [h for h in hangers if h.Symbol.Family.Name != profile["family"]]
already = len(hangers) - len(todo)
if not todo:
    ui.alert(TITLE, u"All chosen supports already use {}.".format(profile["family"]),
             "success", "Nothing to replace")
    script.exit()

use_floors = values["hang"] in ("both", "floors")
use_beams = values["hang"] in ("both", "beams")
new_ids = []
lost = []
group = DB.TransactionGroup(doc, TITLE)
group.Start()
placer = None
try:
    if not symbol.IsActive:
        with revit.Transaction("Activate support type"):
            symbol.Activate()
    with hui.RefView(doc) as ref_view:
        finder = hg.StructureFinder(ref_view, use_floors, use_beams, use_links=True)
        placer = hg.Placer(doc, profile, symbol, finder, no_structure_rod=hg.mm(values["norod"]))
        t = DB.Transaction(doc, "Replace supports")
        t.Start()
        with forms.ProgressBar(title="Replacing supports... ({value} of {max_value})",
                               cancellable=True) as pb:
            for i, h in enumerate(todo):
                if pb.cancelled:
                    break
                pb.update_progress(i + 1, len(todo))
                pipe, dist = hg.locate(doc, h)
                if pipe is None:
                    lost.append((h.Id, dist))
                    continue
                inst = placer.replace(h, pipe, dist)
                if inst is not None:
                    new_ids.append(inst.Id)
        t.Commit()
    group.Assimilate()
except Exception as ex:
    if group.HasStarted() and not group.HasEnded():
        group.RollBack()
    ui.alert(TITLE, u"{}".format(ex), "error", "Nothing was changed")
    script.exit()

# ---------------------------------------------------------------
# Result
# ---------------------------------------------------------------
details = []
clashing, notes = hui.clash_details(doc, new_ids, TITLE) if new_ids else ([], [])
details += notes
if already:
    details.append(u"{} support(s) already used this family.".format(already))
if lost:
    details.append(u"{} support(s) were left as they are: their pipe was deleted or is too far away. "
                   u"They are selected now.".format(len(lost)))
if placer.no_structure:
    details.append(u"{} support(s) found no concrete / floor within 6 m: set to {:g} mm from the pipe."
                   .format(placer.no_structure, values["norod"]))
if placer.bad_params:
    details.append(u"Could not set: {}. Check the family settings.".format(
        u", ".join(sorted(placer.bad_params))))
if placer.problems:
    details.append(u"{} support(s) failed (listed in the pyRevit output window).".format(
        len(placer.problems)))
    out = script.get_output()
    out.print_md("### Replace Support - problems")
    for eid, msg in placer.problems:
        out.print_md(u"- {} : {}".format(out.linkify(eid), msg))

hui.select_ids(uidoc, ([i for i, _ in lost] + clashing) or new_ids)

if not new_ids:
    status, headline = "error", "No supports were replaced"
elif lost or clashing or placer.problems or placer.bad_params:
    status, headline = "warning", "Replaced, with some notes"
else:
    status, headline = "success", "Supports replaced, no clashes"

ui.show_result(TITLE, status, headline,
               stats=[(str(len(new_ids)), "Replaced"), (str(len(lost)), "Left as is"),
                      (str(len(clashing)), "Clashes"), (hui.family_label(profile)[:20], "New family")],
               details=details)
