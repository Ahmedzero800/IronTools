# -*- coding: utf-8 -*-
"""Support Generator engine (no UI): place pipe supports along pipes, sized to the pipe
(and its insulation) and with the rod reaching the nearest slab / beam above.

Works with any support family through a "profile" that tells the tool which family
parameters to drive. See PROFILE FIELDS below.

All lengths inside this module are Revit internal feet unless the name ends in _mm.
"""

import os
import io
import json
import math

import clr
from System import Guid
from System.Collections.Generic import List
from Autodesk.Revit import DB
from Autodesk.Revit.DB.ExtensibleStorage import (Schema, SchemaBuilder, Entity, AccessLevel)

MM_PER_FT = 304.8


def mm(value_mm):
    return float(value_mm) / MM_PER_FT


def to_mm(value_ft):
    return float(value_ft) * MM_PER_FT


# ------------------------------------------------------------------
# PROFILE FIELDS (one profile per support family, saved as JSON)
#   family   : family name in the project
#   path     : .rfa file, used to load the family when it is not in the project
#   type     : type name to use ("" = first type)
#   origin   : where the family origin goes
#              "level"  -> on the pipe's level, under the pipe (geometry raised by p_center)
#              "bottom" -> under the pipe, at the bottom of the pipe / insulation
#              "center" -> on the pipe axis
#   p_size   : parameter that takes the pipe size (required)
#   size_is  : "diameter" or "radius"
#   p_center : parameter = height of the pipe axis above the origin ("" = none)
#   p_top    : parameter = height of the structure (slab / beam bottom) above the origin
#   p_ins    : parameter that takes the insulation thickness ("" = none)
#   rotate   : extra rotation in degrees (0 or 90) so the clamp sits across the pipe
#   top_extra_mm : added to p_top (for example an anchor that goes into the slab)
# ------------------------------------------------------------------
PROFILE_KEYS = ("family", "path", "type", "origin", "p_size", "size_is", "p_center",
                "p_top", "p_ins", "rotate", "top_extra_mm")

ASEC_SIGNATURE = ("Diameter", "CenterElevation", "AnchorElevation")


def asec_profile(family, path=u""):
    """Ready profile for the ASEC round clevis (origin on the level, clamp across family Y)."""
    return {"family": family, "path": path, "type": u"", "origin": "level",
            "p_size": u"Diameter", "size_is": "diameter", "p_center": u"CenterElevation",
            "p_top": u"AnchorElevation", "p_ins": u"", "rotate": 90, "top_extra_mm": 0.0}


def guess_profile(family, path, params):
    """Best guess of a profile from the family's length instance parameters.
    params: list of parameter names (length, instance, not formula-driven)."""
    names = list(params)
    if all(n in names for n in ASEC_SIGNATURE):
        return asec_profile(family, path)
    low = [(n, n.lower()) for n in names]

    def first(keys, exclude=()):
        for key in keys:
            for n, l in low:
                if key in l and n not in exclude:
                    return n
        return u""
    size = first(["inside diameter", "pipe diameter", "diameter", "dn", "size", "radius", "od"])
    top = first(["rod top", "anchor", "rod", "overall", "top", "height", "length"], exclude=[size])
    center = first(["centerelev", "center elev", "centre elev", "center", "centre"],
                   exclude=[size, top])
    ins = first(["insul"], exclude=[size, top, center])
    return {"family": family, "path": path, "type": u"",
            "origin": "level" if center else "bottom",
            "p_size": size, "size_is": "radius" if "radius" in size.lower() else "diameter",
            "p_center": center, "p_top": top, "p_ins": ins, "rotate": 0, "top_extra_mm": 0.0}


# ------------------------------------------------------------------
# Profile storage: %APPDATA%\pyRevit\IronTools\hanger_families.json
# (outside the extension folder, so updates never wipe it)
# ------------------------------------------------------------------
def _store_path():
    root = os.path.join(os.environ.get("APPDATA", u""), u"pyRevit", u"IronTools")
    if not os.path.isdir(root):
        os.makedirs(root)
    return os.path.join(root, u"hanger_families.json")


# Ready-made settings that ship with IronTools (lib/support_profiles.json). They hold only the
# parameter mapping, never a family file or a path: each user keeps the .rfa files in their own
# library folder, and a built-in profile is offered only when its family can be found.
BUILTIN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), u"support_profiles.json")


def builtin_profiles():
    try:
        with io.open(BUILTIN_FILE, "r", encoding="utf-8") as fh:
            items = json.load(fh).get("profiles", [])
    except Exception:
        return []
    out = []
    for p in items:
        p = dict(p)
        p["builtin"] = True
        p["path"] = u""
        out.append(p)
    return out


def load_profiles():
    """Saved profiles of this PC plus the built-in ones (a saved profile of the same family wins)."""
    path = _store_path()
    data = None
    if os.path.isfile(path):
        try:
            with io.open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = None
    if not data or not isinstance(data, dict):
        data = {"profiles": [], "last": u""}
        save_profiles(data)
    mine = set(p.get("family") for p in data.get("profiles", []))
    data["profiles"] = list(data.get("profiles", [])) + \
        [p for p in builtin_profiles() if p["family"] not in mine]
    return data


def save_profiles(data):
    """Save this PC's profiles (built-in ones are not copied into the user file)."""
    keep = dict(data)
    keep["profiles"] = [p for p in data.get("profiles", []) if not p.get("builtin")]
    with io.open(_store_path(), "w", encoding="utf-8") as fh:
        fh.write(u"{}".format(json.dumps(keep, indent=2, ensure_ascii=False)))


def family_path(store, profile):
    """The .rfa of a profile: its own saved path, else '<library folder>\\<family>.rfa'."""
    own = profile.get("path") or u""
    if own and os.path.isfile(own):
        return own
    lib = store.get("library") or u""
    if lib:
        cand = os.path.join(lib, profile["family"] + u".rfa")
        if os.path.isfile(cand):
            return cand
    return u""


def library_families(store):
    """Family names (without .rfa) found in the library folder."""
    lib = store.get("library") or u""
    if not lib or not os.path.isdir(lib):
        return set()
    return set(os.path.splitext(f)[0] for f in os.listdir(lib) if f.lower().endswith(u".rfa"))


def upsert_profile(data, profile):
    profile = dict(profile)
    profile.pop("builtin", None)        # an edited built-in profile becomes this PC's own
    data["profiles"] = [p for p in data.get("profiles", []) if p.get("family") != profile["family"]]
    data["profiles"].append(profile)
    data["last"] = profile["family"]
    save_profiles(data)


# ------------------------------------------------------------------
# Family helpers
# ------------------------------------------------------------------
def find_family(doc, name):
    for fam in DB.FilteredElementCollector(doc).OfClass(DB.Family):
        if fam.Name == name:
            return fam
    return None


def load_family(doc, path):
    """Load an .rfa (inside an open transaction). Returns the Family or None."""
    ref = clr.Reference[DB.Family]()
    loaded = doc.LoadFamily(path, ref)
    if loaded and ref.Value is not None:
        return ref.Value
    name = os.path.splitext(os.path.basename(path))[0]
    return find_family(doc, name)


def family_symbol(doc, family, type_name=u""):
    syms = [doc.GetElement(i) for i in family.GetFamilySymbolIds()]
    syms = [s for s in syms if s is not None]
    if not syms:
        return None
    if type_name:
        for s in syms:
            if DB.Element.Name.GetValue(s) == type_name:
                return s
    return syms[0]


def keep_one_type(doc, family, keep):
    """Delete the family's other types that nothing uses, so only the support type is left.
    Inside an open transaction. Returns how many types were removed."""
    removed = 0
    for sid in list(family.GetFamilySymbolIds()):
        if sid == keep.Id:
            continue
        used = DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance) \
            .WherePasses(DB.FamilyInstanceFilter(doc, sid)).GetElementCount()
        if used:
            continue
        try:
            doc.Delete(sid)
            removed += 1
        except Exception:
            pass
    return removed


def family_length_params(doc, family):
    """Names of the length instance parameters that can be set (no formula), read from the family.
    Must be called with NO transaction open (uses EditFamily)."""
    fdoc = doc.EditFamily(family)
    names = []
    try:
        for p in fdoc.FamilyManager.Parameters:
            try:
                is_len = p.Definition.GetDataType() == DB.SpecTypeId.Length
            except Exception:
                is_len = False
            if is_len and p.IsInstance and not p.Formula and not p.IsReporting:
                names.append(p.Definition.Name)
    finally:
        fdoc.Close(False)
    return sorted(names)


# ------------------------------------------------------------------
# Pipe helpers
# ------------------------------------------------------------------
def is_pipe(el):
    return (el is not None and el.Category is not None
            and el.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_PipeCurves)
            and isinstance(getattr(el, "Location", None), DB.LocationCurve))


def pipe_line(pipe):
    curve = pipe.Location.Curve
    return curve if isinstance(curve, DB.Line) else None


def is_vertical(line, limit_deg=45.0):
    d = line.Direction
    return abs(d.Z) > math.sin(math.radians(limit_deg))


def outer_diameter(pipe):
    p = pipe.get_Parameter(DB.BuiltInParameter.RBS_PIPE_OUTER_DIAMETER)
    if p and p.HasValue and p.AsDouble() > 0:
        return p.AsDouble()
    p = pipe.get_Parameter(DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
    return p.AsDouble() if p else mm(50)


def nominal_diameter(pipe):
    p = pipe.get_Parameter(DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
    return p.AsDouble() if p else outer_diameter(pipe)


def insulation(pipe):
    p = pipe.get_Parameter(DB.BuiltInParameter.RBS_REFERENCE_INSULATION_THICKNESS)
    try:
        return p.AsDouble() if p and p.HasValue else 0.0
    except Exception:
        return 0.0


# Typical maximum support spacing for steel pipe (MSS SP-58 / SP-69, water service).
# (largest nominal size in mm, spacing in mm)
SPACING_BY_SIZE = [(25, 2100), (50, 3000), (80, 3700), (100, 4300), (150, 5200),
                   (200, 5800), (99999, 6400)]


def spacing_for(pipe):
    dn = to_mm(nominal_diameter(pipe))
    for limit, spacing in SPACING_BY_SIZE:
        if dn <= limit + 0.5:
            return mm(spacing)
    return mm(SPACING_BY_SIZE[-1][1])


def stations(length, edge, spacing):
    """Distances along a segment: first and last at `edge` from the ends, the rest evenly
    spread with no gap bigger than `spacing`. Short segments get one support in the middle."""
    if length <= 0:
        return []
    if length <= 2 * edge + mm(1):
        return [length / 2.0]
    usable = length - 2 * edge
    spaces = max(1, int(math.ceil(usable / spacing - 1e-9)))
    step = usable / spaces
    return [edge + i * step for i in range(spaces + 1)]


# ------------------------------------------------------------------
# Structure finder (ray straight up, host model + loaded links)
# ------------------------------------------------------------------
REF_VIEW_NAME = u"IronTools Support Ref (temp)"


def make_ref_view(doc):
    """Create a plain 3D view that shows every model element (used only for ray casting).
    Call inside a transaction; delete it again with doc.Delete when done."""
    vft = None
    for t in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType):
        if t.ViewFamily == DB.ViewFamily.ThreeDimensional:
            vft = t
            break
    view = DB.View3D.CreateIsometric(doc, vft.Id)
    try:
        view.Name = REF_VIEW_NAME
    except Exception:
        pass
    try:
        view.ViewTemplateId = DB.ElementId.InvalidElementId
    except Exception:
        pass
    try:
        view.IsSectionBoxActive = False
    except Exception:
        pass
    try:
        view.DetailLevel = DB.ViewDetailLevel.Coarse
    except Exception:
        pass
    return view


_CONCRETE_WORDS = (u"concrete", u"beton", u"béton", u"خرسان")


def _is_concrete_material(mat):
    if mat is None:
        return False
    try:
        if (mat.MaterialClass or u"").lower() == u"concrete":
            return True
    except Exception:
        pass
    name = (mat.Name or u"").lower()
    return any(w in name for w in _CONCRETE_WORDS)


def is_structural_concrete(el, d):
    """True for concrete you can hang from: a concrete slab / roof (structural floor, or a
    concrete layer whose function is Structure) or a concrete beam / foundation.
    Finish floors, screeds and steel joists / beams are ignored.
    `d` is the document that owns `el` (the link document for linked elements)."""
    try:
        cat = el.Category.Id.IntegerValue
    except Exception:
        return False
    framing = (int(DB.BuiltInCategory.OST_StructuralFraming), int(DB.BuiltInCategory.OST_StructuralFoundation))
    if cat in framing:
        try:
            smt = el.StructuralMaterialType
            if smt in (DB.Structure.StructuralMaterialType.Concrete,
                       DB.Structure.StructuralMaterialType.PrecastConcrete):
                return True
            if smt in (DB.Structure.StructuralMaterialType.Steel, DB.Structure.StructuralMaterialType.Wood):
                return False
        except Exception:
            pass
        return any(_is_concrete_material(d.GetElement(m)) for m in el.GetMaterialIds(False))

    # floors / roofs: look at the layers of the type
    cs = None
    try:
        cs = d.GetElement(el.GetTypeId()).GetCompoundStructure()
    except Exception:
        cs = None
    if cs is not None:
        for layer in cs.GetLayers():
            if layer.Function == DB.MaterialFunctionAssignment.Structure and \
                    _is_concrete_material(d.GetElement(layer.MaterialId)):
                return True
    try:
        p = el.get_Parameter(DB.BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL)
        structural = p is not None and p.AsInteger() == 1
    except Exception:
        structural = False
    if structural or cs is None:
        # structural slab, or an in-place / family slab without layers
        return any(_is_concrete_material(d.GetElement(m)) for m in el.GetMaterialIds(False))
    return False


class StructureFinder(object):
    """Finds the underside (soffit) of the nearest concrete slab / beam above a point,
    in this model and in every loaded link (structural or architectural)."""

    def __init__(self, view, use_floors=True, use_beams=True, use_links=True, max_up=mm(6000)):
        cats = []
        if use_floors:
            cats += [DB.BuiltInCategory.OST_Floors, DB.BuiltInCategory.OST_Roofs,
                     DB.BuiltInCategory.OST_StructuralFoundation]
        if use_beams:
            cats += [DB.BuiltInCategory.OST_StructuralFraming]
        flt = DB.ElementMulticategoryFilter(List[DB.BuiltInCategory](cats))
        self.ri = DB.ReferenceIntersector(flt, DB.FindReferenceTarget.Face, view)
        self.ri.FindReferencesInRevitLinks = bool(use_links)
        self.view = view
        self.doc = view.Document
        self.max_up = max_up
        self._ok = {}           # (link id, element id) -> concrete?

    def _element(self, ref):
        host = self.doc.GetElement(ref.ElementId)
        if isinstance(host, DB.RevitLinkInstance):
            ld = host.GetLinkDocument()
            if ld is None:
                return None, None, None
            return (ld.GetElement(ref.LinkedElementId), ld,
                    (ref.ElementId.IntegerValue, ref.LinkedElementId.IntegerValue))
        return host, self.doc, (0, ref.ElementId.IntegerValue)

    def above(self, point):
        """Z of the concrete soffit above `point`, or None."""
        try:
            hits = list(self.ri.Find(point, DB.XYZ.BasisZ))
        except Exception:
            return None
        for hit in sorted(hits, key=lambda h: h.Proximity):
            if hit.Proximity <= 0:
                continue
            if hit.Proximity > self.max_up:
                break
            el, d, key = self._element(hit.GetReference())
            if el is None:
                continue
            ok = self._ok.get(key)
            if ok is None:
                ok = is_structural_concrete(el, d)
                self._ok[key] = ok
            if ok:
                return point.Z + hit.Proximity
        return None

    def _nearest(self, ri_name, cats, point, direction, limit):
        ri = getattr(self, ri_name, None)
        if ri is None:
            flt = DB.ElementMulticategoryFilter(List[DB.BuiltInCategory](cats))
            ri = DB.ReferenceIntersector(flt, DB.FindReferenceTarget.Face, self.view)
            ri.FindReferencesInRevitLinks = True
            setattr(self, ri_name, ri)
        try:
            hits = list(ri.Find(point, direction))
        except Exception:
            return None
        best = None
        for hit in hits:
            if 0 < hit.Proximity <= limit and (best is None or hit.Proximity < best):
                best = hit.Proximity
        return best

    def below(self, point, limit=mm(6000)):
        """Z of the first floor / slab / foundation surface under `point` (finish floors count:
        a post must stand on top of whatever is there), or None."""
        dist = self._nearest("_ri_floor", [DB.BuiltInCategory.OST_Floors, DB.BuiltInCategory.OST_Roofs,
                                           DB.BuiltInCategory.OST_StructuralFoundation],
                             point, DB.XYZ.BasisZ.Negate(), limit)
        return None if dist is None else point.Z - dist

    def wall(self, point, direction, limit=mm(2000)):
        """Distance from `point` to the first wall face in `direction` (host or links), or None."""
        return self._nearest("_ri_wall", [DB.BuiltInCategory.OST_Walls], point, direction, limit)


# ------------------------------------------------------------------
# Support record (extensible storage: which pipe + where along it)
# ------------------------------------------------------------------
_SCHEMA_GUID = Guid("6b1f6c1e-7d2a-4a8e-9a43-2f5f0c9e7a11")


def _schema():
    sch = Schema.Lookup(_SCHEMA_GUID)
    if sch is not None:
        return sch
    sb = SchemaBuilder(_SCHEMA_GUID)
    sb.SetSchemaName("IronToolsHanger")
    sb.SetVendorId("IRONTOOLS")
    sb.SetReadAccessLevel(AccessLevel.Public)
    sb.SetWriteAccessLevel(AccessLevel.Public)
    sb.AddSimpleField("Data", str)
    return sb.Finish()


def stamp(hanger, pipe, dist, center_z=None):
    """Remember on the support which pipe it belongs to and how far along it is."""
    try:
        sch = _schema()
        ent = Entity(sch)
        data = {"pipe": pipe.UniqueId, "d": round(dist, 6)}
        if center_z is not None:
            data["z"] = round(center_z, 6)
        ent.Set[str]("Data", json.dumps(data))
        hanger.SetEntity(ent)
    except Exception:
        pass


def read_stamp(el):
    """The stamp dict of a support placed by this tool, or None."""
    sch = Schema.Lookup(_SCHEMA_GUID)
    if sch is None or el is None:
        return None
    try:
        ent = el.GetEntity(sch)
        if not ent.IsValid():
            return None
        return json.loads(ent.Get[str]("Data"))
    except Exception:
        return None


def all_hangers(doc):
    """Every support in the model placed by this tool."""
    if Schema.Lookup(_SCHEMA_GUID) is None:
        return []
    flt = DB.ExtensibleStorage.ExtensibleStorageFilter(_SCHEMA_GUID)
    return [el for el in DB.FilteredElementCollector(doc).WherePasses(flt).WhereElementIsNotElementType()
            if isinstance(el, DB.FamilyInstance)]


def existing_stations(doc):
    """{pipe UniqueId: [distances]} for the supports this tool placed before."""
    out = {}
    for el in all_hangers(doc):
        data = read_stamp(el)
        if data:
            out.setdefault(data.get("pipe"), []).append(float(data.get("d", 0)))
    return out


def locate(doc, hanger):
    """Find the pipe a support belongs to now and where along it, after pipes were moved,
    stretched or split. Returns (pipe, dist) or (None, reason)."""
    data = read_stamp(hanger)
    if not data:
        return None, "not placed by Support Generator"
    pt = hanger.Location.Point
    old_z = data.get("z")

    def fit(pipe):
        line = pipe_line(pipe) if is_pipe(pipe) else None
        if line is None or is_vertical(line):
            return None
        d = line.Direction
        flat = DB.XYZ(d.X, d.Y, 0)
        if flat.GetLength() < 1e-9:
            return None
        flat = flat.Normalize()
        start = line.GetEndPoint(0)
        rel = DB.XYZ(pt.X - start.X, pt.Y - start.Y, 0)
        along_flat = rel.DotProduct(flat)
        side = abs(rel.X * flat.Y - rel.Y * flat.X)
        dist = along_flat / max(flat.DotProduct(DB.XYZ(d.X, d.Y, 0)), 1e-9)
        return line, dist, side

    pipe = doc.GetElement(data.get("pipe") or u"")
    if pipe is not None and is_pipe(pipe):
        vline = pipe_line(pipe)
        if vline is not None and is_vertical(vline):
            # riser: keep the same height along the vertical pipe
            return pipe, min(max(float(data.get("d", 0)), 0.0), vline.Length)
    if pipe is not None:
        res = fit(pipe)
        if res is not None:
            line, dist, side = res
            if -mm(5) <= dist <= line.Length + mm(5):
                return pipe, min(max(dist, 0.0), line.Length)

    # the pipe was split, shortened or deleted: look for the pipe right under / over the support
    # (older stamps have no pipe height: use the bottom of the support as the pipe bottom)
    low = None
    if old_z is None:
        bb = hanger.get_BoundingBox(None)
        low = bb.Min.Z if bb is not None else None
    box = DB.Outline(DB.XYZ(pt.X - mm(150), pt.Y - mm(150), -1e4), DB.XYZ(pt.X + mm(150), pt.Y + mm(150), 1e4))
    best = None
    for cand in (DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_PipeCurves)
                 .WhereElementIsNotElementType().WherePasses(DB.BoundingBoxIntersectsFilter(box))):
        res = fit(cand)
        if res is None:
            continue
        line, dist, side = res
        # a split piece lies on the same axis: only accept pipes right on the support
        if dist < -mm(5) or dist > line.Length + mm(5) or side > mm(100):
            continue
        c = line.GetEndPoint(0) + line.Direction * min(max(dist, 0.0), line.Length)
        if old_z is not None:
            dz = abs(c.Z - float(old_z))
        elif low is not None:
            dz = abs(c.Z - outer_diameter(cand) / 2.0 - insulation(cand) - low)
        else:
            dz = 0.0
        if dz > mm(300):
            continue
        score = side + dz
        if best is None or score < best[0]:
            best = (score, cand, min(max(dist, 0.0), line.Length))
    if best is not None:
        return best[1], best[2]
    if pipe is None:
        return None, "its pipe was deleted"
    return None, "its pipe moved too far away"


def copy_identity(src, dst):
    """Keep comments, mark and workset when a support is rebuilt."""
    for bip in (DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, DB.BuiltInParameter.ALL_MODEL_MARK,
                DB.BuiltInParameter.ELEM_PARTITION_PARAM):
        try:
            a = src.get_Parameter(bip)
            b = dst.get_Parameter(bip)
            if a is None or b is None or b.IsReadOnly or not a.HasValue:
                continue
            if a.StorageType == DB.StorageType.String:
                b.Set(a.AsString() or u"")
            elif a.StorageType == DB.StorageType.Integer:
                b.Set(a.AsInteger())
        except Exception:
            pass


# ------------------------------------------------------------------
# Placement
# ------------------------------------------------------------------
def _set_len(el, name, value):
    if not name:
        return True
    p = el.LookupParameter(name)
    if p is None or p.IsReadOnly:
        return False
    try:
        return bool(p.Set(value))
    except Exception:
        return False


def _set_offset(el, value):
    for bip in (DB.BuiltInParameter.INSTANCE_ELEVATION_PARAM,
                DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM):
        p = el.get_Parameter(bip)
        if p is not None and not p.IsReadOnly:
            try:
                p.Set(value)
                return True
            except Exception:
                pass
    return False


def _level_for(doc, pipe):
    lvl = None
    try:
        lvl = pipe.ReferenceLevel
    except Exception:
        pass
    if lvl is None:
        try:
            lvl = doc.GetElement(pipe.LevelId)
        except Exception:
            lvl = None
    return lvl


# gap kept between a support and the concrete / floor / wall it is fixed to (no clash in Navisworks)
TOP_CLEARANCE = mm(1)

# ------------------------------------------------------------------
# MOUNT TYPES (profile["mount"])
#   ceiling : hangs from the underside of the concrete above (rod up)
#   floor   : stands on the floor / slab below (post down)
#   wall    : horizontal pipe, bracket to the nearest wall at its side
#   riser   : vertical pipe, bracket / clamp to the nearest wall around it
# PIPE POSITION in the family (all optional, family axes, r = pipe radius incl. insulation):
#   pipe centre = origin + (cx_k*r + cx_mm, cy_k*r + cy_mm, cz_k*r + cz_mm)
#   "origin": "bottom" is the same as cz_k = 1, "center" is all zero, "level" puts the origin
#   on the pipe's level and drives p_center instead.
# FAMILY AXES: ceiling / floor -> family X along the pipe after `rotate` degrees;
#   wall / riser -> family +Y points at the wall.
# REACH: p_reach (or p_top) = distance from the origin to the concrete / floor / wall face
#   along the mount direction; reach_extra_mm (or top_extra_mm) is added.
# also_size: other parameters that take the same size value (e.g. a second clamp).
# ------------------------------------------------------------------
MOUNTS = ("ceiling", "floor", "wall", "riser")


def mount_of(profile):
    m = (profile or {}).get("mount") or "ceiling"
    return m if m in MOUNTS else "ceiling"


def geometry_extreme(inst, direction):
    """Largest value of (point . direction) over the real 3D solids of an instance
    (what Navisworks sees). The bounding box is not used: families often carry symbols or
    references that stick out."""
    opt = DB.Options()
    opt.DetailLevel = DB.ViewDetailLevel.Fine
    opt.IncludeNonVisibleObjects = False
    best = [None]

    def walk(geo):
        for g in geo:
            if isinstance(g, DB.GeometryInstance):
                walk(g.GetInstanceGeometry())
            elif isinstance(g, DB.Solid) and g.Volume > 1e-9:
                for edge in g.Edges:
                    for p in edge.Tessellate():
                        v = p.DotProduct(direction)
                        if best[0] is None or v > best[0]:
                            best[0] = v
    try:
        walk(inst.get_Geometry(opt))
    except Exception:
        return None
    return best[0]


def geometry_top(inst):
    return geometry_extreme(inst, DB.XYZ.BasisZ)


def _wrap(angle):
    while angle > math.pi:
        angle -= 2 * math.pi
    while angle <= -math.pi:
        angle += 2 * math.pi
    return angle


def _flat(v):
    f = DB.XYZ(v.X, v.Y, 0)
    return f.Normalize() if f.GetLength() > 1e-9 else None


def _rot2d(v, angle):
    c, s = math.cos(angle), math.sin(angle)
    return DB.XYZ(v.X * c - v.Y * s, v.X * s + v.Y * c, 0)


class Placer(object):
    """Places / updates supports of one profile. Use inside an open transaction."""

    def __init__(self, doc, profile, symbol, finder, around_insulation=True,
                 no_structure_rod=mm(1000), wall_reach=mm(2000)):
        self.doc = doc
        self.pf = profile
        self.mount = mount_of(profile)
        self.symbol = symbol
        self.finder = finder
        self.around_ins = around_insulation
        self.no_structure_rod = no_structure_rod
        self.wall_reach = wall_reach
        self.problems = []          # (element id, message)
        self.no_structure = 0
        self.bad_params = set()
        self._over = {}             # (type id, size mm) -> how far the geometry goes past the reach

    # ---------------- profile helpers ----------------
    def _reach_param(self):
        return self.pf.get("p_reach") or self.pf.get("p_top") or u""

    def _reach_extra(self):
        v = self.pf.get("reach_extra_mm")
        if v is None:
            v = self.pf.get("top_extra_mm", 0)
        return mm(v or 0)

    def _pipe_offset(self, r):
        """Pipe centre relative to the family origin, in family axes (x, y, z)."""
        pf = self.pf
        origin = pf.get("origin", "level")
        cz_k = pf.get("cz_k", 1.0 if origin == "bottom" else 0.0)

        def g(key, default=0.0):
            v = pf.get(key)
            return float(default if v is None else v)
        return (g("cx_k") * r + mm(g("cx_mm")), g("cy_k") * r + mm(g("cy_mm")),
                float(cz_k) * r + mm(g("cz_mm")))

    def symmetric(self):
        ox, oy, _ = self._pipe_offset(mm(50))
        return self.mount in ("ceiling", "floor") and abs(ox) < 1e-6 and abs(oy) < 1e-6

    # ---------------- geometry ----------------
    def _nearest_wall(self, center, dirs, r_out):
        best = None
        for v in dirs:
            start = center + v * (r_out + mm(2))
            dist = self.finder.wall(start, v, self.wall_reach) if self.finder else None
            if dist is not None and (best is None or dist < best[0]):
                best = (dist, v, start + v * dist)
        return best

    def _solve(self, pipe, dist, lvl):
        """Everything a support needs at `dist` along `pipe`, relative to level `lvl`.
        Returns None (and records why) when it cannot be placed."""
        pf = self.pf
        line = pipe_line(pipe)
        d = line.Direction
        center = line.GetEndPoint(0) + d * dist
        ins = insulation(pipe)
        r_pipe = outer_diameter(pipe) / 2.0
        r_out = r_pipe + (ins if self.around_ins else 0.0)
        level_z = lvl.ProjectElevation
        Z = DB.XYZ.BasisZ
        vertical = is_vertical(line)
        if self.mount == "riser" and not vertical:
            self.problems.append((pipe.Id, "riser supports go on vertical pipes only"))
            return None
        if self.mount != "riser" and vertical:
            self.problems.append((pipe.Id, "vertical pipe: use a riser support"))
            return None

        # family axes in the model
        wall_pt = None
        if self.mount in ("ceiling", "floor"):
            u = _flat(d)
            X = _rot2d(u, math.radians(float(pf.get("rotate", 0) or 0)))
            Y = DB.XYZ(-X.Y, X.X, 0)
        else:
            if self.mount == "wall":
                u = _flat(d)
                side = DB.XYZ(-u.Y, u.X, 0)
                dirs = [side, side.Negate()]
            else:
                dirs = [DB.XYZ(math.cos(a), math.sin(a), 0) for a in
                        [math.radians(k * 45.0) for k in range(8)]]
            hit = self._nearest_wall(center, dirs, r_out)
            if hit is None:
                self.problems.append((pipe.Id, "no wall within {:g} mm".format(to_mm(self.wall_reach))))
                return None
            Y = hit[1]
            X = DB.XYZ(Y.Y, -Y.X, 0)
            wall_pt = hit[2]

        ox, oy, oz = self._pipe_offset(r_out)
        origin = center - X * ox - Y * oy - Z * oz
        if pf.get("origin") == "level" and self.mount == "ceiling":
            origin = DB.XYZ(origin.X, origin.Y, level_z)

        # where the support must end, and in which direction
        extra = self._reach_extra()
        found = True
        if self.mount == "ceiling":
            dirv = Z
            soffit = None
            if self.finder:
                soffit = self.finder.above(DB.XYZ(center.X, center.Y, center.Z + r_pipe + ins + mm(2)))
            if soffit is None:
                found = False
                soffit = center.Z + r_out + self.no_structure_rod
            target = soffit + extra
        elif self.mount == "floor":
            dirv = Z.Negate()
            floor = None
            if self.finder:
                # a post is wider than one ray: look down at the centre and at four points
                # around the base, and stand on the highest surface found
                z0 = center.Z - r_pipe - ins - mm(2)
                k = r_out + mm(80)
                for dx, dy in ((0, 0), (k, 0), (-k, 0), (0, k), (0, -k)):
                    p = center + X * dx + Y * dy
                    hit = self.finder.below(DB.XYZ(p.X, p.Y, z0))
                    if hit is not None and (floor is None or hit > floor):
                        floor = hit
            if floor is None:
                found = False
                floor = center.Z - r_out - self.no_structure_rod
            target = -(floor - extra)
        else:
            dirv = Y
            target = wall_pt.DotProduct(Y) + extra
        reach = target - origin.DotProduct(dirv)

        size = 2 * r_out if pf.get("size_is", "diameter") == "diameter" else r_out
        size += mm(pf.get("size_add_mm", 0) or 0)
        values = [(pf.get("p_size"), size), (pf.get("p_ins"), ins)]
        for name in pf.get("also_size") or []:
            values.append((name, size))
        if pf.get("origin") == "level" and self.mount == "ceiling":
            values.append((pf.get("p_center"), center.Z - level_z))
        angle = math.atan2(X.Y, X.X)
        return {"center": center, "origin": origin, "offset": origin.Z - level_z, "values": values,
                "reach": reach, "target": target, "dirv": dirv, "size": size,
                "angle": angle, "found": found}

    # ---------------- writing ----------------
    def _apply(self, inst, s):
        _set_offset(inst, s["offset"])
        for name, value in s["values"]:
            if name and not _set_len(inst, name, value):
                self.bad_params.add(name)
        self._set_reach(inst, s)
        if not s["found"]:
            self.no_structure += 1

    def _set_reach(self, inst, s):
        """Set the rod / post / arm parameter so the END OF THE REAL 3D GEOMETRY (rod, anchor,
        base plate, bracket) stops TOP_CLEARANCE before the concrete, floor or wall: nothing
        enters it, so Navisworks finds no clash. How far a family's geometry goes past its
        parameter is measured once per type and size and taken off."""
        name = self._reach_param()
        if not name:
            return
        key = (inst.Symbol.Id.IntegerValue, round(to_mm(s["size"]), 1))
        over = self._over.get(key)
        if over is None:
            if not _set_len(inst, name, s["reach"]):
                self.bad_params.add(name)
                return
            self.doc.Regenerate()
            end = geometry_extreme(inst, s["dirv"])
            over = (end - s["target"]) if end is not None else 0.0
            if abs(over) > mm(1000):        # the parameter does not drive that end: leave it
                over = 0.0
            self._over[key] = over
        if not _set_len(inst, name, s["reach"] - over - TOP_CLEARANCE):
            self.bad_params.add(name)

    def _turn_to(self, inst, s, exact):
        o = s["origin"]
        hand = inst.HandOrientation
        turn = _wrap(s["angle"] - math.atan2(hand.Y, hand.X))
        if abs(turn) < math.radians(0.5):
            return
        if not exact and abs(abs(turn) - math.pi) < math.radians(0.5):
            return              # a symmetric clamp: 180 degrees off is as good as right
        axis = DB.Line.CreateBound(DB.XYZ(o.X, o.Y, 0), DB.XYZ(o.X, o.Y, 1))
        DB.ElementTransformUtils.RotateElement(self.doc, inst.Id, axis, turn)

    def place(self, pipe, dist):
        """Place one support on `pipe` at `dist` (feet from its start). Returns the instance or None."""
        doc = self.doc
        line = pipe_line(pipe)
        lvl = _level_for(doc, pipe)
        if line is None or lvl is None:
            self.problems.append((pipe.Id, "not a straight pipe or has no level"))
            return None
        s = self._solve(pipe, dist, lvl)
        if s is None:
            return None
        o = s["origin"]
        try:
            inst = doc.Create.NewFamilyInstance(DB.XYZ(o.X, o.Y, 0), self.symbol, lvl,
                                                DB.Structure.StructuralType.NonStructural)
        except Exception as ex:
            self.problems.append((pipe.Id, "could not place the family: {}".format(ex)))
            return None
        # turn first: the reach is measured on the geometry as it finally sits
        try:
            self._turn_to(inst, s, exact=True)
        except Exception:
            pass
        self._apply(inst, s)
        stamp(inst, pipe, dist, s["center"].Z)
        return inst

    def adjust(self, inst, pipe, dist):
        """Move / resize an existing support so it sits on `pipe` at `dist` again.
        Returns True if anything changed."""
        doc = self.doc
        lvl = doc.GetElement(inst.LevelId) or _level_for(doc, pipe)
        if pipe_line(pipe) is None or lvl is None:
            self.problems.append((inst.Id, "pipe is not straight or support has no level"))
            return False
        s = self._solve(pipe, dist, lvl)
        if s is None:
            return False
        before = self._snapshot(inst)
        exact = not self.symmetric()
        self._turn_to(inst, s, exact)
        doc.Regenerate()
        o = s["origin"]
        pt = inst.Location.Point
        move = DB.XYZ(o.X - pt.X, o.Y - pt.Y, 0)
        if move.GetLength() > mm(0.5):
            DB.ElementTransformUtils.MoveElement(doc, inst.Id, move)
        self._apply(inst, s)
        stamp(inst, pipe, dist, s["center"].Z)
        doc.Regenerate()
        return self._snapshot(inst) != before

    def _snapshot(self, inst):
        pt = inst.Location.Point
        vals = [round(to_mm(pt.X)), round(to_mm(pt.Y)),
                round(math.degrees(math.atan2(inst.HandOrientation.Y, inst.HandOrientation.X)))]
        names = [self.pf.get(k) for k in ("p_size", "p_center", "p_ins")] + [self._reach_param()]
        for name in names:
            p = inst.LookupParameter(name) if name else None
            vals.append(round(to_mm(p.AsDouble()), 1) if p is not None else None)
        off = inst.get_Parameter(DB.BuiltInParameter.INSTANCE_ELEVATION_PARAM) or \
            inst.get_Parameter(DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
        vals.append(round(to_mm(off.AsDouble()), 1) if off is not None else None)
        return vals

    def replace(self, old, pipe, dist):
        """Place a support of this profile where `old` is, then delete `old`. Returns the new one."""
        inst = self.place(pipe, dist)
        if inst is None:
            return None
        copy_identity(old, inst)
        try:
            self.doc.Delete(old.Id)
        except Exception:
            pass
        return inst


def pipe_top(pipe, dist):
    """Point on top of the pipe (or its insulation) at `dist` along it."""
    line = pipe_line(pipe)
    c = line.GetEndPoint(0) + line.Direction * dist
    return DB.XYZ(c.X, c.Y, c.Z + outer_diameter(pipe) / 2.0 + insulation(pipe))


# ------------------------------------------------------------------
# Click mode: orange markers that show where supports will go.
# They are temporary elements: deleted again before the supports are placed.
# ------------------------------------------------------------------
MARKER_RGB = (255, 128, 0)


def _solid_fill(doc):
    for fp in DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement):
        try:
            pat = fp.GetFillPattern()
            if pat.IsSolidFill and pat.Target == DB.FillPatternTarget.Drafting:
                return fp.Id
        except Exception:
            pass
    return None


def _circle_loop(center, radius):
    loop = DB.CurveLoop()
    loop.Append(DB.Arc.Create(center, radius, 0, math.pi, DB.XYZ.BasisX, DB.XYZ.BasisY))
    loop.Append(DB.Arc.Create(center, radius, math.pi, 2 * math.pi, DB.XYZ.BasisX, DB.XYZ.BasisY))
    return loop


def _arrow_solids(tip, size):
    """Downward arrow whose point touches `tip`: a cone and a shaft above it."""
    head = size * 0.38
    head_r = size * 0.17
    shaft_r = size * 0.055
    GCU = DB.GeometryCreationUtilities
    # cone: triangle profile revolved around the vertical axis through the tip
    prof = DB.CurveLoop()
    a = tip
    b = tip + DB.XYZ(head_r, 0, head)
    c = tip + DB.XYZ(0, 0, head)
    prof.Append(DB.Line.CreateBound(a, b))
    prof.Append(DB.Line.CreateBound(b, c))
    prof.Append(DB.Line.CreateBound(c, a))
    frame = DB.Frame(tip, DB.XYZ.BasisX, DB.XYZ.BasisY, DB.XYZ.BasisZ)
    cone = GCU.CreateRevolvedGeometry(frame, List[DB.CurveLoop]([prof]), 0, 2 * math.pi)
    shaft = GCU.CreateExtrusionGeometry(List[DB.CurveLoop]([_circle_loop(c, shaft_r)]),
                                        DB.XYZ.BasisZ, size - head)
    return [cone, shaft]


class Markers(object):
    """Temporary orange arrows (3D views) or target circles (plans / sections).
    Every method must run inside an open transaction."""

    def __init__(self, doc, view):
        self.doc = doc
        self.view = view
        self.items = []                 # [pipe, dist, [element ids]]
        try:
            scale = float(view.Scale or 100)
        except Exception:
            scale = 100.0
        self.size = min(max(mm(7) * scale, mm(250)), mm(2000))
        self.is3d = isinstance(view, DB.View3D)
        ogs = DB.OverrideGraphicSettings()
        col = DB.Color(*MARKER_RGB)
        ogs.SetProjectionLineColor(col)
        ogs.SetProjectionLineWeight(6)
        fill = _solid_fill(doc)
        if fill is not None:
            try:
                ogs.SetSurfaceForegroundPatternId(fill)
                ogs.SetSurfaceForegroundPatternColor(col)
            except Exception:
                pass
        self.ogs = ogs

    def _find(self, pipe, dist):
        tol = max(mm(100), self.size * 0.25)
        for i, (p, d, _) in enumerate(self.items):
            if p.Id == pipe.Id and abs(d - dist) < tol:
                return i
        return -1

    def toggle(self, pipe, dist):
        """Add a marker, or remove it when you click the same spot again. True = added."""
        i = self._find(pipe, dist)
        if i >= 0:
            for eid in self.items[i][2]:
                try:
                    self.doc.Delete(eid)
                except Exception:
                    pass
            del self.items[i]
            return False
        tip = pipe_top(pipe, dist)
        ids = self._draw(tip, pipe_line(pipe).Direction)
        self.items.append([pipe, dist, ids])
        return True

    def _draw(self, tip, direction):
        doc, view = self.doc, self.view
        ids = []
        if self.is3d:
            ds = DB.DirectShape.CreateElement(doc, DB.ElementId(DB.BuiltInCategory.OST_GenericModel))
            ds.SetShape(List[DB.GeometryObject](_arrow_solids(tip, self.size)))
            try:
                ds.Name = u"IronTools support marker"
            except Exception:
                pass
            ids.append(ds.Id)
        else:
            # target circle + a short bar across the pipe, drawn in the view plane
            n = view.ViewDirection
            o = view.Origin
            p = tip - n * (tip - o).DotProduct(n)
            u = view.RightDirection
            v = view.UpDirection
            r = self.size * 0.22
            for a0, a1 in ((0, math.pi), (math.pi, 2 * math.pi)):
                arc = DB.Arc.Create(p, r, a0, a1, u, v)
                ids.append(doc.Create.NewDetailCurve(view, arc).Id)
            across = DB.XYZ(-direction.Y, direction.X, 0)
            across = across - n * across.DotProduct(n)
            if across.GetLength() > 1e-6:
                across = across.Normalize() * (r * 1.8)
                ids.append(doc.Create.NewDetailCurve(
                    view, DB.Line.CreateBound(p - across, p + across)).Id)
        for eid in ids:
            try:
                view.SetElementOverrides(eid, self.ogs)
            except Exception:
                pass
        return ids

    def clear(self):
        for _, _, ids in self.items:
            for eid in ids:
                try:
                    self.doc.Delete(eid)
                except Exception:
                    pass

    def spots(self):
        return [(p, d) for p, d, _ in self.items]


def plan_auto(pipes, edge, spacing=None, by_size=False, skip_existing=None, min_gap=mm(150),
              mount="ceiling"):
    """List of (pipe, dist) for automatic placement + counts of skipped pipes.
    Riser supports take vertical pipes only, all other mounts horizontal / sloped pipes only.
    `vertical` counts the pipes skipped for that reason."""
    plan = []
    vertical = short = 0
    for pipe in pipes:
        line = pipe_line(pipe)
        if line is None:
            continue
        if is_vertical(line) != (mount == "riser"):
            vertical += 1
            continue
        if line.Length < mm(100):
            short += 1
            continue
        step = spacing_for(pipe) if by_size or not spacing else spacing
        taken = (skip_existing or {}).get(pipe.UniqueId, [])
        for dist in stations(line.Length, edge, step):
            if any(abs(dist - t) < min_gap for t in taken):
                continue
            plan.append((pipe, dist))
    return plan, vertical, short


# ------------------------------------------------------------------
# Clash check (like a Navisworks hard clash): do the real 3D solids of a support
# intersect floors, roofs, beams, columns, foundations or walls in this model or a link?
# ------------------------------------------------------------------
CLASH_CATS = [DB.BuiltInCategory.OST_Floors, DB.BuiltInCategory.OST_Roofs,
              DB.BuiltInCategory.OST_StructuralFraming, DB.BuiltInCategory.OST_StructuralColumns,
              DB.BuiltInCategory.OST_StructuralFoundation, DB.BuiltInCategory.OST_Walls]


def _solids(inst):
    opt = DB.Options()
    opt.DetailLevel = DB.ViewDetailLevel.Fine
    out = []

    def walk(g):
        for x in g:
            if isinstance(x, DB.GeometryInstance):
                walk(x.GetInstanceGeometry())
            elif isinstance(x, DB.Solid) and x.Volume > 1e-9:
                out.append(x)
    try:
        walk(inst.get_Geometry(opt))
    except Exception:
        pass
    return out


class ClashChecker(object):
    def __init__(self, doc):
        self.doc = doc
        self.flt = DB.ElementMulticategoryFilter(List[DB.BuiltInCategory](CLASH_CATS))
        self.targets = [(doc, None, u"this model")]
        for li in DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance):
            ld = li.GetLinkDocument()
            if ld is not None:
                self.targets.append((ld, li.GetTotalTransform().Inverse, ld.Title))

    def hits(self, inst):
        """['<model>: <category> <type>', ...] for everything the support cuts into."""
        out = set()
        ss = _solids(inst)
        if not ss:
            return []
        bb = inst.get_BoundingBox(None)
        for d, inv, name in self.targets:
            for s in ss:
                try:
                    local = s if inv is None else DB.SolidUtils.CreateTransformed(s, inv)
                    col = DB.FilteredElementCollector(d).WherePasses(self.flt)
                    if bb is not None:
                        lo = bb.Min if inv is None else inv.OfPoint(bb.Min)
                        hi = bb.Max if inv is None else inv.OfPoint(bb.Max)
                        box = DB.Outline(DB.XYZ(min(lo.X, hi.X), min(lo.Y, hi.Y), min(lo.Z, hi.Z)),
                                         DB.XYZ(max(lo.X, hi.X), max(lo.Y, hi.Y), max(lo.Z, hi.Z)))
                        col = col.WherePasses(DB.BoundingBoxIntersectsFilter(box))
                    col = col.WherePasses(DB.ElementIntersectsSolidFilter(local))
                    for el in col:
                        if d is self.doc and el.Id == inst.Id:
                            continue
                        # demolished elements are not in the finished building (nor in Navisworks)
                        dem = el.get_Parameter(DB.BuiltInParameter.PHASE_DEMOLISHED)
                        if dem is not None and dem.AsElementId() != DB.ElementId.InvalidElementId:
                            continue
                        t = d.GetElement(el.GetTypeId())
                        out.add(u"{}: {} {}".format(name, el.Category.Name,
                                                    DB.Element.Name.GetValue(t) if t else u""))
                except Exception:
                    pass
        return sorted(out)