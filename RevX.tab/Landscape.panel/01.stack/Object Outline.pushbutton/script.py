# -*- coding: utf-8 -*-
"""
Surface Pattern Region (v9 - Crash-Hardened)
-------------------------------------------------------------------------------
Creates filled regions from top compound layer of Floors/Roofs/Ceilings/Toposolids.
Hardened for large/complex floors & toposolids.
"""

__title__ = "Surface Pattern Region"
__author__ = "PyRevit"
__doc__ = (
    "Select Floors, Roofs, Ceilings or Toposolids. "
    "Creates FilledRegion '<TypeName> (Layout)' from top surface patterns."
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
    FilteredElementCollector, Floor, RoofBase, Ceiling,
    FilledRegionType, FilledRegion, CurveLoop, Transaction,
    ElementId, Options, XYZ, Line, Arc, UV, FailureProcessingResult
)

try:
    from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
except ImportError:
    from Autodesk.Revit.UI.Selection import ObjectType
    ISelectionFilter = object

from Autodesk.Revit.Exceptions import OperationCanceledException

try:
    from Autodesk.Revit.DB import Toposolid
    HAS_TOPOSOLID = True
except ImportError:
    HAS_TOPOSOLID = False

try:
    from Autodesk.Revit.DB import HostObjectUtils
    HAS_HOST_UTILS = True
except ImportError:
    HAS_HOST_UTILS = False

from pyrevit import forms, revit

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView

SUFFIX = " (Layout)"

# ---------------- limits (raise carefully) ----------------
MAX_SEGMENTS_PER_LOOP = 400          # hard cap before simplify/skip
MAX_LOOPS_PER_ELEMENT = 40
MAX_TESSELLATION_SAMPLES = 24        # arcs/splines
COLLINEAR_DOT_TOL = 0.99985          # simplify almost-straight chains
GAP_TOLERANCE = 0.05                 # ~15mm join tolerance
AREA_MIN = 0.05                      # ignore tiny slivers (ft^2)

try:
    SHORT_CURVE_TOLERANCE = float(doc.Application.ShortCurveTolerance)
except Exception:
    SHORT_CURVE_TOLERANCE = 0.00256
MIN_CURVE_LEN = max(0.005, SHORT_CURVE_TOLERANCE * 2.0)

try:
    REVIT_VERSION = int(doc.Application.VersionNumber)
except Exception:
    REVIT_VERSION = 2024


# =============================================================================
# FAILURE PREPROCESSOR (prevents many hard crashes)
# =============================================================================
class _WarnSwallower(DB.IFailuresPreprocessor):
    def PreprocessFailures(self, failuresAccessor):
        try:
            for f in list(failuresAccessor.GetFailureMessages()):
                try:
                    failuresAccessor.DeleteWarning(f)
                except Exception:
                    pass
        except Exception:
            pass
        return FailureProcessingResult.Continue


def _cfg_tx(t):
    """Configure transaction to be less crashy."""
    try:
        opts = t.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(_WarnSwallower())
        opts.SetClearAfterRollback(True)
        opts.SetDelayedMiniWarnings(True)
        t.SetFailureHandlingOptions(opts)
    except Exception:
        pass


def eid_int(eid):
    if eid is None:
        return -1
    try:
        return int(eid.Value)
    except Exception:
        try:
            return int(eid.IntegerValue)
        except Exception:
            return -1


def is_supported(e):
    if e is None:
        return False
    if isinstance(e, (Floor, RoofBase, Ceiling)):
        return True
    if HAS_TOPOSOLID and isinstance(e, Toposolid):
        return True
    return False


class SupportedFilter(ISelectionFilter):
    def AllowElement(self, e):
        return is_supported(e)
    def AllowReference(self, r, p):
        return False


# =============================================================================
# TYPE / MATERIAL / PATTERN
# =============================================================================
def get_type_name(element):
    try:
        et = doc.GetElement(element.GetTypeId())
        if et and et.Name and et.Name.strip():
            return et.Name.strip()
    except Exception:
        pass
    try:
        et = doc.GetElement(element.GetTypeId())
        p = et.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM) if et else None
        if p and p.AsString():
            return p.AsString().strip()
    except Exception:
        pass
    return "UnknownType_{}".format(eid_int(element.Id))


def get_top_layer_material(element):
    try:
        et = doc.GetElement(element.GetTypeId())
        cs = et.GetCompoundStructure() if et else None
        if cs:
            layers = list(cs.GetLayers())
            try:
                first_core = cs.GetFirstCoreLayerIndex()
            except Exception:
                first_core = 0
            order = list(range(first_core)) + list(range(first_core, len(layers))) if first_core > 0 else list(range(len(layers)))
            for idx in order:
                try:
                    mid = layers[idx].MaterialId
                    if eid_int(mid) != eid_int(ElementId.InvalidElementId):
                        mat = doc.GetElement(mid)
                        if mat:
                            return mat
                except Exception:
                    continue
    except Exception:
        pass
    for bip in (DB.BuiltInParameter.MATERIAL_ID_PARAM, DB.BuiltInParameter.STRUCTURAL_MATERIAL_PARAM):
        try:
            p = element.get_Parameter(bip)
            if not p:
                continue
            mid = p.AsElementId()
            if mid and eid_int(mid) != eid_int(ElementId.InvalidElementId):
                mat = doc.GetElement(mid)
                if mat:
                    return mat
        except Exception:
            continue
    return None


def get_surface_pattern(material, foreground=True):
    if material is None:
        return None, None
    try:
        if foreground:
            pid, color = material.SurfaceForegroundPatternId, material.SurfaceForegroundPatternColor
        else:
            pid, color = material.SurfaceBackgroundPatternId, material.SurfaceBackgroundPatternColor
        pat = doc.GetElement(pid) if eid_int(pid) != eid_int(ElementId.InvalidElementId) else None
        return pat, color
    except Exception:
        pass
    try:
        if foreground:
            pid, color = material.SurfacePatternId, material.SurfacePatternColor
            pat = doc.GetElement(pid) if eid_int(pid) != eid_int(ElementId.InvalidElementId) else None
            return pat, color
    except Exception:
        pass
    return None, None


def get_view_z(v):
    try:
        if v.GenLevel:
            return float(v.GenLevel.Elevation)
    except Exception:
        pass
    try:
        return float(v.Origin.Z)
    except Exception:
        return 0.0


# =============================================================================
# GEOMETRY EXTRACTION (lighter)
# =============================================================================
def _collect_solids(ge):
    out = []
    if ge is None:
        return out
    for obj in ge:
        if obj is None:
            continue
        if isinstance(obj, DB.Solid) and obj.Volume > 1e-6:
            out.append(obj)
        elif hasattr(obj, "GetInstanceGeometry"):
            try:
                out.extend(_collect_solids(obj.GetInstanceGeometry()))
            except Exception:
                pass
    return out


def extract_raw_curves(element):
    """Extract boundary curves with lightest possible method first."""
    curves = []

    # 1) Sketch profile (best quality, lightest)
    sketch = None
    for getter in (
        lambda: doc.GetElement(element.SketchId) if hasattr(element, "SketchId") else None,
        lambda: doc.GetElement(element.GetSketchId()) if hasattr(element, "GetSketchId") else None,
    ):
        try:
            s = getter()
            if s is not None:
                sketch = s
                break
        except Exception:
            pass

    if sketch is None:
        try:
            for dep_id in element.GetDependentElements(None):
                dep = doc.GetElement(dep_id)
                if dep and dep.GetType().Name == "Sketch":
                    sketch = dep
                    break
        except Exception:
            pass

    if sketch is not None:
        try:
            for arr in sketch.Profile:
                for c in arr:
                    if c is not None:
                        curves.append(c)
            if curves:
                return curves
        except Exception:
            curves = []

    # 2) Host top faces edges
    if HAS_HOST_UTILS and isinstance(element, (Floor, RoofBase, Ceiling)):
        try:
            face_refs = HostObjectUtils.GetTopFaces(element)
            opts = Options()
            opts.ComputeReferences = True
            geom = element.get_Geometry(opts)
            # fallback to solid face edges below if needed
        except Exception:
            face_refs = None

    # 3) Solid top/bottom face edges (coarse detail!)
    try:
        opts = Options()
        opts.ComputeReferences = False
        try:
            opts.DetailLevel = DB.ViewDetailLevel.Coarse  # IMPORTANT for big elements
        except Exception:
            pass
        solids = _collect_solids(element.get_Geometry(opts))
        upward = not isinstance(element, Ceiling)
        best_face, best_z = None, None
        for s in solids:
            try:
                for face in s.Faces:
                    try:
                        if isinstance(face, DB.PlanarFace):
                            n = face.FaceNormal
                            z = face.Origin.Z
                        else:
                            # skip heavy non-planar faces on huge toposolids
                            if HAS_TOPOSOLID and isinstance(element, Toposolid):
                                continue
                            n = face.ComputeNormal(UV(0.5, 0.5))
                            bb = face.GetBoundingBox()
                            z = face.Evaluate(UV((bb.Min.U + bb.Max.U) * 0.5,
                                                 (bb.Min.V + bb.Max.V) * 0.5)).Z
                        target = 1.0 if upward else -1.0
                        if abs(n.Z - target) > 0.15:
                            continue
                        if best_z is None or (upward and z > best_z) or ((not upward) and z < best_z):
                            best_z = z
                            best_face = face
                    except Exception:
                        continue
            except Exception:
                continue

        if best_face is not None:
            for lp in best_face.GetEdgesAsCurveLoops():
                for c in lp:
                    curves.append(c)
    except Exception:
        pass

    return curves


# =============================================================================
# PROJECT / SIMPLIFY / LOOP BUILD
# =============================================================================
def _xy(p, z):
    return XYZ(p.X, p.Y, z)


def _dist2d(a, b):
    dx = a.X - b.X
    dy = a.Y - b.Y
    return math.sqrt(dx * dx + dy * dy)


def project_curve_to_plane(c, z):
    """
    Keep Line/Arc as single curve when possible.
    Tessellate only heavier curves, with hard sample cap.
    Returns list[Curve].
    """
    if c is None:
        return []
    try:
        p0 = _xy(c.GetEndPoint(0), z)
        p1 = _xy(c.GetEndPoint(1), z)
        if _dist2d(p0, p1) < MIN_CURVE_LEN and not isinstance(c, Arc):
            return []

        # Straight line -> one segment
        if isinstance(c, Line):
            if _dist2d(p0, p1) >= MIN_CURVE_LEN:
                return [Line.CreateBound(p0, p1)]
            return []

        # Arc: try planar projected arc-ish polyline with limited samples
        n = 1
        if isinstance(c, Arc):
            try:
                ang = abs(c.Length / max(c.Radius, 1e-6))
                n = int(math.ceil(ang / (math.pi / 12.0)))  # ~15 deg
            except Exception:
                n = 8
        else:
            n = 10

        n = max(2, min(MAX_TESSELLATION_SAMPLES, n))
        t0 = c.GetEndParameter(0)
        t1 = c.GetEndParameter(1)
        pts = []
        for i in range(n + 1):
            t = t0 + (t1 - t0) * (float(i) / float(n))
            pt = c.Evaluate(t, False)
            pts.append(_xy(pt, z))
        return _points_to_lines(pts)
    except Exception:
        return []


def _points_to_lines(pts):
    lines = []
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        if _dist2d(a, b) >= MIN_CURVE_LEN:
            try:
                lines.append(Line.CreateBound(a, b))
            except Exception:
                pass
    return lines


def simplify_points(pts):
    """Remove near-duplicate and collinear points."""
    if len(pts) < 3:
        return pts
    # dedupe consecutive
    clean = [pts[0]]
    for p in pts[1:]:
        if _dist2d(p, clean[-1]) >= MIN_CURVE_LEN:
            clean.append(p)
    if len(clean) < 3:
        return clean

    # collinear reduce
    out = [clean[0]]
    for i in range(1, len(clean) - 1):
        a = out[-1]
        b = clean[i]
        c = clean[i + 1]
        v1 = XYZ(b.X - a.X, b.Y - a.Y, 0)
        v2 = XYZ(c.X - b.X, c.Y - b.Y, 0)
        try:
            l1 = math.sqrt(v1.X * v1.X + v1.Y * v1.Y)
            l2 = math.sqrt(v2.X * v2.X + v2.Y * v2.Y)
            if l1 < 1e-9 or l2 < 1e-9:
                continue
            dot = (v1.X * v2.X + v1.Y * v2.Y) / (l1 * l2)
            if dot > COLLINEAR_DOT_TOL:
                continue  # almost collinear, skip b
        except Exception:
            pass
        out.append(b)
    out.append(clean[-1])
    return out


def curves_to_simple_loops(raw_curves, z):
    """Project, chain, simplify, build closed CurveLoops with hard caps."""
    segs = []
    for c in raw_curves:
        segs.extend(project_curve_to_plane(c, z))
    if not segs:
        return []

    # safety: insane geometry
    if len(segs) > 5000:
        return []  # caller will report too complex

    remaining = list(segs)
    loops = []

    def near(a, b):
        return _dist2d(a, b) <= GAP_TOLERANCE

    guard = 0
    max_guard = max(50, len(remaining) * 2)

    while remaining and len(loops) < MAX_LOOPS_PER_ELEMENT and guard < max_guard:
        guard += 1
        ln = remaining.pop(0)
        pts = [ln.GetEndPoint(0), ln.GetEndPoint(1)]
        grew = True
        local_guard = 0
        while grew and remaining and local_guard < len(segs) + 5:
            local_guard += 1
            grew = False
            tail = pts[-1]
            for i, s in enumerate(remaining):
                sp, ep = s.GetEndPoint(0), s.GetEndPoint(1)
                if near(tail, sp):
                    pts.append(ep)
                    remaining.pop(i)
                    grew = True
                    break
                if near(tail, ep):
                    pts.append(sp)
                    remaining.pop(i)
                    grew = True
                    break

        if len(pts) < 4:
            continue
        if not near(pts[0], pts[-1]):
            continue

        pts[-1] = pts[0]
        pts = simplify_points(pts)
        if len(pts) >= 3 and _dist2d(pts[0], pts[-1]) > 1e-6:
            pts.append(pts[0])
        if len(pts) < 4:
            continue

        # hard simplify if still too dense
        if len(pts) - 1 > MAX_SEGMENTS_PER_LOOP:
            step = int(math.ceil((len(pts) - 1) / float(MAX_SEGMENTS_PER_LOOP)))
            reduced = [pts[i] for i in range(0, len(pts) - 1, step)]
            if reduced[0].DistanceTo(pts[0]) > 1e-9:
                reduced = [pts[0]] + reduced
            reduced.append(pts[0])
            pts = simplify_points(reduced)
            if len(pts) >= 3 and _dist2d(pts[0], pts[-1]) > 1e-6:
                pts.append(pts[0])

        if len(pts) < 4:
            continue

        cl = CurveLoop()
        ok = True
        count = 0
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            if _dist2d(a, b) < MIN_CURVE_LEN:
                continue
            try:
                cl.Append(Line.CreateBound(a, b))
                count += 1
            except Exception:
                ok = False
                break
            if count > MAX_SEGMENTS_PER_LOOP:
                ok = False
                break
        if not ok or count < 3:
            continue
        try:
            if cl.IsOpen():
                continue
        except Exception:
            continue
        if abs(loop_area_2d(cl)) < AREA_MIN:
            continue
        loops.append(cl)

    return loops


def loop_area_2d(cl):
    try:
        pts = [c.GetEndPoint(0) for c in cl]
        n = len(pts)
        if n < 3:
            return 0.0
        a = 0.0
        for i in range(n):
            j = (i + 1) % n
            a += pts[i].X * pts[j].Y - pts[j].X * pts[i].Y
        return 0.5 * a
    except Exception:
        return 0.0


def point_in_poly(pt, poly):
    x, y = pt.X, pt.Y
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i].X, poly[i].Y
        xj, yj = poly[j].X, poly[j].Y
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / float(yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def orient_loops(loops):
    if not loops:
        return []
    ordered = sorted(loops, key=lambda lp: abs(loop_area_2d(lp)), reverse=True)
    out = []
    for i, cl in enumerate(ordered):
        pts_i = [c.GetEndPoint(0) for c in cl]
        depth = 0
        for j in range(i):
            pts_j = [c.GetEndPoint(0) for c in ordered[j]]
            if point_in_poly(pts_i[0], pts_j):
                depth += 1
        is_outer = (depth % 2 == 0)
        try:
            ccw = cl.IsCounterclockwise(XYZ.BasisZ)
            if is_outer and not ccw:
                cl.Flip()
            elif (not is_outer) and ccw:
                cl.Flip()
        except Exception:
            try:
                # older API name
                ccw = cl.IsCounterClockwise(XYZ.BasisZ)
                if is_outer and not ccw:
                    cl.Flip()
                elif (not is_outer) and ccw:
                    cl.Flip()
            except Exception:
                pass
        out.append(cl)
    return out


# =============================================================================
# FILLED REGION TYPE
# =============================================================================
def _apply_patterns(frt, fg_pat, fg_col, bg_pat, bg_col):
    def safe_set(obj, attr, val):
        try:
            setattr(obj, attr, val)
        except Exception:
            pass
    try:
        safe_set(frt, "ForegroundPatternId", fg_pat.Id if fg_pat else ElementId.InvalidElementId)
        safe_set(frt, "BackgroundPatternId", bg_pat.Id if bg_pat else ElementId.InvalidElementId)
        if fg_col is not None:
            safe_set(frt, "ForegroundPatternColor", fg_col)
        if bg_col is not None:
            safe_set(frt, "BackgroundPatternColor", bg_col)
        return
    except Exception:
        pass
    if fg_pat is not None:
        safe_set(frt, "FillPatternId", fg_pat.Id)
    if fg_col is not None:
        safe_set(frt, "Color", fg_col)


def get_or_create_fr_type(name, fg_pat, fg_col, bg_pat, bg_col):
    all_frt = list(FilteredElementCollector(doc).OfClass(FilledRegionType).ToElements())
    if not all_frt:
        forms.alert("No Filled Region Type in project. Create one first.", exitscript=True)
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
        new_frt = all_frt[0].Duplicate("_tmp_{}".format(int(time.time())))
        try:
            new_frt.Name = name
        except Exception:
            pass
    _apply_patterns(new_frt, fg_pat, fg_col, bg_pat, bg_col)
    return new_frt


def delete_existing_of_types(type_ids):
    if not type_ids:
        return
    wanted = set(eid_int(t) for t in type_ids)
    doomed = []
    for fr in FilteredElementCollector(doc, view.Id).OfClass(FilledRegion):
        try:
            if eid_int(fr.GetTypeId()) in wanted:
                doomed.append(fr.Id)
        except Exception:
            pass
    # delete in chunks
    chunk = 50
    for i in range(0, len(doomed), chunk):
        part = doomed[i:i + chunk]
        ids = List[ElementId]()
        for x in part:
            ids.Add(x)
        try:
            doc.Delete(ids)
        except Exception:
            for x in part:
                try:
                    doc.Delete(x)
                except Exception:
                    pass


# =============================================================================
# CREATE FR SAFELY
# =============================================================================
def create_fr_safe(fr_type_id, loops):
    """
    Try create filled region with fallbacks.
    Returns (ok, note)
    """
    if not loops:
        return False, "no loops"

    def _try(loop_list):
        t = Transaction(doc, "FR place")
        t.Start()
        _cfg_tx(t)
        try:
            net = List[CurveLoop]()
            for lp in loop_list:
                net.Add(lp)
            FilledRegion.Create(doc, fr_type_id, view.Id, net)
            t.Commit()
            return True
        except Exception:
            try:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
            except Exception:
                pass
            return False

    # 1) all loops
    if len(loops) <= MAX_LOOPS_PER_ELEMENT and _try(loops):
        return True, "all loops"

    # 2) outer only
    if _try([loops[0]]):
        return True, "outer only"

    # 3) each loop separately
    ok_n = 0
    for lp in loops[:MAX_LOOPS_PER_ELEMENT]:
        if _try([lp]):
            ok_n += 1
    if ok_n:
        return True, "individual {}/{}".format(ok_n, len(loops))
    return False, "create failed"


# =============================================================================
# MAIN
# =============================================================================
def main():
    valid_vt = [
        DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan, DB.ViewType.AreaPlan,
        DB.ViewType.Detail, DB.ViewType.DraftingView, DB.ViewType.Section, DB.ViewType.Elevation
    ]
    if view.ViewType not in valid_vt:
        forms.alert("Run from Plan/Section/Elevation/Detail view.", exitscript=True)

    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element, SupportedFilter(),
            "Select Floors / Roofs / Ceilings / Toposolids, then Finish."
        )
    except OperationCanceledException:
        sys.exit()

    elements = [doc.GetElement(r.ElementId) for r in refs]
    elements = [e for e in elements if is_supported(e)]
    if not elements:
        forms.alert("No supported elements selected.", exitscript=True)

    z_view = get_view_z(view)
    created = 0
    skipped = []
    type_ids = set()

    # prep types first
    prepared = []
    t = Transaction(doc, "FR prepare types")
    t.Start()
    _cfg_tx(t)
    try:
        for e in elements:
            label = "{} [{}]".format(e.GetType().Name, eid_int(e.Id))
            try:
                tname = get_type_name(e) + SUFFIX
                mat = get_top_layer_material(e)
                fg_p, fg_c = get_surface_pattern(mat, True)
                bg_p, bg_c = get_surface_pattern(mat, False)
                frt = get_or_create_fr_type(tname, fg_p, fg_c, bg_p, bg_c)
                type_ids.add(frt.Id)
                prepared.append((e, frt, label))
            except Exception as ex:
                skipped.append("{} prep: {}".format(label, ex))
        t.Commit()
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        forms.alert("Type prep failed:\n{}".format(ex), exitscript=True)

    # clear old once
    t = Transaction(doc, "FR clear old")
    t.Start()
    _cfg_tx(t)
    try:
        delete_existing_of_types(type_ids)
        t.Commit()
    except Exception:
        try:
            t.RollBack()
        except Exception:
            pass

    # place one by one with progress
    total = len(prepared)
    with forms.ProgressBar(title="Surface Pattern Region", cancellable=True) as pb:
        for i, (e, frt, label) in enumerate(prepared):
            if pb.cancelled:
                skipped.append("Cancelled by user")
                break
            pb.update_progress(i + 1, total)

            try:
                raw = extract_raw_curves(e)
                if not raw:
                    skipped.append("{} - no boundary".format(label))
                    continue
                if len(raw) > 3000:
                    skipped.append("{} - too complex ({} curves)".format(label, len(raw)))
                    continue

                loops = curves_to_simple_loops(raw, z_view)
                if not loops:
                    skipped.append("{} - loop build failed / too complex".format(label))
                    continue

                loops = orient_loops(loops)
                ok, note = create_fr_safe(frt.Id, loops)
                if ok:
                    created += 1
                    if note != "all loops":
                        skipped.append("{} - {}".format(label, note))
                else:
                    skipped.append("{} - {}".format(label, note))
            except Exception as ex:
                skipped.append("{} - {}".format(label, ex))
                continue

    msg = "Created {} Filled Region(s).".format(created)
    if skipped:
        msg += "\n\nSkipped / Notes:\n" + "\n".join(skipped[:15])
        if len(skipped) > 15:
            msg += "\n...and {} more.".format(len(skipped) - 15)
    forms.alert(msg, title="Surface Pattern Region", warn_icon=(created == 0))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        forms.alert("Script error:\n{}\n\n{}".format(e, traceback.format_exc()), title="Error")