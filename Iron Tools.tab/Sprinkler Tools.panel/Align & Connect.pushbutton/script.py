# -*- coding: utf-8 -*-
"""
pyRevit PushButton Script for Revit 2025
Title: Align & Connect Sprinklers (25mm Nipple)
Description:
- Ignores sprinklers that are already connected.
- Aligns selected Upright & Pendent Sprinklers perpendicular to the XY centerline
  of selected Firefighting Pipes (Branch or Main, Horizontal or Sloped) without
  shifting sprinklers along the pipe length and while strictly locking Sprinkler
  Elevation & Offset from Host.
- Automatically shifts any conflicting inline Pipe Reducer along the line so that
  the LARGER diameter pipe increases in length, clearing space for the sprinkler Tee.
- Automatically deletes any Pipe Cap at the end of a pipe, extends the pipe along
  its axis to reach the furthest sprinkler beyond its end, and connects the end
  sprinkler with an Elbow + 25mm Nipple.
- Sorts sprinklers sequentially along each pipe and connects intermediate sprinklers
  with a Tee + Reducer + 25mm Nipple (or Takeoff).
- Automatically selects/highlights any unconnected sprinklers in the Revit model
  upon completion so the user can inspect them immediately.
"""

__title__ = "Align & Connect\nSprinklers"
__author__ = "BIM Automation"
__doc__ = (
    "Aligns Upright & Pendent Sprinklers to Pipe centerlines (locking Elevation & "
    "Offset from Host), shifts conflicting Reducers to lengthen the larger pipe, "
    "extends open/capped pipe ends, removes Caps, and connects via 25mm Nipple."
)

from System.Collections.Generic import List
from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    ConnectorType,
    Domain,
    ElementId,
    ElementTransformUtils,
    FamilyInstance,
    Line,
    PartType,
    SubTransaction,
    Transaction,
    UnitTypeId,
    UnitUtils,
    XYZ,
)
from Autodesk.Revit.DB.Plumbing import Pipe, PlumbingUtils
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
import irontools_ui as dui

TOOL_TITLE = "Align & Connect Sprinklers"

uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

# ---------------------------------------------------------------------------
# Configuration Constants
# ---------------------------------------------------------------------------
# Fixed Nipple Diameter in Millimeters (always 25 mm)
NIPPLE_DIAMETER_MM = 25.0
NIPPLE_DIAMETER_FT = UnitUtils.ConvertToInternalUnits(NIPPLE_DIAMETER_MM, UnitTypeId.Millimeters)

# Maximum perpendicular XY distance (ft) to match a sprinkler to a pipe (~10 ft / 3 m)
MAX_XY_SEARCH_DIST_FT = 10.0

# Maximum longitudinal extension distance (ft) past an open/capped pipe end (~16.4 ft / 5 m)
MAX_PIPE_EXTENSION_FT = 16.4

# Distance threshold (ft) inside an open/capped pipe end to treat the sprinkler as an End Elbow (~0.5 ft / 150 mm)
PIPE_END_TOLERANCE_FT = 0.50

# Clearance threshold (ft) around an existing inline Reducer (~0.65 ft / 200 mm)
REDUCER_CONFLICT_DIST_FT = UnitUtils.ConvertToInternalUnits(200.0, UnitTypeId.Millimeters)

# Target clearance (ft) after shifting a conflicting Reducer toward the smaller pipe (~0.82 ft / 250 mm)
REDUCER_SHIFT_CLEARANCE_FT = UnitUtils.ConvertToInternalUnits(250.0, UnitTypeId.Millimeters)

# Minimum vertical distance (ft) required to create a vertical 25mm Nipple (~0.05 ft / 15 mm)
MIN_NIPPLE_HEIGHT_FT = 0.05

# Minimum distance from pipe endpoints required by PlumbingUtils.BreakCurve (~0.05 ft / 15 mm)
MIN_BREAK_DIST_FT = 0.05

# Fallback to direct Tee connection (without 25mm Nipple) if vertical clearance is too small for fittings
FALLBACK_DIRECT_FIT_WHEN_TOO_CLOSE = False


# ---------------------------------------------------------------------------
# Revit 2025 Compatibility Helpers
# ---------------------------------------------------------------------------
def get_id_value(element_id):
    """Returns 64-bit integer ID in Revit 2024/2025+ or 32-bit in older versions."""
    if element_id is None:
        return -1
    return element_id.Value if hasattr(element_id, "Value") else element_id.IntegerValue


def is_category(elem, builtin_cat):
    """Checks if an element belongs to the given BuiltInCategory."""
    if elem is None or elem.Category is None:
        return False
    return get_id_value(elem.Category.Id) == int(builtin_cat)


class SprinklerAndPipeSelectionFilter(ISelectionFilter):
    """Allows selecting Sprinklers, Pipes, and Pipe Fittings."""

    def AllowElement(self, elem):
        return (
            is_category(elem, BuiltInCategory.OST_Sprinklers)
            or is_category(elem, BuiltInCategory.OST_PipeCurves)
            or is_category(elem, BuiltInCategory.OST_PipeFitting)
        )

    def AllowReference(self, reference, position):
        return False


# ---------------------------------------------------------------------------
# Connector, Cap & Reducer Inspection Helpers
# ---------------------------------------------------------------------------
def get_piping_connectors(elem):
    """Returns a list of Piping Domain connectors from a FamilyInstance or MEPCurve."""
    connectors = []
    conn_mgr = None

    if isinstance(elem, FamilyInstance) and elem.MEPModel:
        conn_mgr = elem.MEPModel.ConnectorManager
    elif hasattr(elem, "ConnectorManager") and elem.ConnectorManager:
        conn_mgr = elem.ConnectorManager

    if conn_mgr is None:
        return connectors

    for conn in conn_mgr.Connectors:
        if conn.Domain == Domain.DomainPiping and (
            conn.ConnectorType == ConnectorType.End
            or conn.ConnectorType == ConnectorType.Curve
            or conn.ConnectorType == ConnectorType.Physical
        ):
            connectors.append(conn)
    return connectors


def is_sprinkler_already_connected(sprinkler):
    """Returns True if any piping connector on the sprinkler is already connected."""
    conns = get_piping_connectors(sprinkler)
    return any(c.IsConnected for c in conns)


def get_sprinkler_connector(sprinkler):
    """Returns the primary unconnected piping connector of a Sprinkler."""
    conns = get_piping_connectors(sprinkler)
    if not conns:
        return None
    for c in conns:
        if not c.IsConnected:
            return c
    return conns[0]


def get_pipe_diameter(pipe):
    """Returns the diameter (in internal ft) of a Pipe."""
    p = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
    if p:
        return p.AsDouble()
    conns = get_piping_connectors(pipe)
    if conns:
        return conns[0].Radius * 2.0
    return 0.0


def is_cap_fitting(elem):
    """Determines whether an element is a Pipe Cap fitting."""
    if not is_category(elem, BuiltInCategory.OST_PipeFitting):
        return False
    if isinstance(elem, FamilyInstance) and elem.MEPModel:
        try:
            if elem.MEPModel.PartType == PartType.Cap:
                return True
        except Exception:
            pass
    conns = get_piping_connectors(elem)
    return len(conns) == 1


def is_transition_reducer_fitting(elem):
    """Determines whether an element is a Pipe Reducer / Transition fitting."""
    if not is_category(elem, BuiltInCategory.OST_PipeFitting):
        return False
    conns = get_piping_connectors(elem)
    if len(conns) != 2:
        return False
    if isinstance(elem, FamilyInstance) and elem.MEPModel:
        try:
            if elem.MEPModel.PartType == PartType.Transition:
                return True
        except Exception:
            pass
    # Also detect by differing connector radii
    return abs(conns[0].Radius - conns[1].Radius) > 1e-5


def get_connected_cap_at_connector(pipe, end_conn):
    """If end_conn is connected to a Pipe Cap, returns the Cap element; otherwise None."""
    if end_conn is None or not end_conn.IsConnected:
        return None
    for ref_conn in end_conn.AllRefs:
        owner = ref_conn.Owner
        if owner is not None and get_id_value(owner.Id) != get_id_value(pipe.Id):
            if is_cap_fitting(owner):
                return owner
    return None


def is_pipe_end_open_or_capped(pipe, end_index):
    """
    Checks if endpoint `end_index` (0 or 1) of `pipe` is either unconnected
    or connected only to a removable Cap.
    Returns (is_extendable, end_connector, cap_element_or_None).
    """
    curve = pipe.Location.Curve
    end_pt = curve.GetEndPoint(end_index)

    best_conn = None
    min_dist = float("inf")
    for conn in get_piping_connectors(pipe):
        d = conn.Origin.DistanceTo(end_pt)
        if d < min_dist:
            min_dist = d
            best_conn = conn

    if best_conn is None:
        return (False, None, None)

    if not best_conn.IsConnected:
        return (True, best_conn, None)

    cap_elem = get_connected_cap_at_connector(pipe, best_conn)
    if cap_elem is not None:
        return (True, best_conn, cap_elem)

    return (False, best_conn, None)


# ---------------------------------------------------------------------------
# Reducer Conflict Resolution (Lengthening the Larger Diameter Pipe)
# ---------------------------------------------------------------------------
def get_reducer_and_adjacent_pipes(pipe, end_conn):
    """
    If `end_conn` on `pipe` is connected to a Reducer (Transition fitting),
    finds the Reducer and the two pipes (`large_pipe`, `small_pipe`) on either side.
    Returns (reducer_elem, large_pipe, small_pipe) or (None, None, None).
    """
    if end_conn is None or not end_conn.IsConnected:
        return (None, None, None)

    reducer_elem = None
    for ref_conn in end_conn.AllRefs:
        owner = ref_conn.Owner
        if owner is not None and get_id_value(owner.Id) != get_id_value(pipe.Id):
            if is_transition_reducer_fitting(owner):
                reducer_elem = owner
                break

    if reducer_elem is None:
        return (None, None, None)

    other_pipe = None
    for r_conn in get_piping_connectors(reducer_elem):
        for ref_c in r_conn.AllRefs:
            cand = ref_c.Owner
            if (
                cand is not None
                and get_id_value(cand.Id) != get_id_value(reducer_elem.Id)
                and get_id_value(cand.Id) != get_id_value(pipe.Id)
                and isinstance(cand, Pipe)
            ):
                other_pipe = cand
                break

    if other_pipe is None:
        return (None, None, None)

    d1 = get_pipe_diameter(pipe)
    d2 = get_pipe_diameter(other_pipe)
    if abs(d1 - d2) < 1e-5:
        return (None, None, None)

    if d1 > d2:
        return (reducer_elem, pipe, other_pipe)
    else:
        return (reducer_elem, other_pipe, pipe)


def shift_conflicting_reducer_to_lengthen_larger_pipe(doc, pipe, p_on_pipe, active_pipes):
    """
    Checks if `p_on_pipe` is too close to an inline Reducer at either end of `pipe`.
    If so, shifts the Reducer toward the SMALLER diameter pipe so that the LARGER
    diameter pipe increases in length, and returns the updated target pipe (`large_pipe`)
    and updated projection point on `large_pipe`.
    """
    if not pipe.IsValidObject:
        return pipe, p_on_pipe

    curve = pipe.Location.Curve
    p0 = curve.GetEndPoint(0)
    p1 = curve.GetEndPoint(1)

    for end_idx, end_pt in ((0, p0), (1, p1)):
        if p_on_pipe.DistanceTo(end_pt) > REDUCER_CONFLICT_DIST_FT:
            continue

        end_conn, _ = get_closest_connector_to_point(pipe, end_pt, only_unconnected=False)
        reducer_elem, large_pipe, small_pipe = get_reducer_and_adjacent_pipes(pipe, end_conn)
        if reducer_elem is None or large_pipe is None or small_pipe is None:
            continue

        # Ensure small_pipe is in active_pipes if not already
        if not any(get_id_value(p.Id) == get_id_value(small_pipe.Id) for p in active_pipes):
            active_pipes.append(small_pipe)
        if not any(get_id_value(p.Id) == get_id_value(large_pipe.Id) for p in active_pipes):
            active_pipes.append(large_pipe)

        # Determine the 3D axis vector pointing from large_pipe toward small_pipe
        l_curve = large_pipe.Location.Curve
        s_curve = small_pipe.Location.Curve
        red_pt = reducer_elem.Location.Point if reducer_elem.Location else end_pt

        l_p0, l_p1 = l_curve.GetEndPoint(0), l_curve.GetEndPoint(1)
        l_far = l_p0 if l_p0.DistanceTo(red_pt) > l_p1.DistanceTo(red_pt) else l_p1

        s_p0, s_p1 = s_curve.GetEndPoint(0), s_curve.GetEndPoint(1)
        s_far = s_p0 if s_p0.DistanceTo(red_pt) > s_p1.DistanceTo(red_pt) else s_p1

        axis_vec = s_far - l_far
        total_len = axis_vec.GetLength()
        if total_len < 1e-6:
            continue
        u_hat = axis_vec.Normalize()

        # Distance from l_far along u_hat
        d_sp = (p_on_pipe - l_far).DotProduct(u_hat)
        d_red = (red_pt - l_far).DotProduct(u_hat)
        d_s_far = (s_far - l_far).DotProduct(u_hat)

        # Target new position for the Reducer is past the sprinkler along u_hat
        target_d_red = max(d_sp, d_red) + REDUCER_SHIFT_CLEARANCE_FT
        shift_dist = target_d_red - d_red

        # Ensure small_pipe retains sufficient length after shrinking
        min_remaining_small_len = MIN_BREAK_DIST_FT * 4.0
        if shift_dist <= 1e-4 or (d_s_far - target_d_red) < min_remaining_small_len:
            continue

        shift_vec = u_hat.Multiply(shift_dist)

        # Method 1: Move the Reducer inline (Revit automatically stretches large_pipe & shrinks small_pipe)
        moved = False
        sub_move = SubTransaction(doc)
        sub_move.Start()
        try:
            ElementTransformUtils.MoveElement(doc, reducer_elem.Id, shift_vec)
            doc.Regenerate()
            sub_move.Commit()
            moved = True
        except Exception:
            if sub_move.HasStarted():
                sub_move.RollBack()

        # Method 2 (Fallback): Delete Reducer, extend large_pipe, shrink small_pipe, recreate Reducer
        if not moved:
            sub_rebuild = SubTransaction(doc)
            sub_rebuild.Start()
            try:
                l_near = l_p1 if l_far.IsAlmostEqualTo(l_p0) else l_p0
                s_near = s_p1 if s_far.IsAlmostEqualTo(s_p0) else s_p0
                doc.Delete(reducer_elem.Id)
                doc.Regenerate()

                new_l_near = l_near + shift_vec
                new_s_near = s_near + shift_vec

                if l_far.IsAlmostEqualTo(l_p0):
                    large_pipe.Location.Curve = Line.CreateBound(l_far, new_l_near)
                else:
                    large_pipe.Location.Curve = Line.CreateBound(new_l_near, l_far)

                if s_far.IsAlmostEqualTo(s_p1):
                    small_pipe.Location.Curve = Line.CreateBound(new_s_near, s_far)
                else:
                    small_pipe.Location.Curve = Line.CreateBound(s_far, new_s_near)

                doc.Regenerate()
                c_large, _ = get_closest_connector_to_point(large_pipe, new_l_near, only_unconnected=True)
                c_small, _ = get_closest_connector_to_point(small_pipe, new_s_near, only_unconnected=True)
                if c_large is not None and c_small is not None:
                    doc.Create.NewTransitionFitting(c_large, c_small)
                    doc.Regenerate()
                    sub_rebuild.Commit()
                    moved = True
                else:
                    sub_rebuild.RollBack()
            except Exception:
                if sub_rebuild.HasStarted():
                    sub_rebuild.RollBack()

        if moved:
            # Re-project onto large_pipe now that it covers the sprinkler location
            _, new_p_line, _, _, _ = project_xy_onto_pipe_unbounded(p_on_pipe, large_pipe)
            if new_p_line is not None:
                return large_pipe, new_p_line

    return pipe, p_on_pipe


# ---------------------------------------------------------------------------
# Unbounded Line Projection & Pipe Matching
# ---------------------------------------------------------------------------
def project_xy_onto_pipe_unbounded(point_xyz, pipe):
    """
    Projects a 3D point perpendicular onto a Pipe's infinite line in the XY plane.
    Supports both horizontal and sloped pipes (interpolating Z along the pipe slope).
    Returns:
      - perp_xy_dist: perpendicular horizontal distance to the pipe line
      - p_on_line: 3D point on the pipe's infinite centerline at t_raw
      - p_on_segment: 3D point clamped to [0, 1] on the pipe segment
      - t_raw: unclamped normalized parameter (< 0 before P0, [0,1] on pipe, > 1 past P1)
      - overhang_ft: longitudinal distance beyond the pipe segment (0.0 if 0 <= t_raw <= 1)
    """
    loc = pipe.Location
    if loc is None or not hasattr(loc, "Curve") or loc.Curve is None:
        return (float("inf"), None, None, 0.0, float("inf"))

    curve = loc.Curve
    p0 = curve.GetEndPoint(0)
    p1 = curve.GetEndPoint(1)

    vx = p1.X - p0.X
    vy = p1.Y - p0.Y
    len_sq_xy = vx * vx + vy * vy

    if len_sq_xy < 1e-10:
        # Vertical pipe: skip horizontal projection
        return (float("inf"), None, None, 0.0, float("inf"))

    len_xy = len_sq_xy ** 0.5
    wx = point_xyz.X - p0.X
    wy = point_xyz.Y - p0.Y

    t_raw = (wx * vx + wy * vy) / len_sq_xy
    t_clamped = max(0.0, min(1.0, t_raw))

    # Point on infinite line (pure perpendicular projection, zero shift along pipe length)
    lx = p0.X + t_raw * (p1.X - p0.X)
    ly = p0.Y + t_raw * (p1.Y - p0.Y)
    lz = p0.Z + t_raw * (p1.Z - p0.Z)
    p_on_line = XYZ(lx, ly, lz)

    # Point clamped to current segment
    cx = p0.X + t_clamped * (p1.X - p0.X)
    cy = p0.Y + t_clamped * (p1.Y - p0.Y)
    cz = p0.Z + t_clamped * (p1.Z - p0.Z)
    p_on_segment = XYZ(cx, cy, cz)

    dx = point_xyz.X - lx
    dy = point_xyz.Y - ly
    perp_xy_dist = (dx * dx + dy * dy) ** 0.5

    if t_raw < 0.0:
        overhang_ft = -t_raw * len_xy
    elif t_raw > 1.0:
        overhang_ft = (t_raw - 1.0) * len_xy
    else:
        overhang_ft = 0.0

    return (perp_xy_dist, p_on_line, p_on_segment, t_raw, overhang_ft)


def find_target_pipe_for_sprinkler(point_xyz, pipes, allow_extension=True):
    """
    Finds the best pipe for a sprinkler in the XY plane.
    Supports matching sprinklers that lie past an open/capped end of a pipe.
    """
    best_pipe = None
    best_score = float("inf")
    best_p_line = None
    best_t_raw = 0.0

    for pipe in pipes:
        if not pipe.IsValidObject:
            continue
        perp_dist, p_on_line, p_on_seg, t_raw, overhang_ft = project_xy_onto_pipe_unbounded(
            point_xyz, pipe
        )
        if p_on_line is None or perp_dist > MAX_XY_SEARCH_DIST_FT:
            continue

        # Check if sprinkler is beyond the pipe ends
        if t_raw < 0.0:
            can_ext, _, _ = is_pipe_end_open_or_capped(pipe, 0)
            if not (allow_extension and can_ext and overhang_ft <= MAX_PIPE_EXTENSION_FT):
                continue
        elif t_raw > 1.0:
            can_ext, _, _ = is_pipe_end_open_or_capped(pipe, 1)
            if not (allow_extension and can_ext and overhang_ft <= MAX_PIPE_EXTENSION_FT):
                continue

        # Score favors smallest perpendicular distance + slight penalty for overhang
        score = perp_dist + (0.25 * overhang_ft)
        if score < best_score:
            best_score = score
            best_pipe = pipe
            best_p_line = p_on_line
            best_t_raw = t_raw

    return best_pipe, best_p_line, best_t_raw


def get_closest_connector_to_point(elem, pt, only_unconnected=True):
    """Returns the connector on elem closest to pt."""
    best_conn = None
    min_dist = float("inf")
    for conn in get_piping_connectors(elem):
        if only_unconnected and conn.IsConnected:
            continue
        d = conn.Origin.DistanceTo(pt)
        if d < min_dist:
            min_dist = d
            best_conn = conn
    return best_conn, min_dist


def get_pipe_system_and_type_ids(pipe):
    """Extracts MEPSystemTypeId, PipeTypeId, and ReferenceLevelId from a Pipe."""
    pipe_type_id = pipe.GetTypeId()
    level_id = pipe.ReferenceLevel.Id if pipe.ReferenceLevel else ElementId.InvalidElementId

    sys_type_id = ElementId.InvalidElementId
    if pipe.MEPSystem is not None:
        sys_type_id = pipe.MEPSystem.GetTypeId()
    if sys_type_id == ElementId.InvalidElementId:
        sys_param = pipe.get_Parameter(BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
        if sys_param:
            sys_type_id = sys_param.AsElementId()

    return sys_type_id, pipe_type_id, level_id


# ---------------------------------------------------------------------------
# Elevation Lock & Perpendicular XY Alignment
# ---------------------------------------------------------------------------
def get_sprinkler_elevation_state(sprinkler):
    """Captures the Sprinkler's Z coordinate and Offset from Host so they never change."""
    pt_z = sprinkler.Location.Point.Z if sprinkler.Location else None
    offset_param = sprinkler.get_Parameter(BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
    orig_offset = offset_param.AsDouble() if (offset_param and not offset_param.IsReadOnly) else None
    return pt_z, orig_offset


def restore_sprinkler_elevation_state(doc, sprinkler, orig_z, orig_offset):
    """Ensures the Sprinkler's Elevation and Offset from Host remain 100% locked."""
    if orig_offset is not None:
        offset_param = sprinkler.get_Parameter(BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
        if offset_param and not offset_param.IsReadOnly:
            if abs(offset_param.AsDouble() - orig_offset) > 1e-6:
                offset_param.Set(orig_offset)

    if orig_z is not None and sprinkler.Location:
        curr_z = sprinkler.Location.Point.Z
        dz_drift = orig_z - curr_z
        if abs(dz_drift) > 1e-6:
            ElementTransformUtils.MoveElement(doc, sprinkler.Id, XYZ(0.0, 0.0, dz_drift))


def align_sprinkler_perpendicular_xy(doc, sprinkler, target_xy_pt):
    """
    Moves the sprinkler strictly perpendicular to the pipe in X and Y ONLY
    (never moving along the pipe length and strictly preserving Elevation & Offset from Host).
    """
    sp_conn = get_sprinkler_connector(sprinkler)
    ref_pt = sp_conn.Origin if sp_conn else sprinkler.Location.Point

    orig_z, orig_offset = get_sprinkler_elevation_state(sprinkler)

    dx = target_xy_pt.X - ref_pt.X
    dy = target_xy_pt.Y - ref_pt.Y

    if abs(dx) > 1e-6 or abs(dy) > 1e-6:
        translation = XYZ(dx, dy, 0.0)
        ElementTransformUtils.MoveElement(doc, sprinkler.Id, translation)

    restore_sprinkler_elevation_state(doc, sprinkler, orig_z, orig_offset)


# ---------------------------------------------------------------------------
# Pipe Extension & Cap Removal Before Connection
# ---------------------------------------------------------------------------
def extend_pipe_and_remove_caps_for_sprinklers(doc, pipe, sp_items):
    """
    Given a Pipe and the list of Sprinklers matched to it:
    - Checks if any sprinklers lie beyond endpoint 0 (t_raw < 0) or endpoint 1 (t_raw > 1),
      or within PIPE_END_TOLERANCE_FT of a capped end.
    - Deletes the Cap fitting automatically if present on that end.
    - Extends the pipe along its exact axis to reach the furthest sprinkler so the
      sprinkler does not move along the pipe length and connects at the end with an Elbow.
    """
    if not sp_items:
        return

    curve = pipe.Location.Curve
    p0 = curve.GetEndPoint(0)
    p1 = curve.GetEndPoint(1)

    new_p0 = p0
    new_p1 = p1

    # Check Endpoint 0 (t_raw < 0 or near t_raw == 0)
    min_item = min(sp_items, key=lambda x: x["t_raw"])
    can_ext_0, _, cap_0 = is_pipe_end_open_or_capped(pipe, 0)
    if can_ext_0:
        dist_to_p0 = min_item["p_line"].DistanceTo(p0)
        if min_item["t_raw"] < 0.0 or dist_to_p0 <= PIPE_END_TOLERANCE_FT:
            if cap_0 is not None and cap_0.IsValidObject:
                doc.Delete(cap_0.Id)
                doc.Regenerate()
            if min_item["t_raw"] < 0.0:
                new_p0 = min_item["p_line"]

    # Check Endpoint 1 (t_raw > 1 or near t_raw == 1)
    max_item = max(sp_items, key=lambda x: x["t_raw"])
    can_ext_1, _, cap_1 = is_pipe_end_open_or_capped(pipe, 1)
    if can_ext_1:
        dist_to_p1 = max_item["p_line"].DistanceTo(p1)
        if max_item["t_raw"] > 1.0 or dist_to_p1 <= PIPE_END_TOLERANCE_FT:
            if cap_1 is not None and cap_1.IsValidObject:
                doc.Delete(cap_1.Id)
                doc.Regenerate()
            if max_item["t_raw"] > 1.0:
                new_p1 = max_item["p_line"]

    # Apply extended curve if either endpoint moved
    if new_p0.DistanceTo(p0) > 1e-5 or new_p1.DistanceTo(p1) > 1e-5:
        if new_p0.DistanceTo(new_p1) > MIN_BREAK_DIST_FT:
            pipe.Location.Curve = Line.CreateBound(new_p0, new_p1)
            doc.Regenerate()


# ---------------------------------------------------------------------------
# 25mm Nipple Creation & Connection (Upright & Pendent, Branch & Main, Sloped)
# ---------------------------------------------------------------------------
def create_25mm_nipple(doc, pipe, p_on_pipe, sp_pt):
    """
    Creates a 25mm Nipple pipe between p_on_pipe and sp_pt (works for both
    Upright sprinklers above the pipe and Pendent sprinklers below the pipe).
    Returns (nipple_pipe, nipple_pipe_side_conn, nipple_sprinkler_side_conn).
    """
    sys_type_id, pipe_type_id, level_id = get_pipe_system_and_type_ids(pipe)

    nipple = Pipe.Create(doc, sys_type_id, pipe_type_id, level_id, p_on_pipe, sp_pt)

    # Set Nipple diameter strictly to 25mm
    diam_param = nipple.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
    if diam_param and not diam_param.IsReadOnly:
        diam_param.Set(NIPPLE_DIAMETER_FT)

    doc.Regenerate()

    nipple_pipe_conn, _ = get_closest_connector_to_point(nipple, p_on_pipe, only_unconnected=True)
    nipple_sp_conn, _ = get_closest_connector_to_point(nipple, sp_pt, only_unconnected=True)

    return nipple, nipple_pipe_conn, nipple_sp_conn


def connect_nipple_to_sprinkler(doc, nipple, sprinkler, orig_z, orig_offset):
    """
    Connects the sprinkler-side end of the 25mm Nipple to the Sprinkler connector
    (inserting a Reducer/Transition if sizes differ) while keeping Elevation locked.
    """
    doc.Regenerate()
    sp_conn = get_sprinkler_connector(sprinkler)
    if sp_conn is None or sp_conn.IsConnected:
        restore_sprinkler_elevation_state(doc, sprinkler, orig_z, orig_offset)
        return True

    nipple_sp_conn, _ = get_closest_connector_to_point(nipple, sp_conn.Origin, only_unconnected=True)
    if nipple_sp_conn is None:
        return False

    diam_diff = abs((nipple_sp_conn.Radius * 2.0) - (sp_conn.Radius * 2.0))

    connected = False
    if diam_diff > 1e-4:
        sub_trans = SubTransaction(doc)
        sub_trans.Start()
        try:
            doc.Create.NewTransitionFitting(nipple_sp_conn, sp_conn)
            sub_trans.Commit()
            connected = True
        except Exception:
            if sub_trans.HasStarted():
                sub_trans.RollBack()

    if not connected:
        try:
            nipple_sp_conn.ConnectTo(sp_conn)
            connected = True
        except Exception:
            connected = False

    doc.Regenerate()
    restore_sprinkler_elevation_state(doc, sprinkler, orig_z, orig_offset)
    return connected


def connect_at_pipe_end(doc, pipe, end_index, p_on_pipe, sprinkler, sp_conn, dz, orig_z, orig_offset):
    """
    Removes any Cap on `end_index`, trims/extends that open end to `p_on_pipe`,
    and connects the Sprinkler via a 25mm Nipple + Elbow (+ Reducer if pipe > 25mm).
    """
    can_ext, end_conn, cap_elem = is_pipe_end_open_or_capped(pipe, end_index)
    if not can_ext:
        return False

    if cap_elem is not None and cap_elem.IsValidObject:
        doc.Delete(cap_elem.Id)
        doc.Regenerate()

    # Adjust pipe endpoint to match p_on_pipe exactly so the 25mm Nipple is plumb
    loc_curve = pipe.Location.Curve
    p0 = loc_curve.GetEndPoint(0)
    p1 = loc_curve.GetEndPoint(1)
    if end_index == 0:
        if p0.DistanceTo(p_on_pipe) > 1e-5 and p1.DistanceTo(p_on_pipe) > MIN_BREAK_DIST_FT:
            pipe.Location.Curve = Line.CreateBound(p_on_pipe, p1)
    else:
        if p1.DistanceTo(p_on_pipe) > 1e-5 and p0.DistanceTo(p_on_pipe) > MIN_BREAK_DIST_FT:
            pipe.Location.Curve = Line.CreateBound(p0, p_on_pipe)

    doc.Regenerate()
    end_conn, _ = get_closest_connector_to_point(pipe, p_on_pipe, only_unconnected=True)
    if end_conn is None:
        return False

    # Attempt 1: 25mm Nipple + Elbow (+ automatic Reducer if pipe diameter != 25mm)
    if dz >= MIN_NIPPLE_HEIGHT_FT:
        sub = SubTransaction(doc)
        sub.Start()
        try:
            nipple, nipple_pipe_conn, _ = create_25mm_nipple(doc, pipe, p_on_pipe, sp_conn.Origin)
            doc.Create.NewElbowFitting(end_conn, nipple_pipe_conn)
            if connect_nipple_to_sprinkler(doc, nipple, sprinkler, orig_z, orig_offset):
                sub.Commit()
                return True
            sub.RollBack()
        except Exception:
            if sub.HasStarted():
                sub.RollBack()

    # Optional Fallback if vertical clearance is too tight for a 25mm Nipple
    if FALLBACK_DIRECT_FIT_WHEN_TOO_CLOSE:
        sub_direct = SubTransaction(doc)
        sub_direct.Start()
        try:
            doc.Create.NewElbowFitting(end_conn, sp_conn)
            doc.Regenerate()
            restore_sprinkler_elevation_state(doc, sprinkler, orig_z, orig_offset)
            sub_direct.Commit()
            return True
        except Exception:
            if sub_direct.HasStarted():
                sub_direct.RollBack()

    return False


def connect_along_pipe_middle(doc, pipe, p_on_pipe, sprinkler, sp_conn, dz, active_pipes, orig_z, orig_offset):
    """
    Connects an Upright or Pendent Sprinkler along the body of a Branch or Main pipe
    (horizontal or sloped) using a 25mm Nipple + Tee + Reducer (or Takeoff).
    """
    if dz >= MIN_NIPPLE_HEIGHT_FT:
        # Attempt 1: 25mm Nipple + BreakCurve + NewTeeFitting (creates Tee + Reducer automatically)
        sub = SubTransaction(doc)
        sub.Start()
        try:
            nipple, nipple_pipe_conn, _ = create_25mm_nipple(doc, pipe, p_on_pipe, sp_conn.Origin)
            new_pipe_id = PlumbingUtils.BreakCurve(doc, pipe.Id, p_on_pipe)
            new_pipe = doc.GetElement(new_pipe_id)
            doc.Regenerate()

            c1, _ = get_closest_connector_to_point(pipe, p_on_pipe, only_unconnected=True)
            c2, _ = get_closest_connector_to_point(new_pipe, p_on_pipe, only_unconnected=True)

            if c1 is not None and c2 is not None and nipple_pipe_conn is not None:
                doc.Create.NewTeeFitting(c1, c2, nipple_pipe_conn)
                if connect_nipple_to_sprinkler(doc, nipple, sprinkler, orig_z, orig_offset):
                    sub.Commit()
                    active_pipes.append(new_pipe)
                    return True
            sub.RollBack()
        except Exception:
            if sub.HasStarted():
                sub.RollBack()

        # Attempt 2: 25mm Nipple + NewTakeoffFitting (for Tap / Mechanical Tee or sloped pipes)
        sub_tap = SubTransaction(doc)
        sub_tap.Start()
        try:
            nipple, nipple_pipe_conn, _ = create_25mm_nipple(doc, pipe, p_on_pipe, sp_conn.Origin)
            doc.Create.NewTakeoffFitting(nipple_pipe_conn, pipe)
            if connect_nipple_to_sprinkler(doc, nipple, sprinkler, orig_z, orig_offset):
                sub_tap.Commit()
                return True
            sub_tap.RollBack()
        except Exception:
            if sub_tap.HasStarted():
                sub_tap.RollBack()

    # Optional Fallback if vertical clearance is too tight for a 25mm Nipple
    if FALLBACK_DIRECT_FIT_WHEN_TOO_CLOSE:
        sub_direct = SubTransaction(doc)
        sub_direct.Start()
        try:
            new_pipe_id = PlumbingUtils.BreakCurve(doc, pipe.Id, p_on_pipe)
            new_pipe = doc.GetElement(new_pipe_id)
            doc.Regenerate()

            c1, _ = get_closest_connector_to_point(pipe, p_on_pipe, only_unconnected=True)
            c2, _ = get_closest_connector_to_point(new_pipe, p_on_pipe, only_unconnected=True)

            if c1 is not None and c2 is not None:
                doc.Create.NewTeeFitting(c1, c2, sp_conn)
                doc.Regenerate()
                restore_sprinkler_elevation_state(doc, sprinkler, orig_z, orig_offset)
                sub_direct.Commit()
                active_pipes.append(new_pipe)
                return True
            sub_direct.RollBack()
        except Exception:
            if sub_direct.HasStarted():
                sub_direct.RollBack()

    return False


# ---------------------------------------------------------------------------
# Main Execution Entry Point
# ---------------------------------------------------------------------------
def main():
    # 1. Get user selection (support both pre-selection and interactive prompt)
    selected_ids = list(uidoc.Selection.GetElementIds())
    elements = [doc.GetElement(eid) for eid in selected_ids if doc.GetElement(eid) is not None]

    all_sprinklers = [e for e in elements if is_category(e, BuiltInCategory.OST_Sprinklers)]
    pipes = [e for e in elements if is_category(e, BuiltInCategory.OST_PipeCurves) and isinstance(e, Pipe)]

    if not all_sprinklers or not pipes:
        try:
            refs = uidoc.Selection.PickObjects(
                ObjectType.Element,
                SprinklerAndPipeSelectionFilter(),
                "Select Sprinklers (Upright/Pendent) and Firefighting Pipes, then click Finish",
            )
            elements = [doc.GetElement(r.ElementId) for r in refs]
            all_sprinklers = [e for e in elements if is_category(e, BuiltInCategory.OST_Sprinklers)]
            pipes = [e for e in elements if is_category(e, BuiltInCategory.OST_PipeCurves) and isinstance(e, Pipe)]
        except Exception:
            return

    if not all_sprinklers or not pipes:
        dui.alert(TOOL_TITLE, "Please select at least one Sprinkler and one Firefighting Pipe.",
                  "warning", "Nothing to connect")
        return

    # Filter out sprinklers that are already connected so they are never moved or altered
    sprinklers = []
    skipped_connected_count = 0
    for sp in all_sprinklers:
        if is_sprinkler_already_connected(sp):
            skipped_connected_count += 1
        else:
            sprinklers.append(sp)

    if not sprinklers:
        dui.show_result(TOOL_TITLE, "success", "All sprinklers are already connected",
                        stats=[(str(skipped_connected_count), "Already connected")])
        return

    active_pipes = list(pipes)
    aligned_count = 0
    connected_count = 0
    failed_element_ids = []

    t = Transaction(doc, "Align & Connect Sprinklers (25mm Nipple)")
    t.Start()

    try:
        # Phase 1: Match each unconnected Sprinkler to its target Pipe (unbounded XY projection)
        pipe_groups = {}

        for sp in sprinklers:
            sp_conn = get_sprinkler_connector(sp)
            if sp_conn is None:
                failed_element_ids.append(sp.Id)
                continue

            orig_z, orig_offset = get_sprinkler_elevation_state(sp)
            target_pipe, p_line, t_raw = find_target_pipe_for_sprinkler(
                sp_conn.Origin, active_pipes, allow_extension=True
            )

            if target_pipe is None or p_line is None:
                failed_element_ids.append(sp.Id)
                continue

            # Move Sprinkler strictly perpendicular to the pipe's line (zero shift along pipe length)
            align_sprinkler_perpendicular_xy(doc, sp, p_line)
            aligned_count += 1

            rec = {
                "sprinkler": sp,
                "pipe": target_pipe,
                "p_line": p_line,
                "t_raw": t_raw,
                "orig_z": orig_z,
                "orig_offset": orig_offset,
            }
            pid = get_id_value(target_pipe.Id)
            pipe_groups.setdefault(pid, []).append(rec)

        doc.Regenerate()

        # Phase 2: Sort sprinklers along each pipe & extend open/capped Pipe ends / remove Caps
        ordered_records = []
        for pid, group_items in pipe_groups.items():
            # Sort sequentially along the pipe axis by t_raw
            group_items.sort(key=lambda x: x["t_raw"])
            pipe_elem = group_items[0]["pipe"]
            if pipe_elem and pipe_elem.IsValidObject:
                extend_pipe_and_remove_caps_for_sprinklers(doc, pipe_elem, group_items)

            # Separate middle (Tee) sprinklers and end (Elbow) sprinklers so middle splits run in order first
            middle_items = []
            end_items = []
            for item in group_items:
                if item["t_raw"] <= 0.01 or item["t_raw"] >= 0.99:
                    end_items.append(item)
                else:
                    middle_items.append(item)
            ordered_records.extend(middle_items + end_items)

        doc.Regenerate()

        # Phase 3: Connect each Sprinkler (with automatic Reducer shifting to lengthen larger pipe)
        for rec in ordered_records:
            sp = rec["sprinkler"]
            orig_z = rec["orig_z"]
            orig_offset = rec["orig_offset"]

            sp_conn = get_sprinkler_connector(sp)
            if sp_conn is None or sp_conn.IsConnected:
                continue

            # Find current pipe segment directly under/above the sprinkler
            target_pipe, p_on_pipe, _ = find_target_pipe_for_sprinkler(
                sp_conn.Origin, active_pipes, allow_extension=False
            )
            if target_pipe is None or p_on_pipe is None:
                failed_element_ids.append(sp.Id)
                continue

            # Check if sprinkler projection conflicts with an inline Reducer -> Shift Reducer to lengthen larger pipe
            target_pipe, p_on_pipe = shift_conflicting_reducer_to_lengthen_larger_pipe(
                doc, target_pipe, p_on_pipe, active_pipes
            )

            dz = abs(sp_conn.Origin.Z - p_on_pipe.Z)

            loc_curve = target_pipe.Location.Curve
            p0 = loc_curve.GetEndPoint(0)
            p1 = loc_curve.GetEndPoint(1)
            dist_to_p0 = p_on_pipe.DistanceTo(p0)
            dist_to_p1 = p_on_pipe.DistanceTo(p1)

            can_ext_0, _, _ = is_pipe_end_open_or_capped(target_pipe, 0)
            can_ext_1, _, _ = is_pipe_end_open_or_capped(target_pipe, 1)

            connected = False

            # Check if this sprinkler sits at an open/capped end of the pipe -> Connect with Elbow + 25mm Nipple
            if can_ext_0 and dist_to_p0 <= PIPE_END_TOLERANCE_FT and dist_to_p0 <= dist_to_p1:
                connected = connect_at_pipe_end(
                    doc, target_pipe, 0, p_on_pipe, sp, sp_conn, dz, orig_z, orig_offset
                )
            elif can_ext_1 and dist_to_p1 <= PIPE_END_TOLERANCE_FT:
                connected = connect_at_pipe_end(
                    doc, target_pipe, 1, p_on_pipe, sp, sp_conn, dz, orig_z, orig_offset
                )

            # Otherwise connect along the body of the pipe -> Tee + Reducer + 25mm Nipple
            loc_curve = target_pipe.Location.Curve
            dist_to_p0 = p_on_pipe.DistanceTo(loc_curve.GetEndPoint(0))
            dist_to_p1 = p_on_pipe.DistanceTo(loc_curve.GetEndPoint(1))
            can_break = (dist_to_p0 > MIN_BREAK_DIST_FT) and (dist_to_p1 > MIN_BREAK_DIST_FT)

            if not connected and can_break:
                connected = connect_along_pipe_middle(
                    doc, target_pipe, p_on_pipe, sp, sp_conn, dz, active_pipes, orig_z, orig_offset
                )

            if connected:
                connected_count += 1
            else:
                failed_element_ids.append(sp.Id)

        t.Commit()

        # Auto-select / highlight failed sprinklers in the Revit UI for immediate visual inspection
        if failed_element_ids:
            try:
                net_id_list = List[ElementId](failed_element_ids)
                uidoc.Selection.SetElementIds(net_id_list)
            except Exception:
                pass

        # Summary card: green = all connected, amber = some failed, red = none connected
        total = len(sprinklers)
        details = []
        if skipped_connected_count > 0:
            details.append("{0} sprinkler(s) were already connected and left untouched."
                           .format(skipped_connected_count))
        if failed_element_ids:
            failed_str = ", ".join(str(get_id_value(eid)) for eid in failed_element_ids)
            details.append("Not connected (now selected in your view): " + failed_str)

        if connected_count == 0:
            status, headline = "error", "No sprinklers were connected"
        elif failed_element_ids:
            status, headline = "warning", "Connected, with some sprinklers left"
        else:
            status, headline = "success", "All sprinklers aligned and connected"

        dui.show_result(
            TOOL_TITLE, status, headline,
            stats=[("{0}/{1}".format(connected_count, total), "Connected"),
                   ("{0}/{1}".format(aligned_count, total), "Aligned"),
                   (str(len(failed_element_ids)), "Failed")],
            details=details)

    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        dui.alert(TOOL_TITLE, "Nothing was changed. " + str(ex), "error", "The tool stopped with an error")


if __name__ == "__main__":
    main()