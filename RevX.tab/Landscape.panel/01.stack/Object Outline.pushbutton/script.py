# -*- coding: utf-8 -*-
"""
Surface Pattern Region (v8 - Robust Crash-Proof Floor Boundary Extractor)
-------------------------------------------------------------------------------
PyRevit Script: Creates filled regions from the topmost compound-structure
layer of Floors, Roofs, Ceilings, and Toposolids.

FilledRegionType naming rule:
  "<ElementTypeName> (Layout)"
  e.g.  "LA_Paving type-IF1"  ->  "LA_Paving type-IF1 (Layout)"

Compatible: Revit 2023 - 2027+  |  IronPython 2.7 (PyRevit) / Pythonnet CPython
"""

__title__  = "Surface Pattern Region"
__author__  = "PyRevit"
__doc__    = (
    "Select Floors, Roofs, Ceilings or Toposolids. "
    "Creates a FilledRegion named '<TypeName> (Layout)' matching "
    "the topmost layer surface foreground + background patterns."
)

import sys
import math
import traceback

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System")

from System.Collections.Generic import List

import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    Floor, RoofBase, Ceiling,
    FilledRegionType, FilledRegion,
    CurveLoop, Transaction, ElementId, Options, XYZ, Line, Arc, UV
)

# Selection imports compatibility across Revit versions
try:
    from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
except ImportError:
    try:
        from Autodesk.Revit.UI import ISelectionFilter, ObjectType
    except ImportError:
        from Autodesk.Revit.UI.Selection import ObjectType
        ISelectionFilter = object

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
    _ver_str = doc.Application.VersionNumber
    REVIT_VERSION = int(_ver_str)
except Exception:
    REVIT_VERSION = 2024

# Minimum curve length based on Revit's internal short curve tolerance (~0.00256 ft)
try:
    SHORT_CURVE_TOLERANCE = doc.Application.ShortCurveTolerance
except Exception:
    SHORT_CURVE_TOLERANCE = 0.00256

MIN_CURVE_LEN = max(0.003, SHORT_CURVE_TOLERANCE * 1.05)
GAP_TOLERANCE = 0.02   # Max gap in feet to consider vertices connected (~6mm)

# =============================================================================
# COMPAT HELPER: ElementId integer value
# =============================================================================

def element_id_to_int(eid):
    """Return the integer value of an ElementId regardless of Revit version (2023-2027+)."""
    if eid is None:
        return -1
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
    """Return the Type name of a system-family element."""
    try:
        etype = doc.GetElement(element.GetTypeId())
        if etype is not None:
            n = etype.Name
            if n and n.strip():
                return n.strip()
    except Exception:
        pass

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

    try:
        p = element.get_Parameter(DB.BuiltInParameter.ELEM_TYPE_PARAM)
        if p is not None:
            v = p.AsString()
            if v and v.strip():
                return v.strip()
    except Exception:
        pass

    try:
        n = element.Name
        if n and n.strip() and not n.strip().isdigit():
            return n.strip()
    except Exception:
        pass

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
# VIEW Z ELEVATION
# =============================================================================

def get_view_z(v):
    """Get the active view plane Z coordinate for coplanar 2D projection."""
    try:
        if v.GenLevel is not None:
            return v.GenLevel.Elevation
    except Exception:
        pass
    try:
        return v.Origin.Z
    except Exception:
        pass
    return 0.0

# =============================================================================
# GEOMETRY & BOUNDARY EXTRACTION
# =============================================================================

def extract_raw_curves(element):
    """
    Extract all boundary curves from an element using Sketch, Profile,
    or Solid Geometry faces. Returns a flat list of DB.Curve objects.
    """
    raw_curves = []

    # 1. Try Sketch
    sketch = None
    try:
        sid = element.SketchId if hasattr(element, "SketchId") else None
        if sid and element_id_to_int(sid) != element_id_to_int(ElementId.InvalidElementId):
            sketch = doc.GetElement(sid)
    except Exception:
        pass

    if sketch is None:
        try:
            sid = element.GetSketchId()
            if element_id_to_int(sid) != element_id_to_int(ElementId.InvalidElementId):
                sketch = doc.GetElement(sid)
        except Exception:
            pass

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

    if sketch is not None:
        try:
            for arr in sketch.Profile:
                for c in arr:
                    if c is not None:
                        raw_curves.append(c)
            if raw_curves:
                return raw_curves
        except Exception:
            pass

    # 2. Try GetProfile()
    if REVIT_VERSION < 2026:
        try:
            prof = element.GetProfile()
            if prof:
                for lp in prof:
                    for c in lp:
                        raw_curves.append(c)
                if raw_curves:
                    return raw_curves
        except Exception:
            pass

    # 3. Try Solid Geometry
    upward = not isinstance(element, Ceiling)
    solids = []

    try:
        opts = Options()
        opts.ComputeReferences = False
        try:
            opts.DetailLevel = DB.ViewDetailLevel.Fine
        except Exception:
            pass

        geom = element.get_Geometry(opts)
        if geom is not None:
            def _collect_solids(g_elem):
                collected = []
                for obj in g_elem:
                    if obj is None:
                        continue
                    if isinstance(obj, DB.Solid):
                        if obj.Volume > 0.0001:
                            collected.append(obj)
                    elif hasattr(obj, "GetInstanceGeometry"):
                        try:
                            ig = obj.GetInstanceGeometry()
                            if ig is not None:
                                collected.extend(_collect_solids(ig))
                        except Exception:
                            pass
                return collected

            solids = _collect_solids(geom)
    except Exception:
        solids = []

    best_face = None
    best_z = None

    for s in solids:
        try:
            for face in s.Faces:
                try:
                    if isinstance(face, DB.PlanarFace):
                        norm = face.FaceNormal
                        z = face.Origin.Z
                    else:
                        norm = face.ComputeNormal(UV(0.5, 0.5))
                        bb = face.GetBoundingBox()
                        mid_uv = UV((bb.Min.U + bb.Max.U) * 0.5, (bb.Min.V + bb.Max.V) * 0.5)
                        z = face.Evaluate(mid_uv).Z

                    target_z = 1.0 if upward else -1.0
                    if abs(norm.Z - target_z) < 0.1:  # ~5 degrees vertical
                        if best_z is None:
                            best_z = z
                            best_face = face
                        elif upward and z > best_z:
                            best_z = z
                            best_face = face
                        elif not upward and z < best_z:
                            best_z = z
                            best_face = face
                except Exception:
                    continue
        except Exception:
            continue

    if best_face is not None:
        try:
            for lp in best_face.GetEdgesAsCurveLoops():
                for c in lp:
                    raw_curves.append(c)
        except Exception:
            pass

    return raw_curves

# =============================================================================
# TESSELLATION & FLATTENING
# =============================================================================

def tessellate_and_flatten(curves, z_target):
    """
    Tessellate all curves (lines, arcs, splines) into straight 2D DB.Line segments
    lying strictly on the z_target elevation plane.
    """
    flat_lines = []

    for c in curves:
        if c is None:
            continue
        try:
            # Determine sample points along the curve
            t0 = c.GetEndParameter(0)
            t1 = c.GetEndParameter(1)

            if isinstance(c, Line):
                n_samples = 1
            elif isinstance(c, Arc):
                arc_len = abs(t1 - t0)
                n_samples = max(4, int(math.ceil(arc_len / (math.pi / 16.0))))  # ~32 segs per full circle
            else:
                n_samples = 8  # Splines, Ellipses, etc.

            pts = []
            for i in range(n_samples + 1):
                t = t0 + (t1 - t0) * (float(i) / float(n_samples))
                eval_pt = c.Evaluate(t, False)
                pts.append(XYZ(eval_pt.X, eval_pt.Y, z_target))

            for i in range(len(pts) - 1):
                p1 = pts[i]
                p2 = pts[i + 1]
                if p1.DistanceTo(p2) >= MIN_CURVE_LEN:
                    flat_lines.append(Line.CreateBound(p1, p2))
        except Exception:
            continue

    return flat_lines

# =============================================================================
# CHAINING & LOOP CLOSURE
# =============================================================================

def chain_lines_to_loops(lines, z_target):
    """
    Group unordered flat DB.Line segments into closed, continuous CurveLoops.
    Guarantees bitwise-exact vertex matches between adjacent segments and loop closure.
    """
    if not lines:
        return []

    remaining = list(lines)
    closed_loops = []

    def pts_near(pA, pB):
        return pA.DistanceTo(pB) <= GAP_TOLERANCE

    while remaining:
        first_line = remaining.pop(0)
        # Chain of XYZ points forming the polygon
        poly_pts = [first_line.GetEndPoint(0), first_line.GetEndPoint(1)]
        changed = True

        while changed and remaining:
            changed = False
            tail = poly_pts[-1]

            for i, line in enumerate(remaining):
                sp = line.GetEndPoint(0)
                ep = line.GetEndPoint(1)

                if pts_near(tail, sp):
                    poly_pts.append(ep)
                    remaining.pop(i)
                    changed = True
                    break
                elif pts_near(tail, ep):
                    poly_pts.append(sp)
                    remaining.pop(i)
                    changed = True
                    break

        # Check loop closure: tail near head
        if len(poly_pts) >= 4:  # At least 3 segments (4 points counting start/end)
            head = poly_pts[0]
            tail = poly_pts[-1]

            if pts_near(head, tail):
                # Force last point to match head exactly
                poly_pts[-1] = head

                # Filter duplicate/micro points
                clean_pts = [poly_pts[0]]
                for pt in poly_pts[1:]:
                    if pt.DistanceTo(clean_pts[-1]) >= MIN_CURVE_LEN:
                        clean_pts.append(pt)

                # Ensure closing point matches first
                if clean_pts[0].DistanceTo(clean_pts[-1]) > 0.0001:
                    clean_pts.append(clean_pts[0])

                if len(clean_pts) >= 4:
                    cl = CurveLoop()
                    ok = True
                    for k in range(len(clean_pts) - 1):
                        p_start = clean_pts[k]
                        p_end   = clean_pts[k + 1]
                        if p_start.DistanceTo(p_end) >= MIN_CURVE_LEN:
                            try:
                                seg = Line.CreateBound(p_start, p_end)
                                cl.Append(seg)
                            except Exception:
                                ok = False
                                break

                    if ok and is_valid_loop(cl):
                        closed_loops.append(cl)

    return closed_loops

# =============================================================================
# LOOP VALIDATION & ORIENTATION
# =============================================================================

def is_valid_loop(cl):
    """Check if CurveLoop is strictly valid for Revit's FilledRegion engine."""
    if cl is None:
        return False
    try:
        if cl.IsOpen():
            return False
    except Exception:
        pass
    try:
        if cl.HasOpenBounds():
            return False
    except Exception:
        pass

    curves = list(cl)
    if len(curves) < 3:
        return False

    for c in curves:
        try:
            if c.Length < SHORT_CURVE_TOLERANCE:
                return False
        except Exception:
            return False

    return True


def loop_area_2d(cl):
    """Calculate signed 2D shoelace area of a CurveLoop."""
    try:
        pts = [c.GetEndPoint(0) for c in cl]
        n = len(pts)
        if n < 3:
            return 0.0
        area = 0.0
        for i in range(n):
            j = (i + 1) % n
            area += pts[i].X * pts[j].Y
            area -= pts[j].X * pts[i].Y
        return area * 0.5
    except Exception:
        return 0.0


def point_in_polygon_2d(pt, poly_pts):
    """Ray-casting algorithm to test if 2D point is inside a polygon of XYZ points."""
    x, y = pt.X, pt.Y
    inside = False
    n = len(poly_pts)
    j = n - 1
    for i in range(n):
        xi, yi = poly_pts[i].X, poly_pts[i].Y
        xj, yj = poly_pts[j].X, poly_pts[j].Y
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / float(yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def orient_loops(loops):
    """
    Sort loops by area (descending) and set winding orientation:
    - Outer islands: Counter-Clockwise (CCW)
    - Holes inside islands: Clockwise (CW)
    """
    if not loops:
        return []

    # Sort loops by absolute area descending
    sorted_loops = sorted(loops, key=lambda lp: abs(loop_area_2d(lp)), reverse=True)

    normal_z = XYZ.BasisZ
    final_loops = []

    for i, cl in enumerate(sorted_loops):
        # Extract points for containment testing
        pts_i = [c.GetEndPoint(0) for c in cl]
        test_pt = pts_i[0]

        # Count how many larger loops contain this loop's test point
        containment_depth = 0
        for j in range(i):
            pts_j = [c.GetEndPoint(0) for c in sorted_loops[j]]
            if point_in_polygon_2d(test_pt, pts_j):
                containment_depth += 1

        is_outer = (containment_depth % 2 == 0)  # Even depth = Island, Odd depth = Hole

        try:
            is_ccw = cl.IsCounterClockwise(normal_z)
            if is_outer and not is_ccw:
                cl.Flip()
            elif not is_outer and is_ccw:
                cl.Flip()
        except Exception:
            pass

        final_loops.append(cl)

    return final_loops

# =============================================================================
# FILLED REGION TYPE MANAGEMENT
# =============================================================================

def _apply_patterns(frt, fg_pat, fg_col, bg_pat, bg_col):
    """Apply surface patterns and colors to a FilledRegionType."""
    def safe_set(obj, attr, val):
        try:
            setattr(obj, attr, val)
        except Exception:
            pass

    try:
        fg_id = fg_pat.Id if fg_pat is not None else ElementId.InvalidElementId
        bg_id = bg_pat.Id if bg_pat is not None else ElementId.InvalidElementId
        safe_set(frt, "ForegroundPatternId", fg_id)
        safe_set(frt, "BackgroundPatternId", bg_id)
        if fg_col is not None and getattr(fg_col, "IsValid", True):
            safe_set(frt, "ForegroundPatternColor", fg_col)
        if bg_col is not None and getattr(bg_col, "IsValid", True):
            safe_set(frt, "BackgroundPatternColor", bg_col)
        return
    except AttributeError:
        pass

    if fg_pat is not None:
        safe_set(frt, "FillPatternId", fg_pat.Id)
    if fg_col is not None and getattr(fg_col, "IsValid", True):
        safe_set(frt, "Color", fg_col)


def get_or_create_fr_type(name, fg_pat, fg_col, bg_pat, bg_col):
    """Find FilledRegionType by name and update it, or duplicate to create a new one."""
    all_frt = list(FilteredElementCollector(doc).OfClass(FilledRegionType).ToElements())
    if not all_frt:
        forms.alert("No Filled Region Type exists in project. Create one manually first.", exitscript=True)

    for frt in all_frt:
        try:
            if frt.Name == name:
                _apply_patterns(frt, fg_pat, fg_col, bg_pat, bg_col)
                return frt
        except Exception:
            continue

    try:
        new_frt = all_frt[0].Duplicate(name)
    except Exception:
        import time
        temp_name = "_tmp_{}".format(int(time.time()))
        new_frt = all_frt[0].Duplicate(temp_name)
        try:
            new_frt.Name = name
        except Exception:
            pass

    _apply_patterns(new_frt, fg_pat, fg_col, bg_pat, bg_col)
    return new_frt


def delete_existing_filled_regions(fr_type_ids):
    """Delete existing FilledRegions matching fr_type_ids in the active view once."""
    if not fr_type_ids:
        return
    type_int_set = set(element_id_to_int(tid) for tid in fr_type_ids)
    existing = FilteredElementCollector(doc, view.Id).OfClass(FilledRegion).ToElements()
    for fr in existing:
        try:
            if element_id_to_int(fr.GetTypeId()) in type_int_set:
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
        forms.alert("Run this script from a Plan, Section, Elevation, or Detail view.", exitscript=True)

    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element,
            SupportedFilter(),
            "Select Floors / Roofs / Ceilings / Toposolids, then press Finish."
        )
    except OperationCanceledException:
        sys.exit()

    if not refs:
        forms.alert("Nothing selected.", exitscript=True)

    elements = [doc.GetElement(r.ElementId) for r in refs]
    elements = [e for e in elements if is_supported(e)]

    if not elements:
        forms.alert("No supported elements in selection.", exitscript=True)

    z_view = get_view_z(view)

    # Preparation Pass: Types & Clean CurveLoops
    prep_data = []
    created_fr_types = set()
    skipped_info = []

    with Transaction(doc, "FR - Prepare Types") as t_prep:
        t_prep.Start()
        for elem in elements:
            label = "{} [{}]".format(elem.GetType().Name, element_id_to_int(elem.Id))
            try:
                type_name    = get_type_name(elem)
                fr_type_name = type_name + SUFFIX
                mat          = get_top_layer_material(elem)
                fg_pat, fg_col = get_surface_pattern(mat, foreground=True)
                bg_pat, bg_col = get_surface_pattern(mat, foreground=False)

                fr_type = get_or_create_fr_type(fr_type_name, fg_pat, fg_col, bg_pat, bg_col)
                created_fr_types.add(fr_type.Id)

                raw_curves = extract_raw_curves(elem)
                if not raw_curves:
                    skipped_info.append("{} - could not extract geometry boundary".format(label))
                    continue

                flat_lines = tessellate_and_flatten(raw_curves, z_view)
                closed_loops = chain_lines_to_loops(flat_lines, z_view)

                if not closed_loops:
                    skipped_info.append("{} - loop closure failed".format(label))
                    continue

                oriented_loops = orient_loops(closed_loops)
                prep_data.append((elem, fr_type, oriented_loops, label))
            except Exception as ex:
                skipped_info.append("{} - error preparing: {}".format(label, str(ex)))
        t_prep.Commit()

    # Clean up pre-existing filled regions of these types ONCE before placing
    with Transaction(doc, "FR - Clear Existing") as t_clear:
        t_clear.Start()
        delete_existing_filled_regions(created_fr_types)
        t_clear.Commit()

    # Placement Pass: SubTransactions per element
    created_count = 0

    for elem, fr_type, oriented_loops, label in prep_data:
        placed = False

        def try_create_fr(loops_to_place):
            stx = DB.SubTransaction(doc)
            stx.Start()
            try:
                net_list = List[CurveLoop]()
                for lp in loops_to_place:
                    if is_valid_loop(lp):
                        net_list.Add(lp)

                if net_list.Count > 0:
                    FilledRegion.Create(doc, fr_type.Id, view.Id, net_list)
                    stx.Commit()
                    return True
            except Exception:
                pass
            try:
                stx.RollBack()
            except Exception:
                pass
            return False

        with Transaction(doc, "FR - Place: {}".format(label)) as tx:
            tx.Start()

            # Tier 1: All loops (Boundary + Holes)
            if try_create_fr(oriented_loops):
                placed = True

            # Tier 2: Outer Boundary only (Drop Holes if tier 1 fails)
            if not placed and oriented_loops:
                if try_create_fr([oriented_loops[0]]):
                    placed = True
                    skipped_info.append("{} - placed outer boundary only (holes skipped)".format(label))

            # Tier 3: Place loops individually
            if not placed:
                indiv_success = 0
                for lp in oriented_loops:
                    if try_create_fr([lp]):
                        indiv_success += 1
                if indiv_success > 0:
                    placed = True
                    skipped_info.append("{} - placed {}/{} loops individually".format(label, indiv_success, len(oriented_loops)))

            if placed:
                tx.Commit()
                created_count += 1
            else:
                tx.RollBack()
                skipped_info.append("{} - all placement tiers failed".format(label))

    # Final summary alert
    msg = "Created {} Filled Region(s).".format(created_count)
    if skipped_info:
        msg += "\n\nSkipped / Warnings:\n" + "\n".join(skipped_info[:10])
        if len(skipped_info) > 10:
            msg += "\n...and {} more.".format(len(skipped_info) - 10)

    forms.alert(msg, title="Surface Pattern Region", warn_icon=False if created_count > 0 else True)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        forms.alert(
            "Unexpected script error:\n{}\n\n{}".format(str(e), traceback.format_exc()),
            title="Script Error"
        )
