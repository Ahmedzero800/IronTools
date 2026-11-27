# -*- coding: utf-8 -*-
"""Shared dialogs for the Support Tools buttons: choose / add a support family and check
which family parameters the tool drives."""

import os
from pyrevit import revit, forms
import irontools_ui as ui
import hangergen as hg

TITLE = u"Support Tools"
ADD = u"__add__"


def edit_profile(profile, params):
    """Let the user check / correct the parameter mapping. Returns the new profile or None."""
    none = [(u"", u"(none)")]
    opts = [(n, n) for n in params]
    groups = [
        {"key": "p_size", "label": u"Pipe size goes into", "options": opts,
         "value": profile.get("p_size")},
        {"key": "size_is", "label": u"That parameter is the clamp",
         "options": [("diameter", u"Diameter"), ("radius", u"Radius")],
         "value": profile.get("size_is", "diameter")},
        {"key": "p_top", "label": u"Rod top / structure height goes into", "options": none + opts,
         "value": profile.get("p_top", u"")},
        {"key": "origin", "label": u"Family origin (insertion point) sits",
         "options": [("level", u"On the level, under the pipe"),
                     ("bottom", u"Under the pipe (bottom of pipe / insulation)"),
                     ("center", u"On the pipe axis")],
         "value": profile.get("origin", "level")},
        {"key": "p_center", "label": u"Pipe centre height goes into", "options": none + opts,
         "value": profile.get("p_center", u"")},
        {"key": "p_ins", "label": u"Insulation thickness goes into", "options": none + opts,
         "value": profile.get("p_ins", u"")},
        {"key": "rotate", "label": u"Turn the family so the clamp is across the pipe",
         "options": [(0, u"0°"), (90, u"90°")], "value": int(profile.get("rotate", 0) or 0)},
    ]
    values = ui.ask_choices(
        u"Support family settings", subtitle=profile["family"], groups=groups,
        fields=[{"key": "top_extra_mm", "label": u"Extra rod length", "unit": u"mm",
                 "value": u"%g" % float(profile.get("top_extra_mm", 0) or 0),
                 "hint": u"Added above the slab / beam, e.g. an anchor"}],
        note=u"Heights are measured from the family origin. Only length instance parameters "
             u"without a formula are listed.",
        icon=u"settings", ok_text=u"Save", width=760, columns=2, max_height=720)
    if not values:
        return None
    if not values.get("p_size"):
        ui.alert(TITLE, "Choose the parameter that takes the pipe size.", "warning")
        return None
    new = dict(profile)
    for key in ("p_size", "size_is", "p_top", "p_center", "p_ins", "origin", "rotate", "top_extra_mm"):
        new[key] = values.get(key)
    return new


def ensure_family(doc, profile):
    """Family in the project (loads it from its path if needed). Returns the Family or None."""
    fam = hg.find_family(doc, profile["family"])
    if fam is not None:
        return fam
    path = profile.get("path") or u""
    if not os.path.isfile(path):
        return None
    with revit.Transaction("Load support family"):
        fam = hg.load_family(doc, path)
    return fam


def add_family(doc, store):
    """Pick an .rfa, load it, check its parameters. Returns the saved profile or None."""
    path = forms.pick_file(file_ext="rfa", title="Choose a support family (.rfa)")
    if not path:
        return None
    name = os.path.splitext(os.path.basename(path))[0]
    fam = hg.find_family(doc, name)
    if fam is None:
        with revit.Transaction("Load support family"):
            fam = hg.load_family(doc, path)
    if fam is None:
        ui.alert(TITLE, "This family could not be loaded.", "error", "Family not loaded")
        return None
    params = hg.family_length_params(doc, fam)
    if not params:
        ui.alert(TITLE, "This family has no length instance parameters the tool can drive.",
                 "error", "Family not usable")
        return None
    profile = edit_profile(hg.guess_profile(fam.Name, path, params), params)
    if profile:
        hg.upsert_profile(store, profile)
    return profile


def profile_for_family(doc, store, family):
    """Saved profile of a family in the project, or a guessed one (saved for next time)."""
    for p in store.get("profiles", []):
        if p.get("family") == family.Name:
            return p
    params = hg.family_length_params(doc, family)
    profile = hg.guess_profile(family.Name, u"", params)
    if not profile.get("p_size"):
        return None
    store.setdefault("profiles", []).append(profile)
    hg.save_profiles(store)
    return profile


def family_options(store):
    return [(p["family"], p["family"]) for p in store.get("profiles", [])] + [(ADD, u"+ Add family...")]


def resolve_family(doc, store, choice, edit=False):
    """Turn a dialog choice (family name or ADD) into (profile, symbol). Shows its own errors."""
    if choice == ADD:
        profile = add_family(doc, store)
        if not profile:
            return None, None
    else:
        matches = [p for p in store.get("profiles", []) if p["family"] == choice]
        if not matches:
            return None, None
        profile = matches[0]
    family = ensure_family(doc, profile)
    if family is None:
        ui.alert(TITLE, u"'{}' is not in this project and its file was not found:\n{}\n\n"
                 u"Use '+ Add family...' to choose the .rfa again.".format(
                     profile["family"], profile.get("path") or u"(no path)"),
                 "error", "Support family missing")
        return None, None
    if edit and choice != ADD:
        new = edit_profile(profile, hg.family_length_params(doc, family))
        if not new:
            return None, None
        profile = new
        hg.upsert_profile(store, profile)
    else:
        store["last"] = profile["family"]
        hg.save_profiles(store)
    symbol = hg.family_symbol(doc, family, profile.get("type") or u"")
    if symbol is None:
        ui.alert(TITLE, "The support family has no type.", "error")
        return None, None
    return profile, symbol


def hangers_from_selection(doc, uidoc):
    """Supports placed by the tool that are selected, or that sit on selected pipes."""
    picked = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    picked = [el for el in picked if el is not None]
    out = {}
    for el in picked:
        if hg.read_stamp(el):
            out[el.Id.IntegerValue] = el
    pipe_uids = set(el.UniqueId for el in picked if hg.is_pipe(el))
    if pipe_uids:
        for h in hg.all_hangers(doc):
            data = hg.read_stamp(h) or {}
            if data.get("pipe") in pipe_uids:
                out[h.Id.IntegerValue] = h
    return list(out.values())


def pick_hangers(doc, uidoc, prompt):
    """Let the user pick supports (only ones placed by the tool can be picked)."""
    from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
    from Autodesk.Revit.Exceptions import OperationCanceledException

    class _F(ISelectionFilter):
        def AllowElement(self, element):
            return hg.read_stamp(element) is not None

        def AllowReference(self, reference, point):
            return False
    try:
        refs = uidoc.Selection.PickObjects(ObjectType.Element, _F(), prompt)
    except OperationCanceledException:
        return None
    return [doc.GetElement(r) for r in refs]


def scope_group(selected, total):
    """Chip group: which supports to work on."""
    options = []
    if selected:
        options.append(("sel", u"Selected ({})".format(len(selected))))
    options.append(("all", u"All in the model ({})".format(total)))
    options.append(("pick", u"Pick them now"))
    return {"key": "scope", "label": u"Which supports", "options": options,
            "value": "sel" if selected else "pick"}


def hangers_for_scope(doc, uidoc, scope, selected, prompt):
    if scope == "sel":
        return selected
    if scope == "all":
        return hg.all_hangers(doc)
    return pick_hangers(doc, uidoc, prompt)


def select_ids(uidoc, ids):
    try:
        from System.Collections.Generic import List
        from pyrevit import DB
        uidoc.Selection.SetElementIds(List[DB.ElementId](list(ids)))
    except Exception:
        pass


class RefView(object):
    """Context: a temporary 3D view used to find the structure (deleted on exit).
    Use inside a TransactionGroup."""

    def __init__(self, doc):
        self.doc = doc
        self.view = None

    def __enter__(self):
        from pyrevit import DB
        t = DB.Transaction(self.doc, "Support reference view")
        t.Start()
        self.view = hg.make_ref_view(self.doc)
        t.Commit()
        return self.view

    def __exit__(self, *args):
        from pyrevit import DB
        if self.view is None:
            return False
        t = DB.Transaction(self.doc, "Remove support reference view")
        t.Start()
        try:
            self.doc.Delete(self.view.Id)
        except Exception:
            pass
        t.Commit()
        return False
