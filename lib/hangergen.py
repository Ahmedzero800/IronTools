# -*- coding: utf-8 -*-
"""Hanger Generator engine (no UI): place pipe hangers along pipes, sized to the pipe
(and its insulation) and with the rod reaching the nearest slab / beam above.

Works with any hanger family through a "profile" that tells the tool which family
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
# PROFILE FIELDS (one profile per hanger family, saved as JSON)
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


DEFAULT_ASEC_PATH = (u"D:\\collage\\MY DATA\\08- REVIT FAMILY\\Revit Families\\"
                     u"ASEC-Hanger_Round_Clevis-R21.rfa")


def load_profiles():
    """Return the list of saved profiles (adds the ASEC clevis the first time)."""
    path = _store_path()
    data = None
    if os.path.isfile(path):
        try:
            with io.open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = None
    if not data or not isinstance(data, dict):
        data = {"profiles": [asec_profile(u"ASEC-Hanger_Round_Clevis-R21", DEFAULT_ASEC_PATH)],
                "last": u"ASEC-Hanger_Round_Clevis-R21"}
        save_profiles(data)
    return data


def save_profiles(data):
    with io.open(_store_path(), "w", encoding="utf-8") as fh:
        fh.write(u"{}".format(json.dumps(data, indent=2, ensure_ascii=False)))


def upsert_profile(data, profile):
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


# Typical maximum hanger spacing for steel pipe (MSS SP-58 / SP-69, water service).
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
    spread with no gap bigger than `spacing`. Short segments get one hanger in the middle."""
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
REF_VIEW_NAME = u"IronTools Hanger Ref (temp)"


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


class StructureFinder(object):
    """Finds the bottom of the nearest slab / beam / roof above a point."""

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
        self.max_up = max_up

    def above(self, point):
        """Z of the structure above `point`, or None."""
        try:
            hit = self.ri.FindNearest(point, DB.XYZ.BasisZ)
        except Exception:
            return None
        if hit is None or hit.Proximity <= 0 or hit.Proximity > self.max_up:
            return None
        return point.Z + hit.Proximity


# ------------------------------------------------------------------
# Hanger record (extensible storage: which pipe + where along it)
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
    """Remember on the hanger which pipe it belongs to and how far along it is."""
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
    """The stamp dict of a hanger placed by this tool, or None."""
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
    """Every hanger in the model placed by this tool."""
    if Schema.Lookup(_SCHEMA_GUID) is None:
        return []
    flt = DB.ExtensibleStorage.ExtensibleStorageFilter(_SCHEMA_GUID)
    return [el for el in DB.FilteredElementCollector(doc).WherePasses(flt).WhereElementIsNotElementType()
            if isinstance(el, DB.FamilyInstance)]


def existing_stations(doc):
    """{pipe UniqueId: [distances]} for the hangers this tool placed before."""
    out = {}
    for el in all_hangers(doc):
        data = read_stamp(el)
        if data:
            out.setdefault(data.get("pipe"), []).append(float(data.get("d", 0)))
    return out


def locate(doc, hanger):
    """Find the pipe a hanger belongs to now and where along it, after pipes were moved,
    stretched or split. Returns (pipe, dist) or (None, reason)."""
    data = read_stamp(hanger)
    if not data:
        return None, "not placed by Hanger Generator"
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
    if pipe is not None:
        res = fit(pipe)
        if res is not None:
            line, dist, side = res
            if -mm(5) <= dist <= line.Length + mm(5):
                return pipe, min(max(dist, 0.0), line.Length)

    # the pipe was split, shortened or deleted: look for the pipe right under / over the hanger
    # (older stamps have no pipe height: use the bottom of the hanger as the pipe bottom)
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
        # a split piece lies on the same axis: only accept pipes right on the hanger
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
    """Keep comments, mark and workset when a hanger is rebuilt."""
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


def _wrap(angle):
    while angle > math.pi:
        angle -= 2 * math.pi
    while angle <= -math.pi:
        angle += 2 * math.pi
    return angle


class Placer(object):
    """Places / updates hangers of one profile. Use inside an open transaction."""

    def __init__(self, doc, profile, symbol, finder, around_insulation=True,
                 no_structure_rod=mm(1000)):
        self.doc = doc
        self.pf = profile
        self.symbol = symbol
        self.finder = finder
        self.around_ins = around_insulation
        self.no_structure_rod = no_structure_rod
        self.problems = []          # (element id, message)
        self.no_structure = 0
        self.bad_params = set()

    def _solve(self, pipe, dist, lvl):
        """Everything a hanger needs at `dist` along `pipe`, relative to level `lvl`."""
        pf = self.pf
        line = pipe_line(pipe)
        d = line.Direction
        center = line.GetEndPoint(0) + d * dist
        ins = insulation(pipe)
        r_pipe = outer_diameter(pipe) / 2.0
        r_out = r_pipe + (ins if self.around_ins else 0.0)
        level_z = lvl.ProjectElevation

        # structure above the top of the pipe / insulation
        soffit = None
        if self.finder:
            soffit = self.finder.above(DB.XYZ(center.X, center.Y, center.Z + r_pipe + ins + mm(2)))
        found = soffit is not None
        if not found:
            soffit = center.Z + r_out + self.no_structure_rod

        origin_mode = pf.get("origin", "level")
        if origin_mode == "bottom":
            origin_z = center.Z - r_out
        elif origin_mode == "center":
            origin_z = center.Z
        else:
            origin_z = level_z
        size = 2 * r_out if pf.get("size_is", "diameter") == "diameter" else r_out
        values = [(pf.get("p_size"), size),
                  (pf.get("p_center"), center.Z - origin_z),
                  (pf.get("p_top"), soffit - origin_z + mm(pf.get("top_extra_mm", 0) or 0)),
                  (pf.get("p_ins"), ins)]
        angle = math.atan2(d.Y, d.X) + math.radians(float(pf.get("rotate", 0) or 0))
        return {"center": center, "offset": origin_z - level_z, "values": values,
                "angle": angle, "found": found}

    def _apply(self, inst, s):
        _set_offset(inst, s["offset"])
        for name, value in s["values"]:
            if name and not _set_len(inst, name, value):
                self.bad_params.add(name)
        if not s["found"]:
            self.no_structure += 1

    def place(self, pipe, dist):
        """Place one hanger on `pipe` at `dist` (feet from its start). Returns the instance or None."""
        doc = self.doc
        line = pipe_line(pipe)
        lvl = _level_for(doc, pipe)
        if line is None or lvl is None:
            self.problems.append((pipe.Id, "not a straight pipe or has no level"))
            return None
        s = self._solve(pipe, dist, lvl)
        c = s["center"]
        try:
            inst = doc.Create.NewFamilyInstance(DB.XYZ(c.X, c.Y, 0), self.symbol, lvl,
                                                DB.Structure.StructuralType.NonStructural)
        except Exception as ex:
            self.problems.append((pipe.Id, "could not place the family: {}".format(ex)))
            return None
        self._apply(inst, s)
        # turn the hanger so the clamp sits across the pipe
        if abs(s["angle"]) > 1e-9:
            axis = DB.Line.CreateBound(DB.XYZ(c.X, c.Y, 0), DB.XYZ(c.X, c.Y, 1))
            try:
                DB.ElementTransformUtils.RotateElement(doc, inst.Id, axis, s["angle"])
            except Exception:
                pass
        stamp(inst, pipe, dist, c.Z)
        return inst

    def adjust(self, inst, pipe, dist):
        """Move / resize an existing hanger so it sits on `pipe` at `dist` again.
        Returns True if anything changed."""
        doc = self.doc
        lvl = doc.GetElement(inst.LevelId) or _level_for(doc, pipe)
        if pipe_line(pipe) is None or lvl is None:
            self.problems.append((inst.Id, "pipe is not straight or hanger has no level"))
            return False
        s = self._solve(pipe, dist, lvl)
        c = s["center"]
        before = self._snapshot(inst)
        pt = inst.Location.Point
        move = DB.XYZ(c.X - pt.X, c.Y - pt.Y, 0)
        if move.GetLength() > mm(0.5):
            DB.ElementTransformUtils.MoveElement(doc, inst.Id, move)
        self._apply(inst, s)
        hand = inst.HandOrientation
        turn = _wrap(s["angle"] - math.atan2(hand.Y, hand.X))
        # a clamp is symmetric: 180 degrees off is as good as right
        if abs(turn) > math.radians(0.5) and abs(abs(turn) - math.pi) > math.radians(0.5):
            axis = DB.Line.CreateBound(DB.XYZ(c.X, c.Y, 0), DB.XYZ(c.X, c.Y, 1))
            DB.ElementTransformUtils.RotateElement(doc, inst.Id, axis, turn)
        stamp(inst, pipe, dist, c.Z)
        doc.Regenerate()
        return self._snapshot(inst) != before

    def _snapshot(self, inst):
        pt = inst.Location.Point
        vals = [round(to_mm(pt.X)), round(to_mm(pt.Y)),
                round(math.degrees(math.atan2(inst.HandOrientation.Y, inst.HandOrientation.X)))]
        for key in ("p_size", "p_center", "p_top", "p_ins"):
            name = self.pf.get(key)
            p = inst.LookupParameter(name) if name else None
            vals.append(round(to_mm(p.AsDouble()), 1) if p is not None else None)
        off = inst.get_Parameter(DB.BuiltInParameter.INSTANCE_ELEVATION_PARAM) or \
            inst.get_Parameter(DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
        vals.append(round(to_mm(off.AsDouble()), 1) if off is not None else None)
        return vals

    def replace(self, old, pipe, dist):
        """Place a hanger of this profile where `old` is, then delete `old`. Returns the new one."""
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
# Click mode: orange markers that show where hangers will go.
# They are temporary elements: deleted again before the hangers are placed.
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
                ds.Name = u"IronTools hanger marker"
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


def plan_auto(pipes, edge, spacing=None, by_size=False, skip_existing=None, min_gap=mm(150)):
    """List of (pipe, dist) for automatic placement + counts of skipped pipes."""
    plan = []
    vertical = short = 0
    for pipe in pipes:
        line = pipe_line(pipe)
        if line is None:
            continue
        if is_vertical(line):
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
