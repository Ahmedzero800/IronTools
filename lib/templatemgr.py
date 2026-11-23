# -*- coding: utf-8 -*-
"""View Template Manager core (no UI).

  load_templates(doc)            -> list of TemplateInfo (name, type, views using it)
  template_settings(doc, tpl)    -> the template's settings with name, group, value, controlled
  apply_pending(doc, names, nonc)-> rename + 'controlled by template' flags, one transaction
  duplicate(doc, ids) / delete(doc, ids)
  paste_settings(doc, src, targets, param_ids, copy_flags) -> report

Most settings are plain parameters and are copied by value. The ones that have no value
(V/G overrides, filters, view range...) are copied with their own Revit API calls below.
"""

import System
from System.Collections.Generic import List
from Autodesk.Revit.DB import (BuiltInParameter, CategoryType, CopyPasteOptions, ElementId,
                               ElementTransformUtils, FilteredElementCollector, FilteredWorksetCollector,
                               LabelUtils, StorageType, Transaction, Transform, View, ViewPlan,
                               WorksetKind)


def id_int(eid):
    try:
        return eid.Value
    except AttributeError:
        return eid.IntegerValue


def _eid(i):
    return ElementId(System.Int64(i)) if hasattr(ElementId, "Value") else ElementId(int(i))


def _asc_name(s):
    return s or u""


# ---------------------------------------------------------------
# Templates list
# ---------------------------------------------------------------
_TYPE_LABELS = {
    "FloorPlan": u"Floor Plan", "CeilingPlan": u"Ceiling Plan", "EngineeringPlan": u"Structural Plan",
    "AreaPlan": u"Area Plan", "ThreeD": u"3D View", "Section": u"Section", "Elevation": u"Elevation",
    "Detail": u"Detail", "DraftingView": u"Drafting", "Schedule": u"Schedule", "Walkthrough": u"Walkthrough",
    "Rendering": u"Rendering", "Legend": u"Legend",
}


class TemplateInfo(object):
    def __init__(self, view, uses):
        self.id = id_int(view.Id)
        self.view = view
        self.name = view.Name
        self.vtype = _TYPE_LABELS.get(str(view.ViewType), str(view.ViewType))
        self.uses = uses            # list of view ElementIds using this template


def load_templates(doc):
    views = list(FilteredElementCollector(doc).OfClass(View))
    usage = {}
    for v in views:
        if not v.IsTemplate:
            tid = v.ViewTemplateId
            if tid is not None and tid != ElementId.InvalidElementId:
                usage.setdefault(id_int(tid), []).append(v.Id)
    infos = [TemplateInfo(v, usage.get(id_int(v.Id), [])) for v in views if v.IsTemplate]
    infos.sort(key=lambda t: t.name.lower())
    return infos


# ---------------------------------------------------------------
# Settings of one template
# ---------------------------------------------------------------
def _bip(i):
    try:
        return System.Enum.ToObject(BuiltInParameter, int(i))
    except Exception:
        return None


def _bip_int(name):
    b = getattr(BuiltInParameter, name, None)
    return int(b) if b is not None else None


# settings with no plain value that we copy with dedicated API calls
SPECIAL = {}
for _name, _kind in (("VIS_GRAPHICS_MODEL", "cat_model"),
                     ("VIS_GRAPHICS_ANNOTATION", "cat_annotation"),
                     ("VIS_GRAPHICS_ANALYTICAL_MODEL", "cat_analytical"),
                     ("VIS_GRAPHICS_IMPORT", "cat_import"),
                     ("VIS_GRAPHICS_FILTERS", "filters"),
                     ("VIS_GRAPHICS_WORKSETS", "worksets"),
                     ("PLAN_VIEW_RANGE", "view_range"),
                     ("GRAPHIC_DISPLAY_OPTIONS_MODEL", "display_model")):
    _i = _bip_int(_name)
    if _i is not None:
        SPECIAL[_i] = _kind


def setting_name(doc, pid):
    if pid < 0:
        b = _bip(pid)
        if b is not None:
            try:
                return LabelUtils.GetLabelFor(b)
            except Exception:
                pass
        return u"Parameter {}".format(pid)
    el = doc.GetElement(_eid(pid))
    return el.Name if el is not None else u"Parameter {}".format(pid)


def _param_map(view):
    m = {}
    for p in view.Parameters:
        m[id_int(p.Id)] = p
    return m


def _value_text(doc, p):
    if p is None or p.StorageType == StorageType.None:
        return u""
    try:
        if p.StorageType == StorageType.ElementId:
            el = doc.GetElement(p.AsElementId())
            if el is not None:
                return el.Name
            return p.AsValueString() or u"<none>"
        s = p.AsValueString()
        if s is None or s == u"":
            s = p.AsString()
        return (s or u"").strip()
    except Exception:
        return u""


def can_copy(pid, p):
    """True if this setting's value can be copied by the tool."""
    if pid in SPECIAL:
        return True
    return p is not None and p.StorageType != StorageType.None and not p.IsReadOnly


class Setting(object):
    def __init__(self, pid, name, group, value, copyable):
        self.id = pid
        self.name = name
        self.group = group
        self.value = value
        self.copyable = copyable


def template_settings(doc, view):
    """{param id: Setting} for every setting the template can control."""
    pm = _param_map(view)
    out = {}
    for eid in view.GetTemplateParameterIds():
        pid = id_int(eid)
        p = pm.get(pid)
        group = u"Other"
        if p is not None:
            try:
                group = LabelUtils.GetLabelForGroup(p.Definition.GetGroupTypeId()) or u"Other"
            except Exception:
                pass
        if pid in SPECIAL or (p is not None and p.StorageType == StorageType.None):
            value = u""
        else:
            value = _value_text(doc, p)
        out[pid] = Setting(pid, setting_name(doc, pid), group, value, can_copy(pid, p))
    return out


def non_controlled(view):
    return set(id_int(i) for i in view.GetNonControlledTemplateParameterIds())


# ---------------------------------------------------------------
# Apply pending edits (rename + controlled flags)
# ---------------------------------------------------------------
def apply_pending(doc, names, nonc):
    """names: {tid: new name}; nonc: {tid: set of NON-controlled param ids}. Returns errors."""
    errors = []
    t = Transaction(doc, "Template Manager: apply changes")
    t.Start()
    try:
        for tid, new_name in names.items():
            v = doc.GetElement(_eid(tid))
            try:
                v.Name = new_name
            except Exception as ex:
                errors.append(u"Rename '{}' -> '{}': {}".format(v.Name, new_name, ex))
        for tid, ids in nonc.items():
            v = doc.GetElement(_eid(tid))
            try:
                v.SetNonControlledTemplateParameterIds(List[ElementId]([_eid(i) for i in ids]))
            except Exception as ex:
                errors.append(u"'{}': {}".format(v.Name, ex))
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    return errors


# ---------------------------------------------------------------
# Duplicate / delete
# ---------------------------------------------------------------
def duplicate(doc, tids):
    """Copy each template (Revit adds a number to the name). Returns new ids (ints)."""
    new_ids = []
    t = Transaction(doc, "Template Manager: duplicate")
    t.Start()
    try:
        for tid in tids:
            copied = ElementTransformUtils.CopyElements(doc, List[ElementId]([_eid(tid)]), doc,
                                                        Transform.Identity, CopyPasteOptions())
            new_ids.extend(id_int(i) for i in copied)
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    return new_ids


def delete(doc, tids):
    t = Transaction(doc, "Template Manager: delete")
    t.Start()
    try:
        doc.Delete(List[ElementId]([_eid(i) for i in tids]))
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise


# ---------------------------------------------------------------
# Paste settings
# ---------------------------------------------------------------
def _categories(doc, kind):
    """Categories (and sub-categories) for one V/G tab."""
    out = []
    for c in doc.Settings.Categories:
        cid = id_int(c.Id)
        is_import = cid > 0
        if kind == "cat_import":
            ok = is_import
        elif kind == "cat_model":
            ok = (not is_import) and c.CategoryType == CategoryType.Model
        elif kind == "cat_annotation":
            ok = c.CategoryType == CategoryType.Annotation
        else:
            ok = c.CategoryType == CategoryType.AnalyticalModel
        if not ok:
            continue
        out.append(c)
        try:
            for s in c.SubCategories:
                out.append(s)
        except Exception:
            pass
    return out


def _copy_categories(doc, src, dst, kind):
    n = 0
    for c in _categories(doc, kind):
        cid = c.Id
        try:
            if dst.CanCategoryBeHidden(cid):
                dst.SetCategoryHidden(cid, src.GetCategoryHidden(cid))
        except Exception:
            pass
        try:
            if dst.IsCategoryOverridable(cid):
                dst.SetCategoryOverrides(cid, src.GetCategoryOverrides(cid))
                n += 1
        except Exception:
            pass
    return n


def _copy_filters(src, dst):
    """Make the target's filters identical to the source (same order, overrides, on/off)."""
    for f in list(dst.GetFilters()):
        dst.RemoveFilter(f)
    order = list(src.GetOrderedFilters()) if hasattr(src, "GetOrderedFilters") else list(src.GetFilters())
    for f in order:
        dst.AddFilter(f)
        dst.SetFilterOverrides(f, src.GetFilterOverrides(f))
        dst.SetFilterVisibility(f, src.GetFilterVisibility(f))
        if hasattr(src, "GetIsFilterEnabled"):
            dst.SetIsFilterEnabled(f, src.GetIsFilterEnabled(f))
    return len(order)


def _copy_worksets(doc, src, dst):
    if not doc.IsWorkshared:
        return 0
    n = 0
    for ws in FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset):
        dst.SetWorksetVisibility(ws.Id, src.GetWorksetVisibility(ws.Id))
        n += 1
    return n


def _copy_special(doc, src, dst, kind):
    if kind.startswith("cat_"):
        _copy_categories(doc, src, dst, kind)
    elif kind == "filters":
        _copy_filters(src, dst)
    elif kind == "worksets":
        _copy_worksets(doc, src, dst)
    elif kind == "view_range":
        if not (isinstance(src, ViewPlan) and isinstance(dst, ViewPlan)):
            raise Exception("view range needs two plan templates")
        dst.SetViewRange(src.GetViewRange())
    elif kind == "display_model":
        if not hasattr(src, "GetViewDisplayModel"):
            raise Exception("not available in this Revit version")
        dst.SetViewDisplayModel(src.GetViewDisplayModel())


def _copy_value(sp, dp):
    st = sp.StorageType
    if st == StorageType.Integer:
        dp.Set(sp.AsInteger())
    elif st == StorageType.Double:
        dp.Set(sp.AsDouble())
    elif st == StorageType.String:
        dp.Set(sp.AsString() or u"")
    elif st == StorageType.ElementId:
        dp.Set(sp.AsElementId())


def paste_settings(doc, src_id, target_ids, param_ids, copy_flags):
    """Copy the chosen settings from one template to others, in one transaction.

    Returns {'targets': n, 'copied': n, 'not_in_target': n, 'failed': [(template, setting, error)]}"""
    src = doc.GetElement(_eid(src_id))
    src_pm = _param_map(src)
    src_nonc = non_controlled(src)
    report = {"targets": 0, "copied": 0, "not_in_target": 0, "failed": []}
    t = Transaction(doc, "Template Manager: paste settings")
    t.Start()
    try:
        for tid in target_ids:
            if tid == src_id:
                continue
            dst = doc.GetElement(_eid(tid))
            dst_ids = set(id_int(i) for i in dst.GetTemplateParameterIds())
            dst_pm = _param_map(dst)
            dst_nonc = non_controlled(dst)
            report["targets"] += 1
            for pid in param_ids:
                if pid not in dst_ids:
                    report["not_in_target"] += 1
                    continue
                name = setting_name(doc, pid)
                try:
                    if pid in SPECIAL:
                        _copy_special(doc, src, dst, SPECIAL[pid])
                    else:
                        sp, dp = src_pm.get(pid), dst_pm.get(pid)
                        if sp is None or dp is None or dp.IsReadOnly:
                            raise Exception("this setting cannot be written")
                        _copy_value(sp, dp)
                    report["copied"] += 1
                except Exception as ex:
                    report["failed"].append((dst.Name, name, str(ex)))
                    continue
                if copy_flags:
                    if pid in src_nonc:
                        dst_nonc.add(pid)
                    else:
                        dst_nonc.discard(pid)
            if copy_flags:
                dst.SetNonControlledTemplateParameterIds(List[ElementId]([_eid(i) for i in dst_nonc]))
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    return report
