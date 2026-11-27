# -*- coding: utf-8 -*-
"""Re-adjust Hangers: put hangers back on their pipes after the pipes were moved,
resized, re-levelled or split. Updates position, rotation, size and rod length."""

# ---------------------------------------------------------------
# Steps
# 1) Which hangers: the selected ones (or the ones on selected pipes), all of them,
#    or pick them now. Only hangers placed by Hanger Generator can be re-adjusted:
#    each one remembers its pipe.
# 2) For every hanger: find its pipe again (also after a split), slide the hanger onto
#    the pipe axis, turn it across the pipe, resize it to the pipe + insulation and run
#    the rod up to the nearest slab / beam again (host model and links).
# 3) Hangers whose pipe was deleted are left alone and selected at the end.
# 4) Everything is one Ctrl+Z.
# ---------------------------------------------------------------

from pyrevit import revit, DB, script, forms
import irontools_ui as ui
import hangergen as hg
import hangergen_ui as hui

TITLE = "Re-adjust Hangers"
doc = revit.doc
uidoc = revit.uidoc

if doc.IsFamilyDocument:
    ui.alert(TITLE, "Open a project, not a family.", "warning", "Not a project")
    script.exit()

everything = hg.all_hangers(doc)
if not everything:
    ui.alert(TITLE, "There are no hangers from Hanger Generator in this model yet.",
             "warning", "Nothing to re-adjust")
    script.exit()

selected = hui.hangers_from_selection(doc, uidoc)
cfg = script.get_config()
values = ui.ask_choices(
    TITLE, subtitle=u"{} hanger(s) from Hanger Generator in the model".format(len(everything)),
    groups=[hui.scope_group(selected, len(everything)),
            {"key": "hang", "label": u"Hang from",
             "options": [("both", u"Floors and beams"), ("floors", u"Floors / roofs only"),
                         ("beams", u"Beams only")],
             "value": cfg.get_option("hang", "both")}],
    fields=[{"key": "norod", "label": u"Rod if nothing is above", "unit": u"mm",
             "value": cfg.get_option("norod_mm", "1000"), "hint": u"Above the top of the pipe"}],
    note=u"Tip: select pipes you changed and run this to fix only their hangers.",
    icon=u"hanger", ok_text=u"Re-adjust", width=560)
if not values:
    script.exit()
cfg.hang = values["hang"]
cfg.norod_mm = "%g" % values["norod"]
script.save_config()

hangers = hui.hangers_for_scope(doc, uidoc, values["scope"], selected,
                                "Re-adjust: select hangers, then click Finish")
if not hangers:
    script.exit()

store = hg.load_profiles()
use_floors = values["hang"] in ("both", "floors")
use_beams = values["hang"] in ("both", "beams")

changed = same = 0
lost = []            # (hanger id, reason)
no_profile = []
placers = {}
# profiles first (reading a family's parameters needs no open transaction)
for h in hangers:
    fam = h.Symbol.Family
    if fam.Name not in placers:
        placers[fam.Name] = hui.profile_for_family(doc, store, fam)

group = DB.TransactionGroup(doc, TITLE)
group.Start()
try:
    with hui.RefView(doc) as ref_view:
        finder = hg.StructureFinder(ref_view, use_floors, use_beams, use_links=True)
        for name, pf in list(placers.items()):
            placers[name] = hg.Placer(doc, pf, None, finder, no_structure_rod=hg.mm(values["norod"])) \
                if pf else None
        t = DB.Transaction(doc, "Re-adjust hangers")
        t.Start()
        with forms.ProgressBar(title="Re-adjusting hangers... ({value} of {max_value})",
                               cancellable=True) as pb:
            for i, h in enumerate(hangers):
                if pb.cancelled:
                    break
                pb.update_progress(i + 1, len(hangers))
                placer = placers.get(h.Symbol.Family.Name)
                if placer is None:
                    no_profile.append(h.Id)
                    continue
                pipe, dist = hg.locate(doc, h)
                if pipe is None:
                    lost.append((h.Id, dist))
                    continue
                try:
                    if placer.adjust(h, pipe, dist):
                        changed += 1
                    else:
                        same += 1
                except Exception as ex:
                    placer.problems.append((h.Id, u"{}".format(ex)))
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
real = [p for p in placers.values() if p]
no_structure = sum(p.no_structure for p in real)
problems = [x for p in real for x in p.problems]
bad = set()
for p in real:
    bad |= p.bad_params

details = []
if lost:
    details.append(u"{} hanger(s) lost their pipe (the pipe or that piece of it was deleted). "
                   u"They are selected now.".format(len(lost)))
    hui.select_ids(uidoc, [i for i, _ in lost])
if no_structure:
    details.append(u"{} hanger(s) found nothing above: rod set to {:g} mm above the pipe."
                   .format(no_structure, values["norod"]))
if no_profile:
    details.append(u"{} hanger(s) use a family the tool cannot drive. Add it in Hanger Generator."
                   .format(len(no_profile)))
if bad:
    details.append(u"Could not set: {}.".format(u", ".join(sorted(bad))))
if problems:
    details.append(u"{} hanger(s) failed (listed in the pyRevit output window).".format(len(problems)))
    out = script.get_output()
    out.print_md("### Re-adjust Hangers - problems")
    for eid, msg in problems:
        out.print_md(u"- {} : {}".format(out.linkify(eid), msg))

if changed + same == 0:
    status, headline = "error", "No hangers were re-adjusted"
elif details:
    status, headline = "warning", "Re-adjusted, with some notes"
elif changed == 0:
    status, headline = "success", "All hangers were already right"
else:
    status, headline = "success", "Hangers are back on their pipes"

ui.show_result(TITLE, status, headline,
               stats=[(str(changed), "Updated"), (str(same), "Already right"), (str(len(lost)), "Lost pipe")],
               details=details)
