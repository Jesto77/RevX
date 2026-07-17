# -*- coding: utf-8 -*-
"""
MatchSlope.py v99

Environment-style mesh tracing — FAST version with spatial hashing +
crease/edge sampling for clean split-line reproduction.

Changes from v98.1:
- Added collect_shared_edges() to find real crease/split lines in the source
  mesh (edges between triangles with different normals) and naked (boundary)
  edges of the mesh.
- Updated collect_mesh_xy() to also densify samples ALONG those edges and
  to insert exact intersections with the target boundary — so split lines
  project cleanly, and edges stay sharp instead of getting smoothed away.
- Zero changes to workflow, transactions, options prompts, or vertex logic.
"""

import sys
import math
import traceback
import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import (
    Floor, RoofBase, Ceiling,
    Transaction, SubTransaction, XYZ,
    Options,
)
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException
from pyrevit import forms, revit, script


doc = revit.doc
uidoc = revit.uidoc

MM_TO_FT  = 1.0 / 304.8
DEDUP_TOL = 0.15      # ~45 mm — much faster, still very dense
GRID_CELL = 2.0       # ft — spatial hash cell size for triangles

# ---------------------------------------------------------------------------
try:
    REVIT_VERSION = int(revit.app.VersionNumber)
except Exception:
    try:
        REVIT_VERSION = int(doc.Application.VersionNumber)
    except Exception:
        REVIT_VERSION = 2025

USE_ADDPOINT_PATH = REVIT_VERSION >= 2026

try:
    from Autodesk.Revit.DB import TopoSolid as _TopoSolidClass
    HAS_TOPOSOLID = True
except ImportError:
    _TopoSolidClass = None
    HAS_TOPOSOLID = False

TOPOSOLID_TYPE_NAMES = {"TopoSolid", "Toposolid", "toposolid"}


# =============================================================================

def get_element_id_value(eid):
    if eid is None:
        return -1
    try:
        return int(eid.Value)
    except AttributeError:
        pass
    try:
        return int(eid.IntegerValue)
    except AttributeError:
        pass
    return -1


def type_name(el):
    try:
        return el.GetType().Name
    except Exception:
        return ""


def is_toposolid(el):
    if type_name(el) in TOPOSOLID_TYPE_NAMES:
        return True
    if HAS_TOPOSOLID and _TopoSolidClass is not None:
        try:
            return isinstance(el, _TopoSolidClass)
        except Exception:
            pass
    return False


def is_floor_like(el):
    if isinstance(el, (Floor, RoofBase, Ceiling)):
        return True
    return is_toposolid(el)


def element_label(el):
    try:
        tn = el.GetType().Name
    except Exception:
        tn = "Element"
    return "{} [id {}]".format(tn, get_element_id_value(el.Id))


class FloorFilter(ISelectionFilter):
    def AllowElement(self, e):
        return is_floor_like(e)

    def AllowReference(self, r, p):
        return False


# =============================================================================

def get_sse(el):
    for getter in (
        lambda: el.SlabShapeEditor,
        lambda: el.GetSlabShapeEditor(),
        lambda: el.GetSlabShapeEditor(doc.ActiveView),
    ):
        try:
            sse = getter()
            if sse is not None:
                return sse
        except Exception:
            pass
    return None


def get_thickness(el):
    if not is_toposolid(el):
        try:
            etype = doc.GetElement(el.GetTypeId())
            cs = etype.GetCompoundStructure()
            if cs is not None:
                w = cs.GetWidth()
                if w > 0:
                    return w
        except Exception:
            pass
    try:
        bb = el.get_BoundingBox(None)
        if bb is not None:
            h = bb.Max.Z - bb.Min.Z
            if h > 0:
                return h
    except Exception:
        pass
    return 0.328


def get_height_offset_param(el):
    bips = [
        DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM,
        DB.BuiltInParameter.ROOF_LEVEL_OFFSET_PARAM,
        DB.BuiltInParameter.CEILING_HEIGHTABOVELEVEL_PARAM,
    ]
    try:
        bips.append(DB.BuiltInParameter.TOPOSOLID_HEIGHTABOVELEVEL_PARAM)
    except AttributeError:
        pass

    for bip in bips:
        try:
            p = el.get_Parameter(bip)
            if p is not None and p.HasValue:
                return p.AsDouble()
        except Exception:
            pass
    return 0.0


def get_level_elevation(el):
    try:
        lvl = doc.GetElement(el.LevelId)
        if lvl is not None:
            return lvl.Elevation
    except Exception:
        pass
    return 0.0


def get_param_datum_z(el):
    return get_level_elevation(el) + get_height_offset_param(el)


# =============================================================================

def get_boundary(element):
    pts = []

    if is_toposolid(element):
        try:
            bids = element.GetBoundaryIds()
            if bids:
                for bid in bids:
                    loop = element.GetBoundary(bid)
                    if loop is not None:
                        for curve in loop:
                            try:
                                ep = curve.GetEndPoint(0)
                                pts.append((ep.X, ep.Y))
                            except Exception:
                                pass
                if pts:
                    return pts
        except Exception:
            pass

    if hasattr(element, "SketchId"):
        try:
            sketch = doc.GetElement(element.SketchId)
            if sketch is not None:
                for ca in sketch.Profile:
                    for curve in ca:
                        try:
                            pts.append((
                                curve.GetEndPoint(0).X,
                                curve.GetEndPoint(0).Y,
                            ))
                        except Exception:
                            pass
                if pts:
                    return pts
        except Exception:
            pass

    try:
        profile = element.GetProfile()
        if profile and len(profile) > 0:
            for loop in profile:
                for curve in loop:
                    try:
                        pts.append((
                            curve.GetEndPoint(0).X,
                            curve.GetEndPoint(0).Y,
                        ))
                    except Exception:
                        pass
            if pts:
                return pts
    except Exception:
        pass

    try:
        bb = element.get_BoundingBox(None)
        if bb is not None:
            pts = [
                (bb.Min.X, bb.Min.Y), (bb.Max.X, bb.Min.Y),
                (bb.Max.X, bb.Max.Y), (bb.Min.X, bb.Max.Y),
            ]
    except Exception:
        pass

    return pts


def point_in_polygon(x, y, poly):
    if len(poly) < 3:
        return False

    inside = False
    n = len(poly)
    j = n - 1

    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]

        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-18) + xi
        ):
            inside = not inside

        j = i

    return inside


def inset_toward_centroid(px, py, poly, dist=0.015):
    if len(poly) < 3:
        return px, py

    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    dx, dy = cx - px, cy - py
    d = math.sqrt(dx * dx + dy * dy)

    if d < 1e-6:
        return px, py

    return px + (dx / d) * dist, py + (dy / d) * dist


# =============================================================================
# FAST DEDUPE — pure grid bucket, no neighborhood search
# =============================================================================

def fast_dedupe(pts, tol=DEDUP_TOL):
    seen = set()
    out  = []
    inv = 1.0 / tol

    for p in pts:
        key = (int(p[0] * inv), int(p[1] * inv))
        if key in seen:
            continue
        seen.add(key)
        out.append(p)

    return out


# =============================================================================

def read_source_sse_full(source):
    pts = []
    sse = get_sse(source)
    if sse is None:
        return pts

    try:
        if not sse.IsEnabled:
            return pts

        for v in sse.SlabShapeVertices:
            p = v.Position
            pts.append((p.X, p.Y, p.Z))
    except Exception:
        pass

    return pts


# =============================================================================

def fit_plane(pts):
    if not pts:
        return {"A": 0.0, "B": 0.0, "C": 0.0, "slope_ratio": 0.0}

    if len(pts) < 3:
        mz = sum(p[2] for p in pts) / len(pts)
        return {"A": 0.0, "B": 0.0, "C": mz, "slope_ratio": 0.0}

    n   = float(len(pts))
    sx  = sum(p[0] for p in pts)
    sy  = sum(p[1] for p in pts)
    sz  = sum(p[2] for p in pts)
    sxx = sum(p[0] * p[0] for p in pts)
    syy = sum(p[1] * p[1] for p in pts)
    sxy = sum(p[0] * p[1] for p in pts)
    sxz = sum(p[0] * p[2] for p in pts)
    syz = sum(p[1] * p[2] for p in pts)

    det = (sxx * (syy * n - sy * sy)
         - sxy * (sxy * n - sy * sx)
         + sx  * (sxy * sy - syy * sx))

    if abs(det) < 1e-12:
        return {"A": 0.0, "B": 0.0, "C": sz / n, "slope_ratio": 0.0}

    A = ((sxz * (syy * n - sy * sy)
        - sxy * (syz * n - sy * sz)
        + sx  * (syz * sy - syy * sz)) / det)

    B = ((sxx * (syz * n - sz * sy)
        - sxz * (sxy * n - sy * sx)
        + sx  * (sxy * sz - syz * sx)) / det)

    C = ((sxx * (syy * sz - sy * syz)
        - sxy * (sxy * sz - sy * sxz)
        + sxz * (sxy * sy - syy * sx)) / det)

    return {"A": A, "B": B, "C": C,
            "slope_ratio": math.sqrt(A * A + B * B)}


def eval_plane(plane, x, y):
    return plane["A"] * x + plane["B"] * y + plane["C"]


def slope_label(plane):
    r = plane["slope_ratio"]

    if r < 1e-5:
        return "FLAT (0%)"

    return "1:{:.1f} | {:.2f}% | {:.2f} deg".format(
        1.0 / r, r * 100.0, math.degrees(math.atan(r)))


# =============================================================================
# EXTRACT SOURCE TOP/BASE TRIANGLES — single pass, no extra loops
# =============================================================================

def extract_source_triangles(source, src_face):
    triangles = []
    want_top = (src_face == "top")

    try:
        opt = Options()
        opt.ComputeReferences = False
        opt.IncludeNonVisibleObjects = False
        opt.DetailLevel = DB.ViewDetailLevel.Medium  # Medium = much faster than Fine

        geom = source.get_Geometry(opt)
        if geom is None:
            return triangles

        for geo_obj in geom:
            solid = None

            try:
                if hasattr(geo_obj, "Faces") and geo_obj.Faces.Size > 0:
                    solid = geo_obj
                elif hasattr(geo_obj, "GetInstanceGeometry"):
                    for ig in geo_obj.GetInstanceGeometry():
                        if hasattr(ig, "Faces") and ig.Faces.Size > 0:
                            solid = ig
                            break
            except Exception:
                pass

            if solid is None:
                continue

            try:
                for face in solid.Faces:
                    try:
                        n = face.ComputeNormal(DB.UV(0.5, 0.5))
                    except Exception:
                        continue

                    if want_top and n.Z < 0.3:
                        continue

                    if not want_top and n.Z > -0.3:
                        continue

                    try:
                        mesh = face.Triangulate()
                    except Exception:
                        continue

                    if mesh is None:
                        continue

                    nt = mesh.NumTriangles

                    for ti in range(nt):
                        tri = mesh.get_Triangle(ti)
                        a = tri.get_Vertex(0)
                        b = tri.get_Vertex(1)
                        c = tri.get_Vertex(2)

                        triangles.append((
                            a.X, a.Y, a.Z,
                            b.X, b.Y, b.Z,
                            c.X, c.Y, c.Z,
                        ))
            except Exception:
                pass
    except Exception:
        pass

    return triangles


# =============================================================================
# BUILD SPATIAL HASH for triangles (O(1) lookup instead of O(T))
# =============================================================================

def build_triangle_grid(triangles, cell=GRID_CELL):
    """
    Map each grid cell to list of triangle indices whose bbox overlaps it.
    """
    grid = {}
    inv = 1.0 / cell

    for idx, t in enumerate(triangles):
        ax, ay = t[0], t[1]
        bx, by = t[3], t[4]
        cx, cy = t[6], t[7]

        min_x = ax if ax < bx else bx
        if cx < min_x:
            min_x = cx

        max_x = ax if ax > bx else bx
        if cx > max_x:
            max_x = cx

        min_y = ay if ay < by else by
        if cy < min_y:
            min_y = cy

        max_y = ay if ay > by else by
        if cy > max_y:
            max_y = cy

        ix0 = int(math.floor(min_x * inv))
        ix1 = int(math.floor(max_x * inv))
        iy0 = int(math.floor(min_y * inv))
        iy1 = int(math.floor(max_y * inv))

        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                k = (ix, iy)
                if k in grid:
                    grid[k].append(idx)
                else:
                    grid[k] = [idx]

    return grid


def z_on_mesh_inside_fast(px, py, triangles, grid, cell=GRID_CELL):
    """
    Same fast triangle lookup as the original z_on_mesh_fast, but returns None
    when the XY point is outside the actual source mesh.

    This is the only behavioral fix: outside source area = no source plane
    extrapolation.
    """
    inv = 1.0 / cell
    key = (int(math.floor(px * inv)), int(math.floor(py * inv)))

    candidates = grid.get(key)

    if candidates:
        for idx in candidates:
            t = triangles[idx]

            x0, y0, z0 = t[0], t[1], t[2]
            x1, y1, z1 = t[3], t[4], t[5]
            x2, y2, z2 = t[6], t[7], t[8]

            denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            if abs(denom) < 1e-14:
                continue

            w0 = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / denom
            if w0 < -1e-4 or w0 > 1.0001:
                continue

            w1 = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / denom
            if w1 < -1e-4 or w1 > 1.0001:
                continue

            w2 = 1.0 - w0 - w1
            if w2 < -1e-4:
                continue

            return w0 * z0 + w1 * z1 + w2 * z2

    return None


def z_on_mesh_fast(px, py, triangles, grid, plane_fallback, cell=GRID_CELL):
    inv = 1.0 / cell
    key = (int(math.floor(px * inv)), int(math.floor(py * inv)))

    candidates = grid.get(key)

    if candidates:
        for idx in candidates:
            t = triangles[idx]

            x0, y0, z0 = t[0], t[1], t[2]
            x1, y1, z1 = t[3], t[4], t[5]
            x2, y2, z2 = t[6], t[7], t[8]

            denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            if abs(denom) < 1e-14:
                continue

            w0 = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / denom
            if w0 < -1e-4 or w0 > 1.0001:
                continue

            w1 = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / denom
            if w1 < -1e-4 or w1 > 1.0001:
                continue

            w2 = 1.0 - w0 - w1
            if w2 < -1e-4:
                continue

            return w0 * z0 + w1 * z1 + w2 * z2

    return eval_plane(plane_fallback, px, py)


# =============================================================================
# COLLECT SHARED MESH EDGES — this is what creates split / crease lines
# =============================================================================

def collect_shared_edges(triangles, tol=1e-4):
    """
    Return two lists:
      crease_edges - edges SHARED between two triangles whose normals differ
                     (real slope-fold lines)
      naked_edges  - edges belonging to only one triangle (outer boundary of
                     the source mesh footprint)
    Each edge is ((x0, y0, z0), (x1, y1, z1)).
    """
    edge_map = {}
    inv = 1.0 / tol

    def key_of(x, y, z):
        return (int(round(x * inv)),
                int(round(y * inv)),
                int(round(z * inv)))

    def add_edge(pa, pb, tri_idx):
        ka = key_of(*pa)
        kb = key_of(*pb)

        if ka == kb:
            return
        if ka < kb:
            ek = (ka, kb)
            pts = (pa, pb)
        else:
            ek = (kb, ka)
            pts = (pb, pa)

        if ek in edge_map:
            edge_map[ek].append((tri_idx, pts))
        else:
            edge_map[ek] = [(tri_idx, pts)]

    for idx, t in enumerate(triangles):
        a = (t[0], t[1], t[2])
        b = (t[3], t[4], t[5])
        c = (t[6], t[7], t[8])
        add_edge(a, b, idx)
        add_edge(b, c, idx)
        add_edge(c, a, idx)

    crease_edges = []
    naked_edges  = []

    def tri_normal(t):
        ax, ay, az = t[0], t[1], t[2]
        bx, by, bz = t[3], t[4], t[5]
        cx, cy, cz = t[6], t[7], t[8]
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        L  = math.sqrt(nx * nx + ny * ny + nz * nz)
        if L < 1e-14:
            return (0.0, 0.0, 1.0)
        return (nx / L, ny / L, nz / L)

    for ek, entries in edge_map.items():
        if len(entries) == 1:
            # naked edge - lives on the mesh boundary
            naked_edges.append(entries[0][1])
        elif len(entries) >= 2:
            n1 = tri_normal(triangles[entries[0][0]])
            n2 = tri_normal(triangles[entries[1][0]])
            dot = n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2]
            # not co-planar -> real crease/fold/split line
            if dot < 0.9995:
                crease_edges.append(entries[0][1])

    return crease_edges, naked_edges


def _seg_intersect(x1, y1, x2, y2, x3, y3, x4, y4):
    """
    Return (x, y) intersection of segment (x1,y1)-(x2,y2) with segment
    (x3,y3)-(x4,y4), or None if they don't cross inside both.
    """
    dx1 = x2 - x1
    dy1 = y2 - y1
    dx2 = x4 - x3
    dy2 = y4 - y3

    denom = dx1 * dy2 - dy1 * dx2
    if abs(denom) < 1e-12:
        return None

    t = ((x3 - x1) * dy2 - (y3 - y1) * dx2) / denom
    u = ((x3 - x1) * dy1 - (y3 - y1) * dx1) / denom

    if t < -1e-6 or t > 1.0 + 1e-6:
        return None
    if u < -1e-6 or u > 1.0 + 1e-6:
        return None

    return (x1 + dx1 * t, y1 + dy1 * t)


# =============================================================================
# COLLECT VERTEX XYs FROM SOURCE MESH — now also samples crease/naked edges
# and inserts exact intersections with the target boundary.
# =============================================================================

def collect_mesh_xy(triangles, tgt_poly, extra_edges=None):
    """
    XY sample points from:
      1. Every triangle vertex.
      2. Dense samples ALONG crease/split edges and naked mesh edges
         (this is what makes split lines project cleanly onto the target).
      3. Intersections of every crease/naked edge with each segment of the
         target boundary (guarantees a vertex exactly where a crease meets
         the target outline).
    All results are deduped and clipped to the target polygon.
    """
    raw = []

    # (1) triangle vertices
    for t in triangles:
        raw.append((t[0], t[1]))
        raw.append((t[3], t[4]))
        raw.append((t[6], t[7]))

    # (2) dense samples along important edges
    if extra_edges:
        # step size: ~ 100 mm along the edge in feet
        step_ft = 100.0 * MM_TO_FT

        for (pa, pb) in extra_edges:
            ax, ay = pa[0], pa[1]
            bx, by = pb[0], pb[1]
            dx, dy = bx - ax, by - ay
            L = math.sqrt(dx * dx + dy * dy)

            if L < 1e-6:
                raw.append((ax, ay))
                continue

            # always include exact endpoints
            raw.append((ax, ay))
            raw.append((bx, by))

            # densify along the edge
            n = int(math.ceil(L / step_ft))
            if n > 1:
                for k in range(1, n):
                    tval = float(k) / float(n)
                    raw.append((ax + dx * tval, ay + dy * tval))

    # (3) intersections between edges and target boundary segments
    if extra_edges and len(tgt_poly) >= 2:
        for (pa, pb) in extra_edges:
            e1x, e1y = pa[0], pa[1]
            e2x, e2y = pb[0], pb[1]

            for i in range(len(tgt_poly)):
                p1 = tgt_poly[i]
                p2 = tgt_poly[(i + 1) % len(tgt_poly)]

                ix = _seg_intersect(e1x, e1y, e2x, e2y,
                                    p1[0], p1[1], p2[0], p2[1])
                if ix is not None:
                    raw.append(ix)

    # Fast dedupe first
    deduped = fast_dedupe(raw, tol=DEDUP_TOL)

    # Filter by target polygon
    if len(tgt_poly) < 3:
        return deduped

    inside = []
    for (x, y) in deduped:
        if point_in_polygon(x, y, tgt_poly):
            inside.append((x, y))

    return inside


# =============================================================================
# DETECT ModifySubElement REFERENCE Z
# =============================================================================

def detect_ref_z(sse, target, vertex):
    ref_z = None

    sub = SubTransaction(doc)
    sub.Start()

    try:
        sse.ModifySubElement(vertex, 0.0)
        doc.Regenerate()

        sse2 = get_sse(target)
        if sse2 is not None:
            verts = list(sse2.SlabShapeVertices)
            if verts:
                ox, oy = vertex.Position.X, vertex.Position.Y
                best_v = verts[0]
                best_d = 1e18

                for vv in verts:
                    d = (vv.Position.X - ox) ** 2 + (vv.Position.Y - oy) ** 2
                    if d < best_d:
                        best_d = d
                        best_v = vv

                ref_z = best_v.Position.Z
    except Exception:
        pass
    finally:
        sub.RollBack()

    return ref_z


# =============================================================================
# PREPARE TARGET SSE
# =============================================================================

def prepare_target(target, mesh_xy):
    sse = get_sse(target)

    if sse is None:
        raise Exception("No SlabShapeEditor on target.")

    if not sse.IsEnabled:
        sse.Enable()

    sse.ResetSlabShape()
    doc.Regenerate()

    sse = get_sse(target)

    if sse is None:
        raise Exception("SSE lost after reset.")

    if not sse.IsEnabled:
        sse.Enable()

    flat_z = get_param_datum_z(target)

    if USE_ADDPOINT_PATH:
        tgt_poly = get_boundary(target)
        add_list = []

        for (cx, cy) in tgt_poly:
            ix, iy = inset_toward_centroid(cx, cy, tgt_poly, 0.015)
            add_list.append((ix, iy))

        add_list.extend(mesh_xy)
        add_list = fast_dedupe(add_list)

        has_ap = hasattr(sse, "AddPoints")
        has_a  = hasattr(sse, "AddPoint")
        has_dp = hasattr(sse, "DrawPoint")

        created = []

        if has_ap and add_list:
            try:
                batch  = [XYZ(px, py, 0.0) for (px, py) in add_list]
                result = sse.AddPoints(batch)
                if result is not None:
                    created.extend(list(result))
            except Exception:
                pass

        if not created and has_a:
            for (px, py) in add_list:
                try:
                    v = sse.AddPoint(XYZ(px, py, 0.0))
                    if v is not None:
                        created.append(v)
                except Exception:
                    pass

        if not created and has_dp:
            for (px, py) in add_list:
                try:
                    sse.DrawPoint(XYZ(px, py, 0.0))
                except Exception:
                    pass
    else:
        for (px, py) in mesh_xy:
            try:
                sse.DrawPoint(XYZ(px, py, flat_z))
            except Exception:
                pass

    doc.Regenerate()

    sse = get_sse(target)

    if sse is None:
        raise Exception("SSE lost after vertex insertion.")

    if not sse.IsEnabled:
        sse.Enable()

    vertices = list(sse.SlabShapeVertices)

    if not vertices:
        raise Exception("No vertices on target after preparation.")

    return sse, vertices


# =============================================================================
# APPLY SLOPE
# =============================================================================

def apply_slope(target, source, src_face, tgt_face, offset_ft):
    src_thick = get_thickness(source)
    tgt_thick = get_thickness(target) if tgt_face == "base" else 0.0

    raw_src = read_source_sse_full(source)

    if not raw_src:
        raise Exception("Source has no SSE vertices.")

    src_face_pts = []

    for (x, y, zt) in raw_src:
        z = zt if src_face == "top" else zt - src_thick
        src_face_pts.append((x, y, z))

    plane = fit_plane(src_face_pts)

    triangles = extract_source_triangles(source, src_face)

    if not triangles:
        raise Exception("Could not extract source surface triangulation.")

    grid = build_triangle_grid(triangles, cell=GRID_CELL)

    tgt_poly = get_boundary(target)

    # NEW: extract the source mesh's crease/split + naked edges and pass them
    # to collect_mesh_xy so those lines project cleanly onto the target.
    crease_edges, naked_edges = collect_shared_edges(triangles)
    important_edges = crease_edges + naked_edges

    mesh_xy = collect_mesh_xy(triangles, tgt_poly,
                              extra_edges=important_edges)

    def src_face_to_tgt_top(src_face_z):
        src_top = src_face_z + src_thick if src_face == "base" else src_face_z

        if tgt_face == "base":
            return src_top + offset_ft + tgt_thick

        return src_top + offset_ft

    sse, vertices = prepare_target(target, mesh_xy)

    ref_z = detect_ref_z(sse, target, vertices[0])

    sse = get_sse(target)

    if sse is None:
        raise Exception("SSE lost after ref Z detection.")

    if not sse.IsEnabled:
        sse.Enable()

    vertices = list(sse.SlabShapeVertices)

    if ref_z is None:
        ref_z = get_param_datum_z(target)

    applied = 0

    for v in vertices:
        vx, vy = v.Position.X, v.Position.Y

        # Only read real source mesh - never extrapolate onto plane outside it.
        z_src_face = z_on_mesh_inside_fast(vx, vy, triangles, grid)

        if z_src_face is None:
            # Outside the source footprint: keep target point at the normal
            # target reference elevation, i.e. same place / no slope applied.
            desired_z = ref_z
        else:
            desired_z = src_face_to_tgt_top(z_src_face)

        value = desired_z - ref_z

        try:
            sse.ModifySubElement(v, value)
            applied += 1
        except Exception:
            pass

    return applied, len(mesh_xy), len(triangles)


# =============================================================================

def nearest_source(target, sources):
    if len(sources) == 1:
        return sources[0]

    try:
        bb = target.get_BoundingBox(None)
        tcx = (bb.Min.X + bb.Max.X) * 0.5
        tcy = (bb.Min.Y + bb.Max.Y) * 0.5
    except Exception:
        return sources[0]

    best, bd = sources[0], 1e18

    for s in sources:
        try:
            sbb = s.get_BoundingBox(None)
            scx = (sbb.Min.X + sbb.Max.X) * 0.5
            scy = (sbb.Min.Y + sbb.Max.Y) * 0.5
            d = (scx - tcx) ** 2 + (scy - tcy) ** 2
            if d < bd:
                bd, best = d, s
        except Exception:
            pass

    return best


# =============================================================================

def main():
    topo_note = "  TopoSolids supported\n" if HAS_TOPOSOLID else ""

    ok = forms.alert(
        "MATCH SLOPE  v99 (Fast Environment-style + Crease Fit)\n"
        "===========================================\n\n"
        "Revit {}\n\n"
        "STEP 1  Select TARGET floor(s)\n"
        "STEP 2  Select SOURCE floor(s)\n"
        "STEP 3  Set options and apply\n\n"
        "{}"
        "Outside source area remains in place\n"
        "Split / crease lines are now sampled densely\n"
        "===========================================".format(
            REVIT_VERSION, topo_note),
        ok=True, cancel=True,
        title="Match Slope"
    )

    if not ok:
        sys.exit()

    try:
        t_refs = uidoc.Selection.PickObjects(
            ObjectType.Element, FloorFilter(),
            "Select TARGET element(s)  [Ctrl=multi | Finish when done]",
        )
    except OperationCanceledException:
        sys.exit()

    if not t_refs:
        forms.alert("No targets selected.", exitscript=True)

    targets = [doc.GetElement(r.ElementId) for r in t_refs]
    targets = [e for e in targets if is_floor_like(e)]

    if not targets:
        forms.alert("No valid target elements.", exitscript=True)

    forms.alert(
        "Targets: {}\n\nNow select SOURCE element(s)".format(len(targets)),
        ok=True, title="Match Slope  —  Step 2",
    )

    try:
        s_refs = uidoc.Selection.PickObjects(
            ObjectType.Element, FloorFilter(),
            "Select SOURCE element(s)  [Ctrl=multi | Finish when done]",
        )
    except OperationCanceledException:
        sys.exit()

    if not s_refs:
        forms.alert("No sources selected.", exitscript=True)

    sources = [doc.GetElement(r.ElementId) for r in s_refs]
    sources = [e for e in sources if is_floor_like(e)]

    if not sources:
        forms.alert("No valid source elements.", exitscript=True)

    src_ids = set(get_element_id_value(s.Id) for s in sources)
    targets = [t for t in targets if get_element_id_value(t.Id) not in src_ids]

    if not targets:
        forms.alert("Targets and sources must be different.", exitscript=True)

    method = forms.CommandSwitchWindow.show(
        ["Top to Top", "Top to Base", "Base to Top", "Base to Base"],
        message="Match Method",
    )

    if not method:
        script.exit()

    src_face, tgt_face = {
        "Top to Top":   ("top",  "top"),
        "Top to Base":  ("base", "top"),
        "Base to Top":  ("top",  "base"),
        "Base to Base": ("base", "base"),
    }[method]

    raw = forms.ask_for_string(
        default="0",
        prompt="Elevation Offset (mm)",
        title="Match Slope  —  Offset",
    )

    if raw is None:
        script.exit()

    try:
        offset_mm = float(raw)
    except Exception:
        offset_mm = 0.0

    offset_ft = offset_mm * MM_TO_FT

    valid_sources = []

    for src in sources:
        if read_source_sse_full(src):
            valid_sources.append(src)

    if not valid_sources:
        forms.alert("No source has SSE vertices.", exitscript=True)

    results = []
    skipped = []

    with Transaction(doc, "Match Slope") as t:
        t.Start()

        for tgt in targets:
            lbl = element_label(tgt)

            try:
                src = nearest_source(tgt, valid_sources)

                applied, nxy, ntri = apply_slope(
                    tgt, src, src_face, tgt_face, offset_ft
                )

                results.append((lbl, applied, nxy, ntri))
            except Exception as ex:
                skipped.append("{}: {}".format(lbl, str(ex)[:140]))

        t.Commit()

    msg = "Match Slope Complete!\n"
    msg += "=====================================\n"
    msg += "Targets processed: {}\n".format(len(results))
    msg += "Method  :  {}\n".format(method)
    msg += "Offset  :  {} mm\n".format(int(offset_mm))
    msg += "Outside source area: kept in place\n\n"

    for (lbl, applied, nxy, ntri) in results:
        msg += "  • {}\n".format(lbl)
        msg += "      {} vertices | {} mesh pts | {} src triangles\n".format(
            applied, nxy, ntri)

    if skipped:
        msg += "\nFailed ({}):\n".format(len(skipped))
        msg += "\n".join("  • " + s for s in skipped)

    forms.alert(msg, title="Match Slope  —  Done")


# =============================================================================

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        forms.alert(
            "Error:\n{}\n\n{}".format(str(e), traceback.format_exc()),
            title="Match Slope Error",
        )