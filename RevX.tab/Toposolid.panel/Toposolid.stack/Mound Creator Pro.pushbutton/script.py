# -*- coding: utf-8 -*-
"""
Mound Creator Pro v5.0 (Revit API Context Architecture)
-------------------------------------------------------
Advanced Mound Creation, 3D DirectShape Live Preview, 2D Profile Canvas Visualizer,
and Surface Sculpting for Revit 2024-2027 (Toposolid) and legacy Revit 2023 (TopographySurface).

Architecture:
  - Revit API ExternalEvent Context: All database modification transactions (Create Mound,
    3D Preview, Clear Preview, Smooth, Slope, Offset, Peak) are dispatched via Revit API
    IExternalEventHandler and ExternalEvent.Raise().
  - Fixes 'Starting a transaction from an external application running outside of API context is not allowed'.
  - 100% thread-safe & API compliant across all Revit versions.

Author: Jesto Joy
"""

from pyrevit import revit, forms, script
from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent

# ----------------------------------------------------------------
# NATIVE REVIT API EXTERNAL EVENT HANDLER
# ----------------------------------------------------------------

class MoundApiHandler(IExternalEventHandler):
    """Dispatches WPF actions safely onto Revit's main API thread."""
    def __init__(self, action_fn, name="MoundApiHandler", log_fn=None):
        self.action_fn = action_fn
        self.name_str = name
        self.log_fn = log_fn

    def Execute(self, uiapp):
        try:
            self.action_fn(uiapp)
        except Exception as ex:
            # CRITICAL: this used to be a bare "except: pass" — ANY exception
            # raised outside (or escaping) the action's own try/except simply
            # vanished, with no popup, no log line, nothing. That silent
            # swallow is why buttons could look like they "do nothing."
            # Always surface it now.
            import traceback
            tb = traceback.format_exc()
            if self.log_fn is not None:
                try:
                    self.log_fn("[{}] Unhandled error: {}".format(self.name_str, ex))
                    self.log_fn(tb.strip().splitlines()[-1])
                except Exception:
                    pass
            try:
                print("MoundApiHandler [{}] unhandled error:\n{}".format(self.name_str, tb))
            except Exception:
                pass

    def GetName(self):
        return self.name_str


# ----------------------------------------------------------------
# SELECTION FILTERS
# ----------------------------------------------------------------

from Autodesk.Revit.UI.Selection import ISelectionFilter

class AnyElementFilter(ISelectionFilter):
    def AllowElement(self, elem): return True
    def AllowReference(self, ref, point): return True


class ModelLineFilter(ISelectionFilter):
    def AllowElement(self, elem):
        from Autodesk.Revit.DB import CurveElement
        return isinstance(elem, CurveElement)
    def AllowReference(self, ref, point): return True


class TopoFilter(ISelectionFilter):
    def __init__(self, checker): self._checker = checker
    def AllowElement(self, elem):
        try: return bool(self._checker(elem))
        except Exception: return False
    def AllowReference(self, ref, point): return True


# ----------------------------------------------------------------
# MAIN WINDOW CLASS
# ----------------------------------------------------------------

class MoundEditorWindow(forms.WPFWindow):

    def __init__(self):
        from System.Windows import Visibility
        forms.WPFWindow.__init__(self, "ui.xaml")
        self.picked_lines = []
        self.picked_boundary_el = None
        self.picked_center_pt = None
        self.picked_target_el = None
        self.slope_dir_pts = None
        self.preview_ds_id = None

        # Setup ExternalEvents for Revit API context dispatching
        self._handler_create = MoundApiHandler(self.do_create_mound_api, "CreateMound", log_fn=self._log)
        self._event_create = ExternalEvent.Create(self._handler_create)

        self._handler_preview = MoundApiHandler(self.do_preview_3d_api, "Preview3D", log_fn=self._log)
        self._event_preview = ExternalEvent.Create(self._handler_preview)

        self._handler_clear_preview = MoundApiHandler(self.do_clear_preview_api, "ClearPreview", log_fn=self._log)
        self._event_clear_preview = ExternalEvent.Create(self._handler_clear_preview)

        self._handler_smooth = MoundApiHandler(self.do_smooth_api, "Smooth", log_fn=self._log)
        self._event_smooth = ExternalEvent.Create(self._handler_smooth)

        self._handler_slope = MoundApiHandler(self.do_slope_api, "Slope", log_fn=self._log)
        self._event_slope = ExternalEvent.Create(self._handler_slope)

        self._handler_offset = MoundApiHandler(self.do_offset_api, "Offset", log_fn=self._log)
        self._event_offset = ExternalEvent.Create(self._handler_offset)

        self._handler_peak = MoundApiHandler(self.do_peak_api, "Peak", log_fn=self._log)
        self._event_peak = ExternalEvent.Create(self._handler_peak)

        doc, uidoc = self.get_doc_and_uidoc()
        version = self._get_version(doc)
        has_toposolid = self._has_toposolid()

        self.TxtVersionInfo.Text = "Revit {}  |  {}".format(
            version, "Toposolid Engine" if (version >= 2024 and has_toposolid) else "TopographySurface (Legacy)")

        self._load_levels_and_types()
        self._wire_events()

        self.Loaded += lambda s, e: self.update_2d_preview()
        if hasattr(self, 'CnvProfilePreview') and self.CnvProfilePreview is not None:
            self.CnvProfilePreview.Loaded += lambda s, e: self.update_2d_preview()
            self.CnvProfilePreview.SizeChanged += lambda s, e: self.update_2d_preview()

    # ---------------- DOCUMENT & API ACCESS HELPERS ----------------

    def get_doc_and_uidoc(self, uiapp=None):
        if uiapp is not None:
            try:
                u = uiapp.ActiveUIDocument
                d = u.Document
                return d, u
            except Exception:
                pass
        try:
            from pyrevit import revit
            d = revit.doc
            u = revit.uidoc
            if d is not None:
                return d, u
        except Exception:
            pass
        try:
            u = __revit__.ActiveUIDocument
            d = u.Document
            return d, u
        except Exception:
            pass
        return None, None

    def _get_version(self, d=None):
        if d is None:
            d, u = self.get_doc_and_uidoc()
        try:
            return int(d.Application.VersionNumber)
        except Exception:
            return 2024

    def _has_toposolid(self):
        try:
            from Autodesk.Revit.DB import Toposolid
            return True
        except Exception:
            return False

    def _get_topography_class(self):
        try:
            from Autodesk.Revit.DB import TopographySurface
            return TopographySurface
        except ImportError:
            try:
                from Autodesk.Revit.DB.Architecture import TopographySurface
                return TopographySurface
            except ImportError:
                return None

    # ---------------- UNIT CONVERSIONS ----------------

    def _mm_to_ft(self, v):
        try:
            from Autodesk.Revit.DB import UnitUtils
            try:
                from Autodesk.Revit.DB import UnitTypeId
                return UnitUtils.ConvertToInternalUnits(float(v), UnitTypeId.Millimeters)
            except Exception:
                from Autodesk.Revit.DB import DisplayUnitType
                return UnitUtils.ConvertToInternalUnits(float(v), DisplayUnitType.DUT_MILLIMETERS)
        except Exception:
            return float(v) / 304.8

    def _ft_to_mm(self, v):
        try:
            from Autodesk.Revit.DB import UnitUtils
            try:
                from Autodesk.Revit.DB import UnitTypeId
                return UnitUtils.ConvertFromInternalUnits(float(v), UnitTypeId.Millimeters)
            except Exception:
                from Autodesk.Revit.DB import DisplayUnitType
                return UnitUtils.ConvertFromInternalUnits(float(v), DisplayUnitType.DUT_MILLIMETERS)
        except Exception:
            return float(v) * 304.8

    def _slope_from_unit(self, value, unit):
        import math
        if unit == "pct": return value / 100.0
        elif unit == "ratio":
            if value <= 0: raise ValueError("Ratio must be positive")
            return 1.0 / value
        else: return math.tan(math.radians(value))

    # ---------------- GEOMETRY & CURVE PROCESSING ----------------

    def _ensure_bound_curve_segments(self, crv, target_z=None):
        from Autodesk.Revit.DB import Line, Arc, XYZ
        is_bound = True
        try:
            is_bound = bool(crv.IsBound)
        except Exception:
            is_bound = True

        if is_bound:
            try:
                sp = crv.GetEndPoint(0)
                ep = crv.GetEndPoint(1)
                z_s = target_z if target_z is not None else sp.Z
                z_e = target_z if target_z is not None else ep.Z
                if isinstance(crv, Line):
                    return [Line.CreateBound(XYZ(sp.X, sp.Y, z_s), XYZ(ep.X, ep.Y, z_e))]
                elif isinstance(crv, Arc):
                    mp = crv.Evaluate(0.5, True)
                    z_m = target_z if target_z is not None else mp.Z
                    return [Arc.Create(XYZ(sp.X, sp.Y, z_s), XYZ(ep.X, ep.Y, z_e), XYZ(mp.X, mp.Y, z_m))]
            except Exception:
                pass

        try:
            pts = list(crv.Tessellate())
            segs = []
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i + 1]
                if p1.DistanceTo(p2) > 1e-6:
                    z1 = target_z if target_z is not None else p1.Z
                    z2 = target_z if target_z is not None else p2.Z
                    segs.append(Line.CreateBound(XYZ(p1.X, p1.Y, z1), XYZ(p2.X, p2.Y, z2)))
            return segs
        except Exception:
            return []

    def _flatten_curve_segments(self, crv, target_z, tol=1e-6):
        return self._ensure_bound_curve_segments(crv, target_z)

    def _build_boundary_loop(self, curves, target_z, tol=1e-4):
        from Autodesk.Revit.DB import CurveLoop
        if not curves:
            return None
        all_segments = []
        for crv in curves:
            all_segments.extend(self._ensure_bound_curve_segments(crv, target_z))
        if len(all_segments) < 2:
            return None
        remaining = all_segments[:]
        ordered = [remaining.pop(0)]
        while remaining:
            last_end = ordered[-1].GetEndPoint(1)
            found = False
            for i, seg in enumerate(remaining):
                start, end = seg.GetEndPoint(0), seg.GetEndPoint(1)
                if last_end.DistanceTo(start) < tol:
                    ordered.append(seg); remaining.pop(i); found = True; break
                elif last_end.DistanceTo(end) < tol:
                    ordered.append(seg.CreateReversed()); remaining.pop(i); found = True; break
            if not found:
                return None
        gap = ordered[-1].GetEndPoint(1).DistanceTo(ordered[0].GetEndPoint(0))
        if gap > 0.05:
            return None
        loop = CurveLoop()
        for seg in ordered:
            if seg.IsBound:
                loop.Append(seg)
        return loop

    def _point_inside_boundary(self, x, y, pts):
        inside = False
        j = len(pts) - 1
        for i in range(len(pts)):
            xi, yi = pts[i].X, pts[i].Y
            xj, yj = pts[j].X, pts[j].Y
            if ((yi > y) != (yj > y)) and \
                    (x < (xj - xi) * (y - yi) / ((yj - yi) + 1e-10) + xi):
                inside = not inside
            j = i
        return inside

    def _nearest_boundary_distance(self, x, y, boundary_pts):
        import math
        min_d = 1e18
        for p in boundary_pts:
            d = math.sqrt((p.X - x) ** 2 + (p.Y - y) ** 2)
            if d < min_d:
                min_d = d
        return min_d

    # ---------------- PROFILE MATH ----------------

    def _quintic_smooth(self, t):
        return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)

    def _calculate_profile_height(self, d, max_interior, target_height, profile_type, smoothness, plateau_ratio, x=0.5, y=0.5, boundary_pts=[]):
        import math
        if max_interior <= 0:
            return 0.0
        t = min(max(d / max_interior, 0.0), 1.0)
        sigma = 0.15 + (smoothness / 100.0) * 0.60

        if profile_type == 0:  # Dome / Smooth Gaussian Hill
            q = self._quintic_smooth(t)
            g = math.exp(-((t - 1.0) ** 2) / (2.0 * sigma * sigma))
            blended = (1.0 - t) * q + t * g
            return target_height * blended

        elif profile_type == 1:  # Mesa / Flat-Top Plateau
            flat_frac = plateau_ratio / 100.0
            if t >= (1.0 - flat_frac * 0.5):
                return target_height
            else:
                norm_t = t / (1.0 - flat_frac * 0.5)
                q = self._quintic_smooth(norm_t)
                return target_height * q

        elif profile_type == 2:  # Cone / Sharp Peak Hill
            exponent = 0.6 + (100.0 - smoothness) / 100.0 * 0.8
            factor = math.pow(t, exponent)
            return target_height * factor

        elif profile_type == 3:  # Ridge / Elongated Crest
            q = self._quintic_smooth(t)
            g = math.exp(-((t - 1.0) ** 2) / (2.0 * sigma * sigma))
            base_h = (1.0 - t) * q + t * g
            ripple = 1.0 + 0.04 * math.sin(float(x) * 0.5) * math.cos(float(y) * 0.5)
            return target_height * base_h * ripple

        return target_height * self._quintic_smooth(t)

    def _create_mound_points(self, boundary_pts, base_z, target_height, grid_spacing, profile_type, smoothness, plateau_ratio, lock_base=True):
        from Autodesk.Revit.DB import XYZ
        min_x = min(p.X for p in boundary_pts); max_x = max(p.X for p in boundary_pts)
        min_y = min(p.Y for p in boundary_pts); max_y = max(p.Y for p in boundary_pts)

        max_interior = 0.0
        x = min_x
        while x <= max_x + 1e-6:
            y = min_y
            while y <= max_y + 1e-6:
                if self._point_inside_boundary(x, y, boundary_pts):
                    d = self._nearest_boundary_distance(x, y, boundary_pts)
                    if d > max_interior:
                        max_interior = d
                y += grid_spacing
            x += grid_spacing

        if max_interior <= 0:
            return []

        pts = []

        if lock_base:
            for bp in boundary_pts:
                pts.append(XYZ(bp.X, bp.Y, base_z))

        x = min_x
        while x <= max_x + 1e-6:
            y = min_y
            while y <= max_y + 1e-6:
                if self._point_inside_boundary(x, y, boundary_pts):
                    d = self._nearest_boundary_distance(x, y, boundary_pts)
                    if lock_base and d < (grid_spacing * 0.4):
                        h = 0.0
                    else:
                        h = self._calculate_profile_height(d, max_interior, target_height, profile_type, smoothness, plateau_ratio, x, y, boundary_pts)
                    pts.append(XYZ(x, y, base_z + h))
                y += grid_spacing
            x += grid_spacing

        return pts

    # ---------------- ELEMENT & CIRCLE / RECT CREATION ----------------

    def _get_solids(self, geom_elem):
        from Autodesk.Revit.DB import Solid, GeometryInstance
        solids = []
        for obj in geom_elem:
            if isinstance(obj, Solid) and obj.Faces.Size > 0 and obj.Volume > 1e-6:
                solids.append(obj)
            elif isinstance(obj, GeometryInstance):
                try:
                    inst_geom = obj.GetInstanceGeometry()
                    solids.extend(self._get_solids(inst_geom))
                except Exception:
                    pass
        return solids

    def _loop_signed_area(self, curve_loop):
        pts = [c.GetEndPoint(0) for c in curve_loop if c.IsBound]
        if not pts: return 0.0
        area = 0.0
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            area += (a.X * b.Y - b.X * a.Y)
        return area / 2.0

    def _get_element_footprint_curves(self, el):
        from Autodesk.Revit.DB import Options, ViewDetailLevel, Line, XYZ
        opt = Options()
        opt.ComputeReferences = False
        opt.DetailLevel = ViewDetailLevel.Fine
        try:
            geom = el.get_Geometry(opt)
        except Exception:
            geom = None

        best_face = None
        best_area = 0.0
        if geom is not None:
            for solid in self._get_solids(geom):
                for face in solid.Faces:
                    try:
                        normal = face.ComputeNormal(face.GetBoundingBox().Min)
                    except Exception:
                        continue
                    if abs(normal.Z) < 0.98:
                        continue
                    area = face.Area
                    if area > best_area:
                        best_area = area
                        best_face = face

        if best_face is not None:
            loops = best_face.GetEdgesAsCurveLoops()
            if loops and loops.Count > 0:
                outer = max(loops, key=lambda lp: abs(self._loop_signed_area(lp)))
                res = []
                for c in outer:
                    res.extend(self._ensure_bound_curve_segments(c))
                return res

        try:
            bb = el.get_BoundingBox(None)
            if bb is not None:
                z = bb.Min.Z
                p1 = XYZ(bb.Min.X, bb.Min.Y, z)
                p2 = XYZ(bb.Max.X, bb.Min.Y, z)
                p3 = XYZ(bb.Max.X, bb.Max.Y, z)
                p4 = XYZ(bb.Min.X, bb.Max.Y, z)
                return [Line.CreateBound(p1, p2), Line.CreateBound(p2, p3),
                        Line.CreateBound(p3, p4), Line.CreateBound(p4, p1)]
        except Exception:
            pass
        return []

    def _create_circle_curves(self, center_pt, radius_ft):
        from Autodesk.Revit.DB import Arc, XYZ
        cx, cy, cz = center_pt.X, center_pt.Y, center_pt.Z
        p1 = XYZ(cx + radius_ft, cy, cz)
        p2 = XYZ(cx - radius_ft, cy, cz)
        m1 = XYZ(cx, cy + radius_ft, cz)
        m2 = XYZ(cx, cy - radius_ft, cz)
        arc1 = Arc.Create(p1, p2, m1)
        arc2 = Arc.Create(p2, p1, m2)
        return [arc1, arc2]

    def _create_rect_curves(self, center_pt, len_x_ft, width_y_ft):
        from Autodesk.Revit.DB import Line, XYZ
        cx, cy, cz = center_pt.X, center_pt.Y, center_pt.Z
        hx, hy = len_x_ft / 2.0, width_y_ft / 2.0
        p1 = XYZ(cx - hx, cy - hy, cz)
        p2 = XYZ(cx + hx, cy - hy, cz)
        p3 = XYZ(cx + hx, cy + hy, cz)
        p4 = XYZ(cx - hx, cy + hy, cz)
        return [Line.CreateBound(p1, p2), Line.CreateBound(p2, p3),
                Line.CreateBound(p3, p4), Line.CreateBound(p4, p1)]

    # ---------------- EXISTING SURFACE ACCESS ----------------

    def _is_topography(self, el):
        topo_cls = self._get_topography_class()
        if topo_cls is not None and isinstance(el, topo_cls):
            return True
        if self._has_toposolid():
            from Autodesk.Revit.DB import Toposolid
            if isinstance(el, Toposolid):
                return True
        return False

    def _require_target(self):
        """Used by Smooth / Slope / Offset / Peak (Modify Existing tab).
        Returns the picked target element, re-resolved from the live
        document, or None (after logging why) if nothing usable is picked.
        """
        if self.picked_target_el is None:
            self._log("Pick a target Toposolid / Topography first (Modify Existing tab).")
            return None
        try:
            doc, uidoc = self.get_doc_and_uidoc()
            el = doc.GetElement(self.picked_target_el.Id)
            if el is None:
                self._log("Picked target element no longer exists — pick it again.")
                self.picked_target_el = None
                return None
            return el
        except Exception:
            return self.picked_target_el

    # ---------------- Z-REFERENCE BASELINE (Toposolid shape editing) ----------------
    # ModifySubElement's value is an ABSOLUTE offset from the slab's flat
    # (un-shape-edited) baseline elevation — NOT the absolute world Z you
    # want the point to end up at, and NOT a delta from wherever the vertex
    # currently sits. Passing an absolute Z straight in silently places
    # every point off by the baseline elevation (level elevation + height
    # offset). Probe the true baseline once and always work from it.
    def _get_height_offset_param(self, el):
        from Autodesk.Revit.DB import BuiltInParameter
        bips = [
            getattr(BuiltInParameter, "FLOOR_HEIGHTABOVELEVEL_PARAM", None),
            getattr(BuiltInParameter, "TOPOSOLID_HEIGHTABOVELEVEL_PARAM", None),
        ]
        for bip in bips:
            if bip is None:
                continue
            try:
                p = el.get_Parameter(bip)
                if p is not None and p.HasValue:
                    return p.AsDouble()
            except Exception:
                pass
        return 0.0

    def _get_level_elevation(self, el):
        try:
            doc, uidoc = self.get_doc_and_uidoc()
            lvl = doc.GetElement(el.LevelId)
            if lvl is not None:
                return lvl.Elevation
        except Exception:
            pass
        return 0.0

    def _get_param_datum_z(self, el):
        """Fallback flat baseline if the probe below fails."""
        return self._get_level_elevation(el) + self._get_height_offset_param(el)

    def _detect_ref_z(self, doc, target_elem, editor, probe_vertex):
        """Probe the TRUE flat baseline Z that ModifySubElement measures
        from, by applying a 0.0 offset inside a throwaway SubTransaction and
        reading back the resulting position — then rolling back so nothing
        is actually changed. Matches the vertex by nearest XY afterward
        since the vertex object itself may be invalidated by the regenerate.
        """
        from Autodesk.Revit.DB import SubTransaction
        ref_z = None
        sub = SubTransaction(doc)
        sub.Start()
        try:
            editor.ModifySubElement(probe_vertex, 0.0)
            doc.Regenerate()
            editor2 = target_elem.GetSlabShapeEditor()
            if editor2 is not None:
                verts = list(editor2.SlabShapeVertices) if editor2.SlabShapeVertices else []
                if verts:
                    ox, oy = probe_vertex.Position.X, probe_vertex.Position.Y
                    best_v, best_d = verts[0], float("inf")
                    for vv in verts:
                        d = (vv.Position.X - ox) ** 2 + (vv.Position.Y - oy) ** 2
                        if d < best_d:
                            best_d, best_v = d, vv
                    ref_z = best_v.Position.Z
        except Exception:
            pass
        finally:
            sub.RollBack()
        return ref_z

    def _get_points(self, el):
        topo_cls = self._get_topography_class()
        if topo_cls is not None and isinstance(el, topo_cls):
            return list(el.GetPoints())
        if self._has_toposolid():
            from Autodesk.Revit.DB import Toposolid
            if isinstance(el, Toposolid):
                editor = el.GetSlabShapeEditor()
                return [v.Position for v in editor.SlabShapeVertices]
        raise Exception("Unsupported element type for point access.")

    def _set_points(self, el, new_points, old_points):
        from System.Collections.Generic import List
        from Autodesk.Revit.DB import XYZ
        topo_cls = self._get_topography_class()
        if topo_cls is not None and isinstance(el, topo_cls):
            el.DeletePoints(List[XYZ](old_points))
            el.AddPoints(List[XYZ](new_points))
            return
        if self._has_toposolid():
            from Autodesk.Revit.DB import Toposolid
            if isinstance(el, Toposolid):
                doc, uidoc = self.get_doc_and_uidoc()
                editor = el.GetSlabShapeEditor()
                vertices = list(editor.SlabShapeVertices) if editor.SlabShapeVertices else []
                if len(vertices) == len(new_points):
                    ref_z = self._detect_ref_z(doc, el, editor, vertices[0]) if vertices else None
                    if ref_z is None:
                        ref_z = self._get_param_datum_z(el)

                    n = len(vertices)
                    for i in range(n):
                        # Re-fetch fresh each time rather than trusting a
                        # vertex handle held across a Regenerate() — old
                        # handles can go stale once one is called.
                        editor = el.GetSlabShapeEditor()
                        live_verts = list(editor.SlabShapeVertices) if editor.SlabShapeVertices else []
                        if i >= len(live_verts):
                            continue
                        v = live_verts[i]
                        value = new_points[i].Z - ref_z  # ABSOLUTE offset from flat baseline
                        try:
                            editor.ModifySubElement(v, value)
                            doc.Regenerate()  # commit before the next point is read/matched
                        except Exception:
                            pass
                else:
                    for pt in new_points:
                        try:
                            editor.AddPoint(pt)
                        except Exception:
                            pass
                return
        raise Exception("Unsupported element type for point editing.")

    def _estimate_spacing(self, pts):
        import math
        if len(pts) < 2: return 2.0
        sample = pts[:min(len(pts), 40)]
        dists = []
        for p in sample:
            best = None
            for q in pts:
                if p is q: continue
                d = math.hypot(p.X - q.X, p.Y - q.Y)
                if best is None or d < best: best = d
            if best: dists.append(best)
        return sum(dists) / len(dists) if dists else 2.0

    # ---------------- SETUP & DROPDOWNS ----------------

    def _load_levels_and_types(self):
        from System.Windows import Visibility
        from Autodesk.Revit.DB import FilteredElementCollector, Level
        doc, uidoc = self.get_doc_and_uidoc()
        version = self._get_version(doc)
        has_toposolid = self._has_toposolid()

        levels = FilteredElementCollector(doc).OfClass(Level).ToElements()
        self.CmbLevel.Items.Clear()
        for lv in sorted(levels, key=lambda l: l.Elevation):
            self.CmbLevel.Items.Add(lv.Name)
        if self.CmbLevel.Items.Count:
            self.CmbLevel.SelectedIndex = 0
        self._levels_by_name = dict((lv.Name, lv) for lv in levels)

        if version >= 2024 and has_toposolid:
            from Autodesk.Revit.DB import ToposolidType
            types = FilteredElementCollector(doc).OfClass(ToposolidType).ToElements()
            self.CmbToposolidType.Items.Clear()
            for t in types:
                try:
                    name = t.Name
                except Exception:
                    from Autodesk.Revit.DB import Element
                    name = Element.Name.__get__(t)
                self.CmbToposolidType.Items.Add(name)
            if self.CmbToposolidType.Items.Count:
                self.CmbToposolidType.SelectedIndex = 0
            self._types_by_name = dict()
            for t in types:
                try:
                    name = t.Name
                except Exception:
                    from Autodesk.Revit.DB import Element
                    name = Element.Name.__get__(t)
                self._types_by_name[name] = t
            self.TxtHostNote.Text = "Creates a native Toposolid (Revit 2024+)."
        else:
            self.PanelToposolidType.Visibility = Visibility.Collapsed
            self.TxtHostNote.Text = ("Revit {} creates a legacy TopographySurface.").format(version)

    def _wire_events(self):
        self.CmbSourceMode.SelectionChanged += self.on_source_mode_changed
        self.CmbProfileType.SelectionChanged += lambda s, e: (self.on_profile_type_changed(s, e), self.update_2d_preview())

        self.BtnPickBoundaryLines.Click += self.on_pick_boundary_lines
        self.BtnPickSourceElement.Click += self.on_pick_source_element
        self.BtnPickCenterPtCircle.Click += self.on_pick_center_pt_circle
        self.BtnPickCenterPtRect.Click += self.on_pick_center_pt_rect

        self.BtnPresetGentle.Click += lambda s, e: self._apply_preset(30, 75, 1200, 0)
        self.BtnPresetSteep.Click += lambda s, e: self._apply_preset(75, 40, 2200, 0)
        self.BtnPresetMesa.Click += lambda s, e: self._apply_preset(50, 60, 1800, 1)
        self.BtnPresetPeak.Click += lambda s, e: self._apply_preset(90, 20, 2500, 2)

        self.BtnQuickHeightMinus500.Click += lambda s, e: self._adjust_peak_height(-500)
        self.BtnQuickHeightMinus100.Click += lambda s, e: self._adjust_peak_height(-100)
        self.BtnQuickHeightPlus100.Click += lambda s, e: self._adjust_peak_height(100)
        self.BtnQuickHeightPlus500.Click += lambda s, e: self._adjust_peak_height(500)

        self.SldSmoothness.ValueChanged += lambda s, e: (self._sync_label(self.TxtSmoothVal, self.SldSmoothness, "{:.0f}"), self.update_2d_preview())
        self.SldDensity.ValueChanged += lambda s, e: self._sync_label(self.TxtDensityVal, self.SldDensity, "{:.1f} ft")
        self.SldPlateauRatio.ValueChanged += lambda s, e: (self._sync_label(self.TxtPlateauRatioVal, self.SldPlateauRatio, "{:.0f}%"), self.update_2d_preview())

        self.BtnPreview3D.Click += self.on_preview_3d
        self.BtnClearPreview.Click += self.on_clear_preview
        self.BtnCreateMound.Click += self.on_create_mound

        # Modify Tab
        self.BtnPickTarget.Click += self.on_pick_target
        self.BtnModPeakMinus1000.Click += lambda s, e: self._adjust_mod_peak(-1000)
        self.BtnModPeakMinus250.Click += lambda s, e: self._adjust_mod_peak(-250)
        self.BtnModPeakPlus250.Click += lambda s, e: self._adjust_mod_peak(250)
        self.BtnModPeakPlus1000.Click += lambda s, e: self._adjust_mod_peak(1000)

        self.SldSmoothStrength.ValueChanged += lambda s, e: self._sync_label(
            self.TxtSmoothStrengthVal, self.SldSmoothStrength, "{:.0f}")
        self.BtnApplySmoothing.Click += self.on_apply_smoothing
        self.BtnPickSlopeDir.Click += self.on_pick_slope_dir
        self.BtnApplySlope.Click += self.on_apply_slope
        self.BtnApplyOffset.Click += self.on_apply_offset
        self.BtnApplyPeak.Click += self.on_apply_peak

        self.Closed += self.on_window_closed

    def _sync_label(self, label, slider, fmt):
        label.Text = fmt.format(slider.Value)

    def _log(self, msg):
        self.TxtLog.Text = self.TxtLog.Text + "\n" + msg
        try:
            sv = self.TxtLog.Parent
            while sv is not None and not hasattr(sv, "ScrollToEnd"):
                sv = sv.Parent
            if sv is not None:
                sv.ScrollToEnd()
        except Exception:
            pass

    def _log_error(self, prefix, ex):
        import traceback
        self._log("{}: {}".format(prefix, ex))
        self._log(traceback.format_exc().strip().splitlines()[-1])

    # ---------------- 2D PROFILE CANVAS DRAWING ----------------

    def update_2d_preview(self):
        try:
            if not hasattr(self, 'CnvProfilePreview') or self.CnvProfilePreview is None:
                return
            self.CnvProfilePreview.Children.Clear()
            w = self.CnvProfilePreview.ActualWidth if self.CnvProfilePreview.ActualWidth > 20 else 420.0
            h = self.CnvProfilePreview.ActualHeight if self.CnvProfilePreview.ActualHeight > 10 else 50.0
            margin_y = 6.0
            usable_h = h - margin_y * 2

            profile_type = self.CmbProfileType.SelectedIndex
            smoothness = self.SldSmoothness.Value
            plateau_ratio = self.SldPlateauRatio.Value

            from System.Windows.Shapes import Polyline, Line as WpfLine
            from System.Windows.Media import SolidColorBrush, Color
            base_line = WpfLine()
            base_line.X1 = 10; base_line.Y1 = h - margin_y
            base_line.X2 = w - 10; base_line.Y2 = h - margin_y
            base_line.Stroke = SolidColorBrush(Color.FromRgb(200, 210, 205))
            base_line.StrokeThickness = 1
            self.CnvProfilePreview.Children.Add(base_line)

            polyline = Polyline()
            polyline.Stroke = SolidColorBrush(Color.FromRgb(30, 130, 76))
            polyline.StrokeThickness = 2.2

            from System.Windows import Point
            num_pts = 60
            for i in range(num_pts + 1):
                frac = float(i) / num_pts
                d_norm = 1.0 - abs(frac - 0.5) * 2.0
                rel_h = self._calculate_profile_height(
                    d_norm, 1.0, 1.0, profile_type, smoothness, plateau_ratio, frac, 0.5, []
                )
                px = 10.0 + frac * (w - 20.0)
                py = (h - margin_y) - rel_h * usable_h
                polyline.Points.Add(Point(px, py))

            self.CnvProfilePreview.Children.Add(polyline)
        except Exception:
            pass

    # ---------------- UI EVENT HELPERS ----------------

    def on_source_mode_changed(self, sender, args):
        from System.Windows import Visibility
        mode = self.CmbSourceMode.SelectedIndex
        self.PanelLinesSource.Visibility = Visibility.Visible if mode == 0 else Visibility.Collapsed
        self.PanelElementSource.Visibility = Visibility.Visible if mode == 1 else Visibility.Collapsed
        self.PanelCircleSource.Visibility = Visibility.Visible if mode == 2 else Visibility.Collapsed
        self.PanelRectSource.Visibility = Visibility.Visible if mode == 3 else Visibility.Collapsed

    def on_profile_type_changed(self, sender, args):
        from System.Windows import Visibility
        profile = self.CmbProfileType.SelectedIndex
        self.PanelPlateauRatio.Visibility = Visibility.Visible if profile == 1 else Visibility.Collapsed

    def _apply_preset(self, smoothness, density_x10, height_mm, profile_idx):
        self.SldSmoothness.Value = smoothness
        self.TxtPeakHeight.Text = str(height_mm)
        self.CmbProfileType.SelectedIndex = profile_idx
        self.update_2d_preview()
        self._log("Applied preset profile.")

    def _adjust_peak_height(self, delta_mm):
        try:
            val = float(self.TxtPeakHeight.Text.strip()) if self.TxtPeakHeight.Text.strip() else 0.0
            self.TxtPeakHeight.Text = str(int(val + delta_mm))
        except Exception:
            self.TxtPeakHeight.Text = "1500"

    def _adjust_mod_peak(self, delta_mm):
        try:
            val = float(self.TxtPeakTarget.Text.strip()) if self.TxtPeakTarget.Text.strip() else 0.0
            self.TxtPeakTarget.Text = str(int(val + delta_mm))
        except Exception:
            self.TxtPeakTarget.Text = "1500"

    # ---------------- PICKING FUNCTIONS ----------------

    def on_pick_boundary_lines(self, sender, args):
        from Autodesk.Revit.UI.Selection import ObjectType
        doc, uidoc = self.get_doc_and_uidoc()
        self.Hide()
        try:
            refs = uidoc.Selection.PickObjects(
                ObjectType.Element, ModelLineFilter(),
                "Pick Model / Detail Lines forming a closed mound boundary loop")
            lines = [doc.GetElement(r.ElementId) for r in refs]
            self.picked_lines = lines
            self.TxtLinesSourceInfo.Text = "{} line(s) selected".format(len(lines))
            self._log("Picked {} boundary line(s).".format(len(lines)))
        except Exception:
            pass
        finally:
            self.Show()

    def on_pick_source_element(self, sender, args):
        from Autodesk.Revit.UI.Selection import ObjectType
        doc, uidoc = self.get_doc_and_uidoc()
        self.Hide()
        try:
            ref = uidoc.Selection.PickObject(
                ObjectType.Element, AnyElementFilter(),
                "Pick a floor / toposolid / pad to trace footprint")
            el = doc.GetElement(ref.ElementId)
            self.picked_boundary_el = el
            try: name = el.Name
            except Exception: name = el.Category.Name if el.Category else "Element"
            self.TxtSourceElementInfo.Text = "{} (Id {})".format(name, el.Id.IntegerValue)
            self._log("Picked footprint source element Id {}".format(el.Id.IntegerValue))
        except Exception:
            pass
        finally:
            self.Show()

    def on_pick_center_pt_circle(self, sender, args):
        doc, uidoc = self.get_doc_and_uidoc()
        self.Hide()
        try:
            pt = uidoc.Selection.PickPoint("Pick center point for circular mound")
            self.picked_center_pt = pt
            self.TxtCenterPtCircleInfo.Text = "Center: ({:.1f}, {:.1f}, {:.1f})".format(pt.X, pt.Y, pt.Z)
            self._log("Picked center point for circle.")
        except Exception:
            pass
        finally:
            self.Show()

    def on_pick_center_pt_rect(self, sender, args):
        doc, uidoc = self.get_doc_and_uidoc()
        self.Hide()
        try:
            pt = uidoc.Selection.PickPoint("Pick center point for rectangular mound")
            self.picked_center_pt = pt
            self.TxtCenterPtRectInfo.Text = "Center: ({:.1f}, {:.1f}, {:.1f})".format(pt.X, pt.Y, pt.Z)
            self._log("Picked center point for rectangle.")
        except Exception:
            pass
        finally:
            self.Show()

    def on_pick_target(self, sender, args):
        from Autodesk.Revit.UI.Selection import ObjectType
        doc, uidoc = self.get_doc_and_uidoc()
        self.Hide()
        try:
            ref = uidoc.Selection.PickObject(
                ObjectType.Element, TopoFilter(self._is_topography),
                "Pick an existing Toposolid / TopographySurface")
            el = doc.GetElement(ref.ElementId)
            self.picked_target_el = el
            self.TxtPickedTarget.Text = "{} (Id {})".format(type(el).__name__, el.Id.IntegerValue)
            self._log("Target set: Id {}".format(el.Id.IntegerValue))
        except Exception:
            pass
        finally:
            self.Show()

    def on_pick_slope_dir(self, sender, args):
        doc, uidoc = self.get_doc_and_uidoc()
        self.Hide()
        try:
            p1 = uidoc.Selection.PickPoint("Pick FIRST point of the slope direction (low/start)")
            p2 = uidoc.Selection.PickPoint("Pick SECOND point of the slope direction (high/end)")
            self.slope_dir_pts = (p1, p2)
            self.TxtSlopeDir.Text = "({:.1f}, {:.1f}) -> ({:.1f}, {:.1f})".format(
                p1.X, p1.Y, p2.X, p2.Y
            )
            self._log("Picked slope direction vector.")
        except Exception:
            pass
        finally:
            self.Show()

    # ---------------- PARAMETER EXTRACTION ----------------

    def _extract_curves_and_params(self):
        doc, uidoc = self.get_doc_and_uidoc()
        source_mode = self.CmbSourceMode.SelectedIndex
        raw_curves = []

        if source_mode == 0:  # Pick Model/Detail Lines
            if not self.picked_lines:
                self._log("Pick Model or Detail Lines first.")
                return None
            for el in self.picked_lines:
                try:
                    if hasattr(el, 'GeometryCurve'):
                        raw_curves.append(el.GeometryCurve)
                    elif hasattr(el, 'Location') and hasattr(el.Location, 'Curve'):
                        raw_curves.append(el.Location.Curve)
                except Exception:
                    pass
            if not raw_curves:
                self._log("Could not extract curves from picked lines.")
                return None

        elif source_mode == 1:  # Element footprint
            if self.picked_boundary_el is None:
                self._log("Pick a source element first.")
                return None
            raw_curves = self._get_element_footprint_curves(self.picked_boundary_el)
            if not raw_curves:
                self._log("Could not extract boundary curves from picked element.")
                return None

        elif source_mode == 2:  # Circle
            if self.picked_center_pt is None:
                self._log("Pick a center point first.")
                return None
            rad_mm = float(self.TxtCircleRadius.Text.strip())
            rad_ft = self._mm_to_ft(rad_mm)
            raw_curves = self._create_circle_curves(self.picked_center_pt, rad_ft)

        elif source_mode == 3:  # Rect
            if self.picked_center_pt is None:
                self._log("Pick a center point first.")
                return None
            len_mm = float(self.TxtRectLength.Text.strip())
            wid_mm = float(self.TxtRectWidth.Text.strip())
            raw_curves = self._create_rect_curves(self.picked_center_pt, self._mm_to_ft(len_mm), self._mm_to_ft(wid_mm))

        curves = []
        for crv in raw_curves:
            curves.extend(self._ensure_bound_curve_segments(crv))

        if not curves:
            self._log("Failed to process bound curve segments.")
            return None

        base_z = min(c.GetEndPoint(0).Z for c in curves if c.IsBound)

        height_txt = self.TxtPeakHeight.Text.strip()
        slope_txt = self.TxtSlopeRatio.Text.strip()

        boundary_pts = []
        for c in curves:
            if not c.IsBound: continue
            try:
                for i in range(31):
                    boundary_pts.append(c.Evaluate(i / 30.0, True))
            except Exception:
                boundary_pts.extend(c.Tessellate())

        if not boundary_pts:
            self._log("Could not evaluate boundary points.")
            return None

        if height_txt:
            target_height = self._mm_to_ft(float(height_txt))
        elif slope_txt:
            ratio = float(slope_txt)
            if ratio <= 0: raise ValueError("Slope ratio must be positive")
            min_x = min(p.X for p in boundary_pts); max_x = max(p.X for p in boundary_pts)
            min_y = min(p.Y for p in boundary_pts); max_y = max(p.Y for p in boundary_pts)
            max_interior = max(max_x - min_x, max_y - min_y) / 2.0
            target_height = max_interior / ratio
        else:
            self._log("Specify height or slope ratio.")
            return None

        grid_spacing = self.SldDensity.Value
        smoothness = self.SldSmoothness.Value
        profile_type = self.CmbProfileType.SelectedIndex
        plateau_ratio = self.SldPlateauRatio.Value
        lock_base = bool(self.ChkLockBase.IsChecked)

        grid_pts = self._create_mound_points(
            boundary_pts, base_z, target_height, grid_spacing,
            profile_type, smoothness, plateau_ratio, lock_base
        )

        return (curves, boundary_pts, grid_pts, base_z, target_height)

    # ---------------- WPF EVENT CALLBACKS (RAISE EXTERNAL EVENTS) ----------------

    def on_preview_3d(self, sender, args):
        self._event_preview.Raise()

    def on_clear_preview(self, sender, args):
        self._event_clear_preview.Raise()

    def on_create_mound(self, sender, args):
        self._event_create.Raise()

    def on_apply_smoothing(self, sender, args):
        self._event_smooth.Raise()

    def on_apply_slope(self, sender, args):
        self._event_slope.Raise()

    def on_apply_offset(self, sender, args):
        self._event_offset.Raise()

    def on_apply_peak(self, sender, args):
        self._event_peak.Raise()

    def on_window_closed(self, sender, args):
        self._event_clear_preview.Raise()

    # ---------------- REVIT API CONTEXT DISPATCHED METHODS ----------------

    def do_preview_3d_api(self, uiapp):
        import traceback
        doc, uidoc = self.get_doc_and_uidoc(uiapp)
        try:
            data = self._extract_curves_and_params()
            if not data: return
            curves, boundary_pts, grid_pts, base_z, target_height = data

            self._clear_preview_internal_api(doc)

            from Autodesk.Revit.DB import Transaction
            t = Transaction(doc, "Mound 3D DirectShape Preview")
            t.Start()
            try:
                from System.Collections.Generic import List
                from Autodesk.Revit.DB import DirectShape, BuiltInCategory, ElementId, XYZ, Line, GeometryObject
                cat_id = ElementId(BuiltInCategory.OST_GenericModel)
                ds = DirectShape.CreateElement(doc, cat_id)
                ds.ApplicationId = "MoundCreatorPro"
                ds.ApplicationDataId = "PreviewMesh"

                shape_objs = List[GeometryObject]()

                sample_pts = grid_pts[::max(1, len(grid_pts) // 150)]
                for pt in sample_pts:
                    try:
                        p1 = XYZ(pt.X, pt.Y, pt.Z)
                        p2 = XYZ(pt.X, pt.Y, pt.Z + 0.3)
                        shape_objs.Add(Line.CreateBound(p1, p2))
                    except Exception:
                        pass

                for i in range(len(boundary_pts) - 1):
                    try:
                        p1 = boundary_pts[i]
                        p2 = boundary_pts[i + 1]
                        if p1.DistanceTo(p2) > 1e-5:
                            shape_objs.Add(Line.CreateBound(p1, p2))
                    except Exception:
                        pass

                ds.SetShape(shape_objs)
                self.preview_ds_id = ds.Id
                t.Commit()
                self._log("3D Preview rendered in Revit view ({} points).".format(len(grid_pts)))

            except Exception as ex:
                t.RollBack()
                self._log_error("3D Preview failed", ex)

        except Exception as ex:
            self._log_error("3D Preview failed", ex)

    def do_clear_preview_api(self, uiapp):
        doc, uidoc = self.get_doc_and_uidoc(uiapp)
        self._clear_preview_internal_api(doc)
        self._log("Cleared 3D preview.")

    def _clear_preview_internal_api(self, doc=None):
        if doc is None:
            doc, uidoc = self.get_doc_and_uidoc()
        from Autodesk.Revit.DB import Transaction
        if self.preview_ds_id is not None and doc is not None:
            t = Transaction(doc, "Clear Mound Preview")
            t.Start()
            try:
                doc.Delete(self.preview_ds_id)
                self.preview_ds_id = None
                t.Commit()
            except Exception:
                t.RollBack()

    def do_create_mound_api(self, uiapp):
        import traceback
        doc, uidoc = self.get_doc_and_uidoc(uiapp)
        version = self._get_version(doc)
        has_toposolid = self._has_toposolid()

        try:
            data = self._extract_curves_and_params()
            if not data: return
            curves, boundary_pts, grid_pts, base_z, target_height = data

            if len(grid_pts) < 3:
                self._log("Boundary footprint too small for grid density - reduce grid spacing.")
                return

            loop = self._build_boundary_loop(curves, base_z)
            if loop is None:
                self._log("Footprint curves do not form a closed loop.")
                return

            from System.Collections.Generic import List
            from Autodesk.Revit.DB import CurveLoop, Transaction, XYZ
            boundaries = List[CurveLoop]()
            boundaries.Add(loop)

            level_name = self.CmbLevel.SelectedItem
            level = self._levels_by_name.get(str(level_name)) if level_name else None

            self._clear_preview_internal_api(doc)

            t = Transaction(doc, "Create Mound")
            t.Start()
            try:
                if version >= 2024 and has_toposolid:
                    from Autodesk.Revit.DB import Toposolid
                    type_name = self.CmbToposolidType.SelectedItem
                    topo_type = self._types_by_name.get(str(type_name))
                    if topo_type is None or level is None:
                        raise Exception("Select level and toposolid type first.")

                    try:
                        ts = Toposolid.Create(doc, boundaries, List[XYZ](grid_pts), topo_type.Id, level.Id)
                    except Exception:
                        ts = Toposolid.Create(doc, boundaries, topo_type.Id, level.Id)
                        editor = ts.GetSlabShapeEditor()
                        for pt in grid_pts:
                            try:
                                editor.AddPoint(pt)
                            except Exception:
                                pass
                else:
                    topo_cls = self._get_topography_class()
                    if topo_cls is None:
                        raise Exception("TopographySurface unavailable.")
                    topo_cls.Create(doc, List[XYZ](grid_pts))

                t.Commit()
                self._log("Mound created successfully ({} points).".format(len(grid_pts)))

            except Exception as ex:
                t.RollBack()
                self._log_error("Create mound transaction failed", ex)

        except Exception as ex:
            self._log_error("Create mound failed", ex)

    def do_smooth_api(self, uiapp):
        import math
        import traceback
        doc, uidoc = self.get_doc_and_uidoc(uiapp)
        el = self._require_target()
        if el is None: return
        try:
            strength = self.SldSmoothStrength.Value / 100.0
            lock_edges = bool(self.ChkSmoothLockEdges.IsChecked)
            try: passes = max(1, int(self.TxtSmoothPasses.Text.strip()))
            except Exception: passes = 1

            pts = self._get_points(el)
            if len(pts) < 3:
                self._log("Not enough points to smooth.")
                return

            min_z = min(p.Z for p in pts)
            search_r = self._estimate_spacing(pts) * 1.8
            work = list(pts)

            for _ in range(passes):
                new_z = []
                for p in work:
                    if lock_edges and abs(p.Z - min_z) < 0.05:
                        new_z.append(p.Z)
                        continue
                    neighbours = [q for q in work if 0 < math.hypot(p.X - q.X, p.Y - q.Y) <= search_r]
                    if not neighbours:
                        new_z.append(p.Z)
                        continue
                    avg = sum(q.Z for q in neighbours) / len(neighbours)
                    new_z.append(p.Z + (avg - p.Z) * strength)
                work = [XYZ(p.X, p.Y, z) for p, z in zip(work, new_z)]

            from Autodesk.Revit.DB import Transaction, XYZ
            t = Transaction(doc, "Smooth Surface")
            t.Start()
            try:
                self._set_points(el, work, pts)
                t.Commit()
                self._log("Smoothing applied ({} passes, strength {:.0f}%).".format(passes, strength * 100))
            except Exception as ex:
                t.RollBack()
                self._log_error("Smoothing failed", ex)
        except Exception as ex:
            self._log_error("Smoothing failed", ex)

    def do_slope_api(self, uiapp):
        import math
        import traceback
        doc, uidoc = self.get_doc_and_uidoc(uiapp)
        el = self._require_target()
        if el is None: return
        if self.slope_dir_pts is None:
            self._log("Pick slope direction vector first.")
            return
        try:
            unit_map = {0: "pct", 1: "ratio", 2: "deg"}
            unit = unit_map.get(self.CmbSlopeUnit.SelectedIndex, "pct")
            value = float(self.TxtSlopeValue.Text.strip())
            target_slope = self._slope_from_unit(value, unit)

            p1, p2 = self.slope_dir_pts
            dx, dy = p2.X - p1.X, p2.Y - p1.Y
            length = math.hypot(dx, dy)
            if length < 1e-6:
                self._log("Pick two distinct points for slope direction.")
                return
            ux, uy = dx / length, dy / length

            pts = self._get_points(el)
            projections = [((p.X - p1.X) * ux + (p.Y - p1.Y) * uy) for p in pts]
            min_t, max_t = min(projections), max(projections)
            run = max_t - min_t
            if run < 1e-6:
                self._log("Picked direction vector has zero length across surface.")
                return
            total_rise = target_slope * run

            from Autodesk.Revit.DB import XYZ, Transaction
            new_pts = []
            for p, t in zip(pts, projections):
                frac = (t - min_t) / run
                new_pts.append(XYZ(p.X, p.Y, p.Z + frac * total_rise))

            low_idx = projections.index(min_t)
            base_shift = new_pts[low_idx].Z - pts[low_idx].Z
            new_pts = [XYZ(p.X, p.Y, p.Z - base_shift) for p in new_pts]

            trans = Transaction(doc, "Set Surface Slope")
            trans.Start()
            try:
                self._set_points(el, new_pts, pts)
                trans.Commit()
                self._log("Slope applied: {} {} along vector.".format(value, unit))
            except Exception as ex:
                trans.RollBack()
                self._log_error("Slope failed", ex)
        except Exception as ex:
            self._log_error("Slope failed", ex)

    def do_offset_api(self, uiapp):
        import traceback
        doc, uidoc = self.get_doc_and_uidoc(uiapp)
        el = self._require_target()
        if el is None: return
        try:
            offset_ft = self._mm_to_ft(float(self.TxtHeightOffset.Text.strip()))
            pts = self._get_points(el)
            from Autodesk.Revit.DB import XYZ, Transaction
            new_pts = [XYZ(p.X, p.Y, p.Z + offset_ft) for p in pts]
            t = Transaction(doc, "Offset Surface Height")
            t.Start()
            try:
                self._set_points(el, new_pts, pts)
                t.Commit()
                self._log("Offset all points by {} mm.".format(self.TxtHeightOffset.Text.strip()))
            except Exception as ex:
                t.RollBack()
                self._log_error("Offset failed", ex)
        except Exception as ex:
            self._log_error("Offset failed", ex)

    def do_peak_api(self, uiapp):
        import traceback
        doc, uidoc = self.get_doc_and_uidoc(uiapp)
        el = self._require_target()
        if el is None: return
        try:
            target_peak_ft = self._mm_to_ft(float(self.TxtPeakTarget.Text.strip()))
            pts = self._get_points(el)
            min_z = min(p.Z for p in pts)
            max_z = max(p.Z for p in pts)
            current_peak = max_z - min_z
            if current_peak < 1e-6:
                self._log("Surface is flat - nothing to scale.")
                return
            scale = target_peak_ft / current_peak
            from Autodesk.Revit.DB import XYZ, Transaction
            new_pts = [XYZ(p.X, p.Y, min_z + (p.Z - min_z) * scale) for p in pts]
            t = Transaction(doc, "Set Peak Elevation")
            t.Start()
            try:
                self._set_points(el, new_pts, pts)
                t.Commit()
                self._log("Peak elevation scaled to {} mm above base.".format(self.TxtPeakTarget.Text.strip()))
            except Exception as ex:
                t.RollBack()
                self._log_error("Set peak failed", ex)
        except Exception as ex:
            self._log_error("Set peak failed", ex)


# Launch UI
MoundEditorWindow().ShowDialog()