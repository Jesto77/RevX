# -*- coding: utf-8 -*-
"""
FloorToFilledRegion.py  (v7 - per-element transactions + arc tessellation)
-------------------------------------------------------------------------------
PyRevit Script: Creates filled regions from the topmost compound-structure
layer of Floors, Roofs, Ceilings, and Toposolids.

FilledRegionType naming rule:
  "<ElementTypeName> (Layout)"
  e.g.  "LA_Paving type-IF1"  ->  "LA_Paving type-IF1 (Layout)"

Compatible: Revit 2018-2027+  |  IronPython 2.7 (PyRevit)

Changes in v7 (vs v6):
  - Each element now processed in its OWN sub-transaction so a Revit
    sketch-constraint failure on one floor cannot crash all others
  - Arc/curve tessellation fallback: if a loop with arcs causes a
    constraint error, arcs are converted to short line segments
    (tessellate_loop_to_lines) and retried - fixes curved floors in Revit 2026
  - Three-tier placement strategy per element:
      Tier 1: all loops together with original curves
      Tier 2: all loops together with arcs tessellated to lines
      Tier 3: each loop individually (tessellated) as last resort
"""

__title__  = "Surface Pattern Region"
__author__  = "PyRevit"
__doc__    = (
    "Select Floors, Roofs, Ceilings or Toposolids. "
    "Creates a FilledRegion named '<TypeName> (Layout)' matching "
    "the topmost layer surface foreground + background patterns."
)

import sys
import traceback

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    Floor, RoofBase, Ceiling,
    FilledRegionType, FilledRegion,
    CurveLoop, Transaction, ElementId, Options,
)

# ISelectionFilter / ObjectType - handle both old and new import paths
try:
    from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
except ImportError:
    try:
        from Autodesk.Revit.UI import ISelectionFilter, ObjectType
    except ImportError:
        from Autodesk.Revit.UI.Selection import ObjectType
        ISelectionFilter = object  # fallback stub

from Autodesk.Revit.Exceptions import OperationCanceledException

try:
    from Autodesk.Revit.DB import Toposolid
    HAS_TOPOSOLID = True
except ImportError:
    HAS_TOPOSOLID = False

from pyrevit import forms, revit

doc   = revit.doc
uidoc = revit.uidoc
view  = doc.ActiveView

SUFFIX = " (Layout)"

# ---------------------------------------------------------------------------
# Revit version detection
# ---------------------------------------------------------------------------
try:
    _ver_str = doc.Application.VersionNumber          # e.g. "2026"
    REVIT_VERSION = int(_ver_str)
except Exception:
    REVIT_VERSION = 2024   # safe fallback


# =============================================================================
# COMPAT HELPER: ElementId integer value
# IntegerValue was deprecated in 2024 and removed in 2026.
# Use .Value (Python int/long) when available.
# =============================================================================

def element_id_to_int(eid):
    """Return the integer value of an ElementId regardless of Revit version."""
    try:
        return int(eid.Value)          # Revit 2024+
    except AttributeError:
        try:
            return int(eid.IntegerValue)   # Revit 2018-2025
        except Exception:
            return -1


# =============================================================================
# TYPE NAME EXTRACTION
# =============================================================================

def get_type_name(element):
    """
    Return the Type name of a system-family element.
    Tries multiple API paths so it works across all Revit versions.
    """

    # 1. ElementType.Name
    try:
        etype = doc.GetElement(element.GetTypeId())
        if etype is not None:
            n = etype.Name
            if n and n.strip():
                return n.strip()
    except Exception:
        pass

    # 2. ALL_MODEL_TYPE_NAME on the type
    try:
        etype = doc.GetElement(element.GetTypeId())
        if etype is not None:
            p = etype.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
            if p is not None:
                v = p.AsString()
                if v and v.strip():
                    return v.strip()
    except Exception:
        pass

    # 3. SYMBOL_NAME_PARAM on the type
    try:
        etype = doc.GetElement(element.GetTypeId())
        if etype is not None:
            p = etype.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
            if p is not None:
                v = p.AsString()
                if v and v.strip():
                    return v.strip()
    except Exception:
        pass

    # 4. ELEM_TYPE_PARAM on the instance
    try:
        p = element.get_Parameter(DB.BuiltInParameter.ELEM_TYPE_PARAM)
        if p is not None:
            v = p.AsString()
            if v and v.strip():
                return v.strip()
    except Exception:
        pass

    # 5. SYMBOL_NAME_PARAM on the instance
    try:
        p = element.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
        if p is not None:
            v = p.AsString()
            if v and v.strip():
                return v.strip()
    except Exception:
        pass

    # 6. element.Name
    try:
        n = element.Name
        if n and n.strip() and not n.strip().isdigit():
            return n.strip()
    except Exception:
        pass

    # 7. Absolute fallback - use compat helper instead of IntegerValue
    return "UnknownType_{}".format(element_id_to_int(element.Id))


# =============================================================================
# ELEMENT SUPPORT CHECK
# =============================================================================

def is_supported(element):
    if element is None:
        return False
    if isinstance(element, (Floor, RoofBase, Ceiling)):
        return True
    if HAS_TOPOSOLID and isinstance(element, Toposolid):
        return True
    return False


# =============================================================================
# TOP LAYER MATERIAL
# =============================================================================

def get_top_layer_material(element):
    """
    Walk CompoundStructure and return the Material of the topmost
    exterior/finish layer that has a valid material assigned.
    Falls back to element material parameters for Toposolids.
    """
    try:
        etype = doc.GetElement(element.GetTypeId())
        if etype is not None:
            cs = etype.GetCompoundStructure()
            if cs is not None:
                layers = list(cs.GetLayers())
                if layers:
                    try:
                        first_core = cs.GetFirstCoreLayerIndex()
                    except Exception:
                        first_core = 0

                    if first_core > 0:
                        order = list(range(first_core)) + list(range(first_core, len(layers)))
                    else:
                        order = list(range(len(layers)))

                    for idx in order:
                        lyr = layers[idx]
                        try:
                            mid = lyr.MaterialId
                            if element_id_to_int(mid) != element_id_to_int(ElementId.InvalidElementId):
                                mat = doc.GetElement(mid)
                                if mat is not None:
                                    return mat
                        except Exception:
                            continue
    except Exception:
        pass

    # Fallback for Toposolid / no compound structure
    for bip in [
        DB.BuiltInParameter.MATERIAL_ID_PARAM,
        DB.BuiltInParameter.STRUCTURAL_MATERIAL_PARAM,
    ]:
        try:
            p = element.get_Parameter(bip)
            if p is not None:
                mid = p.AsElementId()
                if mid is not None and element_id_to_int(mid) != element_id_to_int(ElementId.InvalidElementId):
                    mat = doc.GetElement(mid)
                    if mat is not None:
                        return mat
        except Exception:
            continue

    return None


# =============================================================================
# SURFACE PATTERN EXTRACTION
# =============================================================================

def get_surface_pattern(material, foreground=True):
    """Return (FillPatternElement or None, Color or None)."""
    if material is None:
        return None, None

    # Revit 2019+ dual-pattern API
    try:
        if foreground:
            pid   = material.SurfaceForegroundPatternId
            color = material.SurfaceForegroundPatternColor
        else:
            pid   = material.SurfaceBackgroundPatternId
            color = material.SurfaceBackgroundPatternColor
        invalid = element_id_to_int(ElementId.InvalidElementId)
        pat = doc.GetElement(pid) if element_id_to_int(pid) != invalid else None
        return pat, color
    except AttributeError:
        pass

    # Revit 2018 single-pattern API
    try:
        pid   = material.SurfacePatternId
        color = material.SurfacePatternColor
        invalid = element_id_to_int(ElementId.InvalidElementId)
        pat   = doc.GetElement(pid) if element_id_to_int(pid) != invalid else None
        if foreground:
            return pat, color
        return None, None
    except Exception:
        return None, None


# =============================================================================
# BOUNDARY EXTRACTION
# =============================================================================

def _from_get_profile(element):
    """GetProfile() - present on some element types, removed/changed in 2026+."""
    # Only attempt on Revit versions where it's reliable
    if REVIT_VERSION >= 2026:
        return []
    try:
        p = element.GetProfile()
        if p and len(p) > 0:
            return list(p)
    except Exception:
        pass
    return []


def _from_sketch(element):
    """
    Extract boundary from the element's Sketch.
    SketchId property was removed in Revit 2026; use GetSketchId() or
    fall back to SubElements / SpanDirection API.
    """
    loops = []

    # --- Method A: SketchId property (Revit 2018-2025) ---
    sketch = None
    try:
        sid = element.SketchId
        if element_id_to_int(sid) != element_id_to_int(ElementId.InvalidElementId):
            sketch = doc.GetElement(sid)
    except AttributeError:
        pass   # property gone in 2026+
    except Exception:
        pass

    # --- Method B: GetSketchId() method (Revit 2026+) ---
    if sketch is None:
        try:
            sid = element.GetSketchId()
            if element_id_to_int(sid) != element_id_to_int(ElementId.InvalidElementId):
                sketch = doc.GetElement(sid)
        except AttributeError:
            pass
        except Exception:
            pass

    # --- Method C: look for sketch via dependents (last resort) ---
    if sketch is None:
        try:
            dep_ids = element.GetDependentElements(None)
            for dep_id in dep_ids:
                dep = doc.GetElement(dep_id)
                if dep is not None and dep.GetType().Name == "Sketch":
                    sketch = dep
                    break
        except Exception:
            pass

    if sketch is None:
        return loops

    # Pull loops from sketch Profile
    try:
        for arr in sketch.Profile:
            cl = CurveLoop()
            for c in arr:
                cl.Append(c)
            loops.append(cl)
    except Exception:
        pass

    return loops


def _find_best_face(solid, upward):
    """
    Return the topmost (upward=True) or bottommost (upward=False) face
    whose normal is within 5 degrees of vertical.
    """
    best_z    = None
    best_face = None
    try:
        for face in solid.Faces:
            try:
                n = face.ComputeNormal(DB.UV(0.5, 0.5))
                target = 1.0 if upward else -1.0
                if abs(n.Z - target) < 0.09:   # ~5 degrees
                    bb  = face.GetBoundingBox()
                    mid = face.Evaluate(
                        DB.UV(
                            (bb.Min.U + bb.Max.U) * 0.5,
                            (bb.Min.V + bb.Max.V) * 0.5
                        )
                    )
                    z = mid.Z
                    if best_z is None:
                        best_z    = z
                        best_face = face
                    elif upward and z > best_z:
                        best_z    = z
                        best_face = face
                    elif not upward and z < best_z:
                        best_z    = z
                        best_face = face
            except Exception:
                continue
    except Exception:
        pass
    return best_face


def _from_geometry(element, upward=True):
    """Extract boundary loops from solid geometry - works on all versions."""
    loops     = []
    best_z    = None
    best_face = None

    try:
        opts = Options()
        opts.ComputeReferences = False
        # In Revit 2026+ the default detail level may differ; set explicitly
        try:
            opts.DetailLevel = DB.ViewDetailLevel.Fine
        except Exception:
            pass

        geom = element.get_Geometry(opts)
        if geom is None:
            return loops

        for obj in geom:
            if obj is None:
                continue

            # Collect solid candidates
            solids = []
            try:
                inst_geom = obj.GetInstanceGeometry()
                if inst_geom is not None:
                    solids = [s for s in inst_geom if s is not None]
            except AttributeError:
                pass

            if not solids:
                # obj itself may be a Solid
                try:
                    if obj.Volume > 0:
                        solids = [obj]
                except Exception:
                    solids = [obj]

            for solid in solids:
                face = _find_best_face(solid, upward)
                if face is not None:
                    try:
                        bb  = face.GetBoundingBox()
                        mid = face.Evaluate(
                            DB.UV(
                                (bb.Min.U + bb.Max.U) * 0.5,
                                (bb.Min.V + bb.Max.V) * 0.5
                            )
                        )
                        z = mid.Z
                        if best_z is None:
                            best_z    = z
                            best_face = face
                        elif upward and z > best_z:
                            best_z    = z
                            best_face = face
                        elif not upward and z < best_z:
                            best_z    = z
                            best_face = face
                    except Exception:
                        pass

        if best_face is not None:
            for lp in best_face.GetEdgesAsCurveLoops():
                loops.append(lp)

    except Exception:
        pass
    return loops


def get_boundary_loops(element):
    upward = not isinstance(element, Ceiling)

    loops = _from_get_profile(element)
    if loops:
        return loops

    loops = _from_sketch(element)
    if loops:
        return loops

    loops = _from_geometry(element, upward)
    return loops


# =============================================================================
# LOOP SANITISATION
# Revit's FilledRegion.Create() is strict:
#   - Every CurveLoop must be closed (end of last curve == start of first)
#   - No duplicate / zero-length curves
#   - Curves must be co-planar with the view's sketch plane
#   - No self-intersections
# Floors with openings, sloped edges, or imported geometry often violate these.
# =============================================================================

# Revit's internal tolerance is ~1/16" = 0.00521 ft.
# We use a slightly larger snap tolerance for gap closing.
CURVE_MIN_LENGTH = 0.001      # feet  – curves shorter than this are degenerate
GAP_TOLERANCE    = 0.01       # feet  – max gap to snap when rebuilding a loop


def _pt_key(pt, tol=0.001):
    """Round a point to a grid so nearby points hash the same."""
    scale = 1.0 / tol
    return (
        int(round(pt.X * scale)),
        int(round(pt.Y * scale)),
        int(round(pt.Z * scale)),
    )


def _pts_equal(a, b, tol=GAP_TOLERANCE):
    d = a - b
    return (d.X * d.X + d.Y * d.Y + d.Z * d.Z) ** 0.5 < tol


def _flatten_curve(curve, z_ref):
    """
    Project a curve onto the horizontal plane at z_ref.
    Returns a new curve or the original if already planar / projection fails.
    Handles Line and Arc; other types are left unchanged.
    """
    try:
        sp = curve.GetEndPoint(0)
        ep = curve.GetEndPoint(1)
        if abs(sp.Z - z_ref) < 0.0001 and abs(ep.Z - z_ref) < 0.0001:
            return curve   # already on plane - fast path

        sp2 = DB.XYZ(sp.X, sp.Y, z_ref)
        ep2 = DB.XYZ(ep.X, ep.Y, z_ref)

        if isinstance(curve, DB.Line):
            if _pts_equal(sp2, ep2, CURVE_MIN_LENGTH):
                return None   # collapses to a point
            return DB.Line.CreateBound(sp2, ep2)

        if isinstance(curve, DB.Arc):
            mid_param = (curve.GetEndParameter(0) + curve.GetEndParameter(1)) * 0.5
            mp  = curve.Evaluate(mid_param, False)
            mp2 = DB.XYZ(mp.X, mp.Y, z_ref)
            try:
                return DB.Arc.Create(sp2, ep2, mp2)
            except Exception:
                # Arc degenerates; replace with a line
                if not _pts_equal(sp2, ep2, CURVE_MIN_LENGTH):
                    return DB.Line.CreateBound(sp2, ep2)
                return None

    except Exception:
        pass
    return curve


def _remove_short_curves(curves):
    """Drop curves shorter than CURVE_MIN_LENGTH."""
    good = []
    for c in curves:
        try:
            if c.Length >= CURVE_MIN_LENGTH:
                good.append(c)
        except Exception:
            good.append(c)
    return good


def _remove_duplicates(curves):
    """
    Remove curves whose (start_key, end_key) pair already appeared
    (checks both orientations).
    """
    seen = set()
    good = []
    for c in curves:
        try:
            sk = _pt_key(c.GetEndPoint(0))
            ek = _pt_key(c.GetEndPoint(1))
            key_fwd = (sk, ek)
            key_rev = (ek, sk)
            if key_fwd not in seen and key_rev not in seen:
                seen.add(key_fwd)
                good.append(c)
        except Exception:
            good.append(c)
    return good


def _chain_curves(curves):
    """
    Try to form one or more closed CurveLoops from an unordered list of curves.
    Uses a greedy next-segment search with GAP_TOLERANCE snapping.
    Returns a list of CurveLoop objects (only the closed ones).
    """
    remaining = list(curves)
    closed_loops = []

    while remaining:
        # Start a new chain with the first remaining curve
        chain  = [remaining.pop(0)]
        changed = True

        while changed:
            changed = False
            tail = chain[-1].GetEndPoint(1)

            for i, cand in enumerate(remaining):
                sp = cand.GetEndPoint(0)
                ep = cand.GetEndPoint(1)

                if _pts_equal(tail, sp):
                    chain.append(remaining.pop(i))
                    changed = True
                    break
                if _pts_equal(tail, ep):
                    # append reversed
                    try:
                        chain.append(cand.CreateReversed())
                    except Exception:
                        chain.append(cand)
                    remaining.pop(i)
                    changed = True
                    break

        # Check if chain closes
        if len(chain) >= 3:
            head = chain[0].GetEndPoint(0)
            tail = chain[-1].GetEndPoint(1)
            if _pts_equal(head, tail):
                cl = CurveLoop()
                ok = True
                for c in chain:
                    try:
                        cl.Append(c)
                    except Exception:
                        ok = False
                        break
                if ok:
                    closed_loops.append(cl)

    return closed_loops


def _extract_curves_from_loop(loop):
    """Iterate a CurveLoop and return its curves as a plain list."""
    curves = []
    try:
        it = loop.GetEnumerator()
        while it.MoveNext():
            curves.append(it.Current)
    except Exception:
        try:
            for c in loop:
                curves.append(c)
        except Exception:
            pass
    return curves


def sanitize_loops(raw_loops, view_normal_z=1.0):
    """
    Given a list of CurveLoop objects (from any extraction method) return
    a cleaned list ready for FilledRegion.Create().

    Strategy:
      1. Collect all curves from all loops into one pool.
      2. Flatten to the view plane (z = 0 in view coords; we use z=0 for
         drafting / use the average Z of the raw curves for model views).
      3. Drop degenerate and duplicate curves.
      4. Re-chain into closed loops.
      5. Return only valid closed loops (>= 3 segments).

    If re-chaining fails we fall back to returning the raw loops as-is
    (original behaviour) so we never make things worse.
    """
    if not raw_loops:
        return raw_loops

    # Step 1: collect all curves
    all_curves = []
    for loop in raw_loops:
        all_curves.extend(_extract_curves_from_loop(loop))

    if not all_curves:
        return raw_loops

    # Step 2: determine reference Z (average of all endpoints)
    try:
        zs = []
        for c in all_curves:
            try:
                zs.append(c.GetEndPoint(0).Z)
                zs.append(c.GetEndPoint(1).Z)
            except Exception:
                pass
        z_ref = sum(zs) / len(zs) if zs else 0.0
    except Exception:
        z_ref = 0.0

    # Step 3: flatten
    flat = []
    for c in all_curves:
        fc = _flatten_curve(c, z_ref)
        if fc is not None:
            flat.append(fc)

    # Step 4: clean
    flat = _remove_short_curves(flat)
    flat = _remove_duplicates(flat)

    if not flat:
        return raw_loops   # nothing survived - return originals

    # Step 5: re-chain
    closed = _chain_curves(flat)

    if closed:
        return closed

    # Fallback: raw loops unchanged
    return raw_loops


# =============================================================================
# ARC TESSELLATION
# Revit 2026 rejects arcs in FilledRegion sketch loops when the arc was
# extracted from 3D geometry (the constraint solver cannot resolve the
# implicit tangent constraints). Fix: approximate arcs as short lines.
# =============================================================================

ARC_TESSELLATION_SEGMENTS = 32   # segments per full circle; arc gets proportional share


def tessellate_curve(curve, segments=ARC_TESSELLATION_SEGMENTS):
    """
    Convert a single curve to a list of DB.Line segments.
    Lines are returned as-is (one-element list).
    Arcs, ellipses, splines etc. are sampled into straight segments.
    """
    try:
        if isinstance(curve, DB.Line):
            return [curve]
    except Exception:
        pass

    lines = []
    try:
        t0 = curve.GetEndParameter(0)
        t1 = curve.GetEndParameter(1)

        # For arcs: scale segment count by arc fraction of full circle
        try:
            if isinstance(curve, DB.Arc):
                import math
                arc_angle = abs(t1 - t0)          # radians
                n = max(3, int(round(segments * arc_angle / (2.0 * math.pi))))
            else:
                n = segments
        except Exception:
            n = segments

        pts = []
        for i in range(n + 1):
            t = t0 + (t1 - t0) * i / float(n)
            try:
                pts.append(curve.Evaluate(t, False))
            except Exception:
                pass

        for i in range(len(pts) - 1):
            try:
                sp = pts[i]
                ep = pts[i + 1]
                if not _pts_equal(sp, ep, CURVE_MIN_LENGTH):
                    lines.append(DB.Line.CreateBound(sp, ep))
            except Exception:
                pass
    except Exception:
        pass

    return lines if lines else [curve]   # return original if tessellation failed


def tessellate_loop(loop):
    """
    Return a new CurveLoop where every non-Line curve has been replaced
    by tessellated line segments. Returns None if the result is not a
    valid closed loop.
    """
    curves = _extract_curves_from_loop(loop)
    new_curves = []
    for c in curves:
        new_curves.extend(tessellate_curve(c))

    new_curves = _remove_short_curves(new_curves)
    if len(new_curves) < 3:
        return None

    # Re-chain to ensure closure after tessellation
    chained = _chain_curves(new_curves)
    if not chained:
        return None

    # Return the largest loop (outer boundary)
    chained.sort(key=lambda lp: _loop_approx_area(lp), reverse=True)
    return chained[0]


def tessellate_loops(loops):
    """Tessellate every loop in the list; skip loops that fail."""
    result = []
    for lp in loops:
        tl = tessellate_loop(lp)
        if tl is not None:
            result.append(tl)
    return result if result else loops   # fallback to originals


def _loop_approx_area(loop):
    """Rough shoelace area of a loop (ignores Z). Used for sorting only."""
    try:
        pts = []
        it = loop.GetEnumerator()
        while it.MoveNext():
            pts.append(it.Current.GetEndPoint(0))
        n = len(pts)
        if n < 3:
            return 0.0
        area = 0.0
        for i in range(n):
            j = (i + 1) % n
            area += pts[i].X * pts[j].Y
            area -= pts[j].X * pts[i].Y
        return abs(area) * 0.5
    except Exception:
        return 0.0


# =============================================================================
# FILLED REGION TYPE - find by name then update, or create new
# =============================================================================

def _apply_patterns(frt, fg_pat, fg_col, bg_pat, bg_col):
    """Apply patterns and colours to a FilledRegionType."""

    def safe_set(obj, attr, val):
        try:
            setattr(obj, attr, val)
        except Exception:
            pass

    invalid = element_id_to_int(ElementId.InvalidElementId)

    # Revit 2019+ dual-pattern API
    try:
        fg_id = fg_pat.Id if fg_pat is not None else ElementId.InvalidElementId
        bg_id = bg_pat.Id if bg_pat is not None else ElementId.InvalidElementId
        safe_set(frt, "ForegroundPatternId", fg_id)
        safe_set(frt, "BackgroundPatternId", bg_id)
        if fg_col is not None:
            try:
                if fg_col.IsValid:
                    safe_set(frt, "ForegroundPatternColor", fg_col)
            except Exception:
                pass
        if bg_col is not None:
            try:
                if bg_col.IsValid:
                    safe_set(frt, "BackgroundPatternColor", bg_col)
            except Exception:
                pass
        return
    except AttributeError:
        pass

    # Revit 2018 single-pattern API
    if fg_pat is not None:
        safe_set(frt, "FillPatternId", fg_pat.Id)
    if fg_col is not None:
        try:
            if fg_col.IsValid:
                safe_set(frt, "Color", fg_col)
        except Exception:
            pass


def get_or_create_fr_type(name, fg_pat, fg_col, bg_pat, bg_col):
    """Find FilledRegionType by name and update it, or create a new one."""
    all_frt = list(
        FilteredElementCollector(doc).OfClass(FilledRegionType).ToElements()
    )
    if not all_frt:
        forms.alert(
            "No Filled Region Type exists in the project.\n"
            "Create one manually first, then re-run.",
            exitscript=True
        )

    # Search by exact name
    for frt in all_frt:
        try:
            if frt.Name == name:
                _apply_patterns(frt, fg_pat, fg_col, bg_pat, bg_col)
                return frt
        except Exception:
            continue

    # Not found - duplicate first available and rename
    # Duplicate() signature is stable; the result is a new ElementType
    try:
        new_frt = all_frt[0].Duplicate(name)
    except Exception:
        # Last resort: duplicate with a temp name then rename
        import time
        temp_name = "_tmp_{}".format(int(time.time()))
        new_frt = all_frt[0].Duplicate(temp_name)
        try:
            new_frt.Name = name
        except Exception:
            pass

    _apply_patterns(new_frt, fg_pat, fg_col, bg_pat, bg_col)
    return new_frt


# =============================================================================
# DELETE EXISTING FILLED REGIONS BY TYPE NAME IN CURRENT VIEW
# =============================================================================

def delete_existing_filled_regions(fr_type_id):
    """
    Delete every FilledRegion in the active view whose type matches fr_type_id.
    Called inside an open Transaction.
    """
    existing = FilteredElementCollector(doc, view.Id)\
                   .OfClass(FilledRegion)\
                   .ToElements()
    for fr in existing:
        try:
            if element_id_to_int(fr.GetTypeId()) == element_id_to_int(fr_type_id):
                doc.Delete(fr.Id)
        except Exception:
            pass


# =============================================================================
# SELECTION FILTER
# =============================================================================

class SupportedFilter(ISelectionFilter):
    def AllowElement(self, e):
        return is_supported(e)

    def AllowReference(self, r, p):
        return False


# =============================================================================
# MAIN
# =============================================================================

def main():
    valid_vt = [
        DB.ViewType.FloorPlan,
        DB.ViewType.CeilingPlan,
        DB.ViewType.AreaPlan,
        DB.ViewType.Detail,
        DB.ViewType.DraftingView,
        DB.ViewType.Section,
        DB.ViewType.Elevation,
    ]
    if view.ViewType not in valid_vt:
        forms.alert(
            "Run this from a Plan, Section, Elevation, or Detail view.",
            exitscript=True
        )

    # Select elements
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element,
            SupportedFilter(),
            "Select Floors / Roofs / Ceilings / Toposolids, then Finish."
        )
    except OperationCanceledException:
        sys.exit()

    if not refs:
        forms.alert("Nothing selected.", exitscript=True)

    elements = [doc.GetElement(r.ElementId) for r in refs]
    elements = [e for e in elements if is_supported(e)]

    if not elements:
        forms.alert("No supported elements in selection.", exitscript=True)

    created = 0
    skipped = []

    # ------------------------------------------------------------------
    # STRATEGY (v8)
    # Root cause: Revit 2026 constraint solver rejects FilledRegion loops
    # that contain arcs extracted from geometry when openings are present.
    # Fix:
    #   1. ALWAYS tessellate every curve to lines (no arcs ever passed).
    #   2. Classify loops as outer boundary vs inner holes by signed area
    #      and sort them: largest (outer) first, holes after.
    #   3. Use SubTransaction inside one outer Transaction so a single
    #      failure is isolated without a blocking modal dialog.
    #   4. Fallback: if combined placement fails, place outer loop only
    #      (ignores holes) - better than nothing.
    # ------------------------------------------------------------------

    # Pass 1: prepare types and loops (one transaction for type creation)
    type_map    = {}   # int(elem.Id) -> (fr_type, sorted_tess_loops, label)
    prep_errors = []

    with Transaction(doc, "FR - Prepare Types") as t_prep:
        t_prep.Start()
        for elem in elements:
            label = "{} [{}]".format(
                elem.GetType().Name, element_id_to_int(elem.Id)
            )
            try:
                type_name    = get_type_name(elem)
                fr_type_name = type_name + SUFFIX
                mat            = get_top_layer_material(elem)
                fg_pat, fg_col = get_surface_pattern(mat, foreground=True)
                bg_pat, bg_col = get_surface_pattern(mat, foreground=False)
                fr_type = get_or_create_fr_type(
                    fr_type_name, fg_pat, fg_col, bg_pat, bg_col
                )
                raw_loops = get_boundary_loops(elem)
                if not raw_loops:
                    prep_errors.append("{} - no boundary extracted".format(label))
                    continue

                # Sanitize -> tessellate ALL curves to lines -> sort by area
                clean   = sanitize_loops(raw_loops)
                tessed  = tessellate_loops(clean)
                # Sort: largest area first (outer boundary), smaller = holes
                tessed.sort(key=lambda lp: _loop_approx_area(lp), reverse=True)

                type_map[element_id_to_int(elem.Id)] = (fr_type, tessed, label)
            except Exception as ex:
                prep_errors.append("{} - prep error: {}".format(label, str(ex)))
        t_prep.Commit()

    skipped.extend(prep_errors)

    # Pass 2: place each element in its own outer Transaction.
    # Inside that we use SubTransaction for the actual Create call so
    # Revit's constraint failure is isolated and never shows a modal dialog.
    for elem in elements:
        eid = element_id_to_int(elem.Id)
        if eid not in type_map:
            continue

        fr_type, tessed_loops, label = type_map[eid]

        def _place_with_subtx(loop_list, tx_name):
            """
            Try FilledRegion.Create inside a SubTransaction.
            SubTransaction rolls back silently on failure - no modal dialog.
            Returns True on success.
            Must be called inside an already-started outer Transaction.
            """
            stx = DB.SubTransaction(doc)
            stx.Start()
            try:
                delete_existing_filled_regions(fr_type.Id)
                FilledRegion.Create(doc, fr_type.Id, view.Id, loop_list)
                stx.Commit()
                return True
            except Exception:
                try:
                    stx.RollBack()
                except Exception:
                    pass
                return False

        placed = False

        with Transaction(doc, "FR - Place: {}".format(label)) as tx:
            tx.Start()

            # Tier 1: all loops (boundary + holes), fully tessellated
            if _place_with_subtx(tessed_loops, "all-loops"):
                placed = True

            # Tier 2: outer boundary loop only (drop holes)
            if not placed and tessed_loops:
                outer = [tessed_loops[0]]
                if _place_with_subtx(outer, "outer-only"):
                    placed = True
                    skipped.append(
                        "{} - placed outer boundary only "
                        "(holes skipped due to constraint error)".format(label)
                    )

            # Tier 3: try each loop individually, collect whatever works
            if not placed:
                ok_count = 0
                for i, single_loop in enumerate(tessed_loops):
                    stx = DB.SubTransaction(doc)
                    stx.Start()
                    try:
                        if i == 0:
                            # Only delete existing on first loop attempt
                            delete_existing_filled_regions(fr_type.Id)
                        FilledRegion.Create(
                            doc, fr_type.Id, view.Id, [single_loop]
                        )
                        stx.Commit()
                        ok_count += 1
                    except Exception:
                        try:
                            stx.RollBack()
                        except Exception:
                            pass

                if ok_count > 0:
                    placed = True
                    skipped.append(
                        "{} - placed {}/{} loops individually".format(
                            label, ok_count, len(tessed_loops)
                        )
                    )

            if placed:
                tx.Commit()
                created += 1
            else:
                tx.RollBack()
                skipped.append(
                    "{} - all placement tiers failed".format(label)
                )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        forms.alert(
            "Unexpected error:\n{}\n\n{}".format(str(e), traceback.format_exc()),
            title="Script Error"
        )
