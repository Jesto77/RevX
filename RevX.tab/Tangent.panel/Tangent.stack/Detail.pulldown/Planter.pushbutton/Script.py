# -*- coding: utf-8 -*-
"""
Fill Planter Cavity (026)

Click inside the soil area of a planter section/detail. The script finds the closed area
around that point from ALL detail components / detail lines in the view (openings such as the
open top of the planter walls or the gap at the drain are closed automatically) and creates a
Filled Region of the type whose name contains "026" exactly there.

It also places the line-based detail item whose family / type name contains "005"
95 mm below the bottom-most horizontal line of the created 026 filled region,
from the left to the right outer wall.

It then places the line-based detail item whose family / type name contains "006":
bottom 10 mm above the 005 line (inset 10 mm from each end of 005), and two sides that are
single STRAIGHT vertical lines from the top of 026 down to the 006 bottom line, 7.5 mm
inside the outermost left / right edge of 026 (they do not follow the L copings or the
sloping floor). The sides start exactly at the top (no gap).

It also places the line-based detail item whose family / type name contains "008"
15 mm inward from every 026 boundary except the open top (starts exactly at the top).

Works in Revit 2022 - 2026.
"""
import math
import clr

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System')

from System.Collections.Generic import List
from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, FilledRegion, FilledRegionType,
    Transaction, Line, Curve, CurveLoop, GeometryInstance, GeometryElement, Options,
    XYZ, BuiltInParameter, FamilySymbol, FamilyPlacementType
)
from pyrevit import forms, script

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView

v_origin = view.Origin
v_x = view.RightDirection
v_y = view.UpDirection

FT2_TO_M2 = 0.09290304
MM = 1.0 / 304.8
BOTTOM_ITEM_KEY = "005"          # detail item family / type name contains this
BOTTOM_ITEM_OFFSET = 95.0 * MM   # 95 mm below 026 soil bottom line
LAYER_006_KEY = "006"
LAYER_006_ABOVE_005 = 10.0 * MM  # 006 sits 10 mm above 005
LAYER_006_BOTTOM_INSET = 10.0 * MM  # 006 bottom inset from each end of 005
LAYER_006_SIDE_OFFSET = 7.5 * MM    # 006 sides inset from 026 side boundaries
LAYER_008_KEY = "008"
LAYER_008_OFFSET = 15.0 * MM        # 008 inset from 026 boundary (except top)
HORIZ_TOL = 0.008                # ~2.4 mm slope tolerance for horizontal lines
SHOW_RESULT = False              # True = show the output window + result message again


def log(msg):
    """Prints to the pyRevit output window only when SHOW_RESULT is on."""
    if SHOW_RESULT:
        print(msg)


def to_2d(pt):
    vec = pt - v_origin
    return XYZ(vec.DotProduct(v_x), vec.DotProduct(v_y), 0)


def to_3d(x, y):
    return v_origin + (x * v_x) + (y * v_y)


# =============================================================================
# 2D GEOMETRY (pure python)
# =============================================================================

# ---------------------------------------------------------------- pure 2D geometry
def _t_on(p, a, b, tol):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 <= 0.0:
        return None
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2
    if t < 0.0 or t > 1.0:
        return None
    px, py = a[0] + t * dx, a[1] + t * dy
    if math.hypot(p[0] - px, p[1] - py) <= tol:
        return t
    return None


def _cuts(a, b, c, d, tol):
    ts_ab, ts_cd = [], []
    for p in (c, d):
        t = _t_on(p, a, b, tol)
        if t is not None:
            ts_ab.append(t)
    for p in (a, b):
        t = _t_on(p, c, d, tol)
        if t is not None:
            ts_cd.append(t)
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) > 1e-12:
        qp = (c[0] - a[0], c[1] - a[1])
        t = (qp[0] * s[1] - qp[1] * s[0]) / den
        u = (qp[0] * r[1] - qp[1] * r[0]) / den
        if 0.0 < t < 1.0 and 0.0 < u < 1.0:
            ts_ab.append(t)
            ts_cd.append(u)
    return ts_ab, ts_cd


def planarize(segs, tol):
    """Splits segments at every crossing / T-junction / touching point."""
    n = len(segs)
    boxes = []
    for a, b in segs:
        boxes.append((min(a[0], b[0]) - tol, max(a[0], b[0]) + tol,
                      min(a[1], b[1]) - tol, max(a[1], b[1]) + tol))
    cuts = [[] for _ in range(n)]
    for i in range(n):
        bi = boxes[i]
        a, b = segs[i]
        for j in range(i + 1, n):
            bj = boxes[j]
            if bi[1] < bj[0] or bj[1] < bi[0] or bi[3] < bj[2] or bj[3] < bi[2]:
                continue
            c, d = segs[j]
            ti, tj = _cuts(a, b, c, d, tol)
            if ti:
                cuts[i].extend(ti)
            if tj:
                cuts[j].extend(tj)
    out = []
    for i in range(n):
        a, b = segs[i]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length <= 0.0:
            continue
        eps = tol / length
        ts = sorted(t for t in cuts[i] if eps < t < 1.0 - eps)
        pts = [a]
        last = 0.0
        for t in ts:
            if t - last > eps:
                pts.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
                last = t
        pts.append(b)
        for k in range(len(pts) - 1):
            out.append((pts[k], pts[k + 1]))
    return out


class Graph(object):
    def __init__(self, tol):
        self.tol = tol
        self.cells = {}
        self.pts = []
        self.adj = {}

    def node(self, p):
        tol = self.tol
        cx, cy = int(math.floor(p[0] / tol)), int(math.floor(p[1] / tol))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for idx in self.cells.get((cx + dx, cy + dy), ()):
                    q = self.pts[idx]
                    if math.hypot(q[0] - p[0], q[1] - p[1]) < tol:
                        return idx
        self.pts.append(p)
        idx = len(self.pts) - 1
        self.cells.setdefault((cx, cy), []).append(idx)
        self.adj[idx] = set()
        return idx

    def add_edge(self, i, j):
        if i != j:
            self.adj[i].add(j)
            self.adj[j].add(i)


def build_graph(segs, tol):
    g = Graph(tol)
    for a, b in segs:
        g.add_edge(g.node(a), g.node(b))
    return g


def close_gaps(g, gap_tol):
    """Joins pairs of dead-end nodes that lie closer than gap_tol (e.g. drain opening)."""
    dead = [i for i in g.adj if len(g.adj[i]) == 1]
    pairs = []
    for x in range(len(dead)):
        for y in range(x + 1, len(dead)):
            a, b = dead[x], dead[y]
            if b in g.adj[a]:
                continue
            d = math.hypot(g.pts[a][0] - g.pts[b][0], g.pts[a][1] - g.pts[b][1])
            if d <= gap_tol:
                pairs.append((d, a, b))
    pairs.sort()
    used, added = set(), 0
    for d, a, b in pairs:
        if a in used or b in used:
            continue
        g.add_edge(a, b)
        used.add(a)
        used.add(b)
        added += 1
    return added


def close_top(g, seed, width):
    """Closes the open top: joins the highest node left of the seed and the highest right of it
    (closest to the seed in X) - the planter walls end without a top line."""
    ymax = max(p[1] for p in g.pts)
    band = 0.03 * width
    left = [i for i, p in enumerate(g.pts) if p[1] >= ymax - band and p[0] < seed[0]]
    right = [i for i, p in enumerate(g.pts) if p[1] >= ymax - band and p[0] > seed[0]]
    if not left or not right:
        return False
    L = max(left, key=lambda i: g.pts[i][0])
    R = min(right, key=lambda i: g.pts[i][0])
    g.add_edge(L, R)
    return True


def _ang(p, q):
    return math.atan2(q[1] - p[1], q[0] - p[0])


def ray_edge(g, seed, d):
    """Nearest edge hit by a ray from the seed in direction d (unit vector), directed so the
    seed lies on its left. A tiny sideways offset keeps the ray away from vertices."""
    off = 1.2345e-4
    s = (seed[0] - d[1] * off, seed[1] + d[0] * off)
    best, best_t = None, None
    for i, nbrs in g.adj.items():
        a = g.pts[i]
        for j in nbrs:
            if j < i:
                continue
            b = g.pts[j]
            ex, ey = b[0] - a[0], b[1] - a[1]
            den = d[0] * ey - d[1] * ex
            if abs(den) < 1e-12:
                continue
            qx, qy = a[0] - s[0], a[1] - s[1]
            t = (qx * ey - qy * ex) / den
            u = (qx * d[1] - qy * d[0]) / den
            if t > 1e-9 and 0.0 < u < 1.0 and (best_t is None or t < best_t):
                best, best_t = (i, j), t
    if best is None:
        return None
    i, j = best
    a, b = g.pts[i], g.pts[j]
    cross = (b[0] - a[0]) * (seed[1] - a[1]) - (b[1] - a[1]) * (seed[0] - a[0])
    return (i, j) if cross > 0 else (j, i)


RAYS = ((0.0, -1.0), (0.0, 1.0), (-1.0, 0.0), (1.0, 0.0))


def trace_face(g, u, v, max_steps=20000):
    first = (u, v)
    path = [u]
    for _ in range(max_steps):
        path.append(v)
        pv = g.pts[v]
        back = _ang(pv, g.pts[u])
        best, best_d = None, None
        for w in g.adj[v]:
            d = (back - _ang(pv, g.pts[w])) % (2.0 * math.pi)
            if w == u:
                d = 2.0 * math.pi
            if best_d is None or d < best_d:
                best, best_d = w, d
        u, v = v, best
        if (u, v) == first:
            break
    else:
        return None
    path.pop()
    return path


def remove_spikes(path):
    pts = list(path)
    changed = True
    while changed and len(pts) > 3:
        changed = False
        n = len(pts)
        for i in range(n):
            if pts[(i - 1) % n] == pts[(i + 1) % n]:
                # remove pts[i] and one of its duplicates
                keep = [pts[k] for k in range(n) if k != i and k != (i + 1) % n]
                pts = keep
                changed = True
                break
    return pts


def area(poly):
    s = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return s / 2.0


def inside(poly, p):
    c = False
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > p[1]) != (y1 > p[1]):
            if p[0] < (x1 - x0) * (p[1] - y0) / (y1 - y0) + x0:
                c = not c
    return c


def simplify(poly, min_len=0.004, cross_tol=0.008):
    pts = [poly[0]]
    for p in poly[1:]:
        if math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) >= min_len:
            pts.append(p)
    if len(pts) > 1 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < min_len:
        pts.pop()
    out = []
    n = len(pts)
    for i in range(n):
        a, b, c = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
        v1 = (b[0] - a[0], b[1] - a[1])
        v2 = (c[0] - b[0], c[1] - b[1])
        l1, l2 = math.hypot(*v1), math.hypot(*v2)
        if l1 < 1e-9 or l2 < 1e-9:
            continue
        if abs(v1[0] * v2[1] - v1[1] * v2[0]) / (l1 * l2) < cross_tol:
            continue
        out.append(b)
    return out


def remove_edge(g, i, j):
    g.adj[i].discard(j)
    g.adj[j].discard(i)


def evaluate(g, seed):
    """Traces the face boundary hit by the downward ray (scoring) and, for finding openings,
    also the boundaries hit by the up / left / right rays. Returns dict or None."""
    main = None
    tips = set()
    for k, d in enumerate(RAYS):
        se = ray_edge(g, seed, d)
        if se is None:
            continue
        path = trace_face(g, se[0], se[1])
        if path is None:
            continue
        tips.update(i for i in path if len(g.adj[i]) == 1)
        if main is None and k == 0:
            main = path
    if main is None:
        return None
    poly = remove_spikes([g.pts[i] for i in main])
    a = area(poly)
    valid = a > 0.0 and inside(poly, seed)
    main_tips = set(i for i in main if len(g.adj[i]) == 1)
    return {"valid": valid, "area": abs(a), "poly": poly, "tips": sorted(tips),
            "main_tips": len(main_tips),
            "score": (0, abs(a)) if valid else (1, len(tips))}


def find_region(segs, seed, snap_tol=0.01):
    """
    Closed face around the seed point.  Openings in the drawing (open top of the planter,
    gap at the drain ...) are closed by joining dead-end nodes - the closure that gives the
    smallest valid face wins.
    """
    info = {}
    xs = [p[0] for s in segs for p in s]
    width = max(xs) - min(xs)
    g = build_graph(planarize(segs, snap_tol), snap_tol)
    info["segments"] = len(segs)
    info["edges"] = sum(len(v) for v in g.adj.values()) // 2
    info["top_closed"] = close_top(g, seed, width)
    best = evaluate(g, seed)
    info["closures"] = 0
    if best is None:
        return None, info
    for _ in range(8):
        if best["valid"] and best["main_tips"] == 0:
            break
        tips = best["tips"]
        cand = []
        for x in range(len(tips)):
            for y in range(x + 1, len(tips)):
                a, b = tips[x], tips[y]
                if b in g.adj[a]:
                    continue
                d = math.hypot(g.pts[a][0] - g.pts[b][0], g.pts[a][1] - g.pts[b][1])
                if d <= 0.6 * width:
                    cand.append((d, a, b))
        cand.sort()
        pick = None
        for d, a, b in cand[:60]:
            g.add_edge(a, b)
            r = evaluate(g, seed)
            remove_edge(g, a, b)
            if r is not None and r["score"] < best["score"]:
                if pick is None or r["score"] < pick[2]["score"]:
                    pick = (a, b, r)
        if pick is None:
            break
        g.add_edge(pick[0], pick[1])
        best = pick[2]
        info["closures"] += 1
    info["area"] = best["area"]
    info["valid"] = best["valid"]
    info["tips_left"] = best["main_tips"]
    if not best["valid"]:
        return None, info
    return simplify(best["poly"]), info


# =============================================================================
# REVIT SIDE
# =============================================================================
def curve_to_segments(c):
    segs = []
    try:
        if isinstance(c, Line):
            a, b = to_2d(c.GetEndPoint(0)), to_2d(c.GetEndPoint(1))
            pts = [(a.X, a.Y), (b.X, b.Y)]
        else:
            pts = []
            for p in c.Tessellate():
                q = to_2d(p)
                pts.append((q.X, q.Y))
        for k in range(len(pts) - 1):
            if math.hypot(pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1]) > 0.002:
                segs.append((pts[k], pts[k + 1]))
    except Exception:
        pass
    return segs


def element_segments(el):
    opt = Options()
    opt.ComputeReferences = False
    opt.View = view
    segs = []

    def walk(g):
        if isinstance(g, Curve):
            segs.extend(curve_to_segments(g))
        elif isinstance(g, GeometryInstance):
            for child in g.GetInstanceGeometry():
                walk(child)
        elif isinstance(g, GeometryElement):
            for child in g:
                walk(child)

    try:
        geom = el.get_Geometry(opt)
        if geom:
            for g in geom:
                walk(g)
    except Exception:
        pass
    return segs


def element_box(el):
    try:
        bb = el.get_BoundingBox(view)
        if bb is None:
            return None
        xs, ys = [], []
        for x in (bb.Min.X, bb.Max.X):
            for y in (bb.Min.Y, bb.Max.Y):
                for z in (bb.Min.Z, bb.Max.Z):
                    q = to_2d(XYZ(x, y, z))
                    xs.append(q.X)
                    ys.append(q.Y)
        return (min(xs), min(ys), max(xs), max(ys))
    except Exception:
        return None


def boxes_touch(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def gather_segments(seed):
    elems = []
    categories = (
        BuiltInCategory.OST_DetailComponents,
        BuiltInCategory.OST_Lines,
        BuiltInCategory.OST_Walls,
        BuiltInCategory.OST_Floors,
        BuiltInCategory.OST_GenericModel
    )
    for bic in categories:
        try:
            elems.extend(FilteredElementCollector(doc, view.Id).OfCategory(bic)
                         .WhereElementIsNotElementType().ToElements())
        except Exception:
            pass
    boxed = [(e, element_box(e)) for e in elems]
    boxed = [(e, b) for e, b in boxed if b is not None]
    sx, sy = seed
    seed_box = (sx - 0.01, sy - 0.01, sx + 0.01, sy + 0.01)
    chosen = [(e, b) for e, b in boxed if boxes_touch(b, seed_box)]
    if chosen:
        ub = (min(b[0] for _, b in chosen), min(b[1] for _, b in chosen),
              max(b[2] for _, b in chosen), max(b[3] for _, b in chosen))
        for _ in range(2):          # also take neighbours overlapping those (drain, pipe ...)
            chosen = [(e, b) for e, b in boxed if boxes_touch(b, ub)]
            ub = (min(b[0] for _, b in chosen), min(b[1] for _, b in chosen),
                  max(b[2] for _, b in chosen), max(b[3] for _, b in chosen))
    else:
        chosen = boxed
    segs = []
    for e, _ in chosen:
        segs.extend(element_segments(e))
    return segs, len(chosen), len(elems)


def pick_region_type():
    types = FilteredElementCollector(doc).OfClass(FilledRegionType).ToElements()
    if not types:
        forms.alert("No Filled Region Types exist in this document.", exitscript=True)

    def type_name(t):
        try:
            p = t.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
            if p is not None and p.AsString():
                return p.AsString()
        except Exception:
            pass
        try:
            return t.Name
        except Exception:
            return ""

    for t in types:
        if "026" in type_name(t):
            return t
    forms.alert("Could not find a Filled Region Type with '026' in its name.\n"
                "Using: '{}'".format(type_name(types[0])), title="Region Type Warning",
                warn_icon=True)
    return types[0]


def find_bottom_line(segs, poly):
    """
    (x0, x1, y) of the planter's outer bottom horizontal face (the base slab bottom).
    x0, x1 span from the left outer wall to the right outer wall.
    y is the lowest wide horizontal line of the planter box structure.
    """
    if not segs or not poly:
        return None

    poly_xs = [p[0] for p in poly]
    poly_ys = [p[1] for p in poly]
    soil_x0, soil_x1 = min(poly_xs), max(poly_xs)
    soil_y0, soil_y1 = min(poly_ys), max(poly_ys)
    soil_w = soil_x1 - soil_x0
    x_center = (soil_x0 + soil_x1) / 2.0

    # Get outer X span of the planter walls
    planter_pts = [p for s in segs for p in s if (soil_y0 - 2.0 <= p[1] <= soil_y1 + 0.5)]
    if planter_pts:
        outer_x0 = min(p[0] for p in planter_pts)
        outer_x1 = max(p[0] for p in planter_pts)
    else:
        outer_x0 = soil_x0
        outer_x1 = soil_x1

    outer_w = outer_x1 - outer_x0

    # Extract all near-horizontal segments
    horiz_segs = []
    for a, b in segs:
        if abs(a[1] - b[1]) <= 0.008:  # dy <= 8mm tolerance
            y_val = (a[1] + b[1]) / 2.0
            x_min = min(a[0], b[0])
            x_max = max(a[0], b[0])
            length = x_max - x_min
            horiz_segs.append({
                'y': y_val,
                'len': length,
                'x_min': x_min,
                'x_max': x_max
            })

    if not horiz_segs:
        return (outer_x0, outer_x1, soil_y0)

    # Group horizontal segments into Y bins (~10mm height bins)
    step = 10.0 * MM
    groups = {}
    for s in horiz_segs:
        key = int(round(s['y'] / step))
        groups.setdefault(key, []).append(s)

    # Evaluate bins to find wide structural lines of the planter box
    valid_y_levels = []
    for key, items in groups.items():
        tot_len = sum(s['len'] for s in items)
        min_x = min(s['x_min'] for s in items)
        max_x = max(s['x_max'] for s in items)
        span = max_x - min_x
        avg_y = sum(s['y'] * s['len'] for s in items) / float(tot_len)

        # Require that the line is wide enough to belong to the main planter structure
        # (filters out narrow drain cap inside cavity and pipe fittings below base)
        is_wide = (tot_len >= 0.25 * outer_w) or (span >= 0.50 * outer_w)
        reaches_outer = (min_x < x_center - 0.15 * soil_w) or (max_x > x_center + 0.15 * soil_w)

        if is_wide and reaches_outer:
            valid_y_levels.append(avg_y)

    if valid_y_levels:
        # LOWEST wide structural line is the bottom face of the base slab
        best_y = min(valid_y_levels)
    else:
        best_y = soil_y0

    return (outer_x0, outer_x1, best_y)


def filled_region_bottom_y(fr):
    """Bottom-most near-horizontal edge of the created 026 filled region, in view Y."""
    try:
        loops = fr.GetBoundaries()
    except Exception:
        return None

    horiz_ys = []
    all_ys = []
    for loop in loops:
        for curve in loop:
            try:
                a = to_2d(curve.GetEndPoint(0))
                b = to_2d(curve.GetEndPoint(1))
            except Exception:
                continue
            all_ys.append(a.Y)
            all_ys.append(b.Y)
            if abs(a.Y - b.Y) <= HORIZ_TOL and abs(a.X - b.X) > abs(a.Y - b.Y):
                horiz_ys.append(0.5 * (a.Y + b.Y))

    if horiz_ys:
        return min(horiz_ys)
    if all_ys:
        return min(all_ys)
    return None


def poly_bottom_y(poly):
    """Fallback: bottom-most near-horizontal edge of the 026 boundary polygon."""
    if not poly:
        return None
    n = len(poly)
    horiz_ys = []
    all_ys = [p[1] for p in poly]
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if abs(a[1] - b[1]) <= HORIZ_TOL and abs(a[0] - b[0]) > abs(a[1] - b[1]):
            horiz_ys.append(0.5 * (a[1] + b[1]))
    if horiz_ys:
        return min(horiz_ys)
    if all_ys:
        return min(all_ys)
    return None


def find_top_edge_index(poly):
    """Open-top closing edge of 026: the highest, widest near-horizontal edge."""
    n = len(poly)
    best_i, best = None, None
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        dx = abs(b[0] - a[0])
        dy = abs(b[1] - a[1])
        if dx < dy:
            continue
        key = (0.5 * (a[1] + b[1]), dx)
        if best is None or key > best:
            best, best_i = key, i
    return best_i


def classify_poly_edges(poly):
    """Tag each 026 edge as 'top', 'bottom', or 'side'."""
    n = len(poly)
    top_i = find_top_edge_index(poly)
    ymin = min(p[1] for p in poly)
    ymax = max(p[1] for p in poly)
    h = max(ymax - ymin, MM)
    kinds = []
    for i in range(n):
        if i == top_i:
            kinds.append("top")
            continue
        a, b = poly[i], poly[(i + 1) % n]
        avg_y = 0.5 * (a[1] + b[1])
        if avg_y <= ymin + 0.22 * h:
            kinds.append("bottom")
        else:
            kinds.append("side")
    return kinds, top_i


def _inward_normal(a, b, sign):
    ex, ey = b[0] - a[0], b[1] - a[1]
    length = math.hypot(ex, ey)
    if length < 1e-12:
        return None
    # Left normal. sign +1 when polygon is CCW so left is inward.
    return (sign * (-ey / length), sign * (ex / length))


def _line_intersect(p, r, q, s):
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-12:
        return None
    t = ((q[0] - p[0]) * s[1] - (q[1] - p[1]) * s[0]) / den
    return (p[0] + t * r[0], p[1] + t * r[1])


def offset_poly_edges(poly, dist, keep_kinds):
    """
    Inward offset of selected 026 edges. Adjacent kept edges are trimmed to their
    intersection so corners (including the top L copings) stay closed.
    The open top is NOT offset: lines that meet the top start exactly at the top line
    (no gap).
    """
    n = len(poly)
    if n < 3 or dist <= 0:
        return []
    sign = 1.0 if area(poly) > 0 else -1.0
    kinds, _top_i = classify_poly_edges(poly)

    raw = []
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        nrm = _inward_normal(a, b, sign)
        if nrm is None:
            raw.append(None)
            continue
        d = 0.0 if kinds[i] == "top" else dist      # top line stays where it is
        o0 = (a[0] + d * nrm[0], a[1] + d * nrm[1])
        o1 = (b[0] + d * nrm[0], b[1] + d * nrm[1])
        raw.append((o0, o1))

    corners = [None] * n
    for i in range(n):
        prev = (i - 1) % n
        A, B = raw[prev], raw[i]
        if A is None or B is None:
            continue
        r = (A[1][0] - A[0][0], A[1][1] - A[0][1])
        svec = (B[1][0] - B[0][0], B[1][1] - B[0][1])
        ip = _line_intersect(A[0], r, B[0], svec)
        if ip is None:
            ip = B[0]
        else:
            # Reject runaway intersections on near-parallel edges
            if math.hypot(ip[0] - B[0][0], ip[1] - B[0][1]) > 8.0 * dist:
                ip = B[0]
        corners[i] = ip

    segs = []
    for i in range(n):
        if kinds[i] not in keep_kinds:
            continue
        if raw[i] is None:
            continue
        c0 = corners[i]
        c1 = corners[(i + 1) % n]
        if c0 is None or c1 is None:
            c0, c1 = raw[i][0], raw[i][1]
        if math.hypot(c1[0] - c0[0], c1[1] - c0[1]) >= MM:
            segs.append((c0, c1))
    return segs


def straight_sides_006(poly, y_bottom, offset):
    """
    Two straight vertical lines: from the top of 026 down to y_bottom (the 006 bottom line),
    `offset` inside the outermost left / right edge of 026.
    """
    xs = [p[0] for p in poly]
    y_top = max(p[1] for p in poly)
    x_left = min(xs) + offset
    x_right = max(xs) - offset
    segs = []
    for x in (x_left, x_right):
        if y_top - y_bottom >= MM:
            segs.append(((x, y_top), (x, y_bottom)))
    return segs


def extend_sides_straight_down(side_segs, y_target, x_center):
    """
    The lowest near-vertical segment on the left and on the right is continued STRAIGHT
    DOWN (same x) to y_target, so the sides meet the bottom line directly instead of
    following the sloping floor of the soil.
    """
    out = list(side_segs)
    for want_left in (True, False):
        cand = []
        for idx, (a, b) in enumerate(out):
            dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
            if dy < MM or dy < 3.0 * dx:
                continue                      # not vertical
            xm = 0.5 * (a[0] + b[0])
            if (xm < x_center) == want_left:
                cand.append((min(a[1], b[1]), idx))
        if not cand:
            continue
        _lowest, idx = min(cand)
        a, b = out[idx]
        lower, upper = (a, b) if a[1] < b[1] else (b, a)
        if y_target < lower[1]:
            out[idx] = (upper, (lower[0], y_target))
    return out


def place_detail_on_segs(sym, segs):
    n_ok = 0
    for a, b in segs:
        if place_line_detail(sym, a[0], a[1], b[0], b[1]) is not None:
            n_ok += 1
    return n_ok


def place_line_detail(sym, x0, y0, x1, y1):
    """Place a curve-based detail item on a 2D view line. Returns the instance or None."""
    if math.hypot(x1 - x0, y1 - y0) < MM:
        return None
    p0 = to_3d(x0, y0)
    p1 = to_3d(x1, y1)
    return doc.Create.NewFamilyInstance(Line.CreateBound(p0, p1), sym, view)


def find_line_based_detail(key):
    """Line-based detail item family symbol whose family / type name contains key."""
    found = []
    try:
        syms = FilteredElementCollector(doc).OfClass(FamilySymbol)\
            .OfCategory(BuiltInCategory.OST_DetailComponents).ToElements()
    except Exception:
        syms = []
    for sym in syms:
        try:
            fam_name = sym.Family.Name
            p = sym.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
            type_name = p.AsString() if p is not None else sym.Name
            if key in fam_name or key in (type_name or ""):
                found.append((sym, fam_name, type_name))
        except Exception:
            pass
    line_based = [f for f in found
                  if f[0].Family.FamilyPlacementType == FamilyPlacementType.CurveBasedDetail]
    return line_based, found


def main():
    target_type = pick_region_type()

    try:
        pt = uidoc.Selection.PickPoint("Click INSIDE the planter soil area (the cavity)")
    except Exception:
        script.exit()
    q = to_2d(pt)
    seed = (q.X, q.Y)

    segs, used, total = gather_segments(seed)
    if len(segs) < 4:
        forms.alert("No detail geometry found around the clicked point.", exitscript=True)

    poly, info = find_region(segs, seed)

    log("Elements used: {} of {} | segments: {} | edges after splitting: {}".format(
        used, total, info.get("segments"), info.get("edges")))
    log("Top closed: {} | extra closures: {} | open ends left: {}".format(
        info.get("top_closed"), info.get("closures"), info.get("tips_left")))

    if poly is None or len(poly) < 3:
        forms.alert(
            "Could not find a closed area around the clicked point.\n\n"
            "Click inside the soil area (not on a line). Details are in the output window.",
            exitscript=True)

    log("Region: {} corners, area {:.4f} m2".format(
        len(poly), abs(area(poly)) * FT2_TO_M2))

    c_loop = CurveLoop()
    n = len(poly)
    try:
        for i in range(n):
            a = to_3d(poly[i][0], poly[i][1])
            b = to_3d(poly[(i + 1) % n][0], poly[(i + 1) % n][1])
            if a.DistanceTo(b) >= 0.003:
                c_loop.Append(Line.CreateBound(a, b))
    except Exception as ex:
        forms.alert("Could not build the boundary:\n{}".format(ex), exitscript=True)

    # ---- detail items (005 / 006 / 008): looked up before the transaction
    bottom = find_bottom_line(segs, poly)
    item_syms, any_syms = find_line_based_detail(BOTTOM_ITEM_KEY)
    item_006_syms, any_006 = find_line_based_detail(LAYER_006_KEY)
    item_008_syms, any_008 = find_line_based_detail(LAYER_008_KEY)
    item_msg = None
    item_006_msg = None
    item_008_msg = None
    if bottom is None:
        item_msg = "bottom line of the planter not found"
        item_006_msg = item_msg
    elif not any_syms:
        item_msg = "no detail item with '{}' in its family / type name".format(BOTTOM_ITEM_KEY)
        item_006_msg = "005 was not placed, so 006 was skipped"
    elif not item_syms:
        item_msg = "'{}' detail item is not a LINE-based family".format(any_syms[0][1])
        item_006_msg = "005 was not placed, so 006 was skipped"
    else:
        log("005 span: x {:.3f}..{:.3f}; detail item: {} : {}".format(
            bottom[0], bottom[1], item_syms[0][1], item_syms[0][2]))
        if len(item_syms) > 1:
            log("({} matches for '{}', using the first)".format(len(item_syms), BOTTOM_ITEM_KEY))

    if item_006_msg is None:
        if not any_006:
            item_006_msg = "no detail item with '{}' in its family / type name".format(LAYER_006_KEY)
        elif not item_006_syms:
            item_006_msg = "'{}' detail item is not a LINE-based family".format(any_006[0][1])
        else:
            log("006 detail item: {} : {}".format(item_006_syms[0][1], item_006_syms[0][2]))
            if len(item_006_syms) > 1:
                log("({} matches for '{}', using the first)".format(len(item_006_syms), LAYER_006_KEY))

    if not any_008:
        item_008_msg = "no detail item with '{}' in its family / type name".format(LAYER_008_KEY)
    elif not item_008_syms:
        item_008_msg = "'{}' detail item is not a LINE-based family".format(any_008[0][1])
    else:
        log("008 detail item: {} : {}".format(item_008_syms[0][1], item_008_syms[0][2]))
        if len(item_008_syms) > 1:
            log("({} matches for '{}', using the first)".format(len(item_008_syms), LAYER_008_KEY))

    t = Transaction(doc, "Fill Planter Cavity (026) + 005 + 006 + 008")
    t.Start()
    try:
        loops = List[CurveLoop]()
        loops.Add(c_loop)
        fr = FilledRegion.Create(doc, target_type.Id, view.Id, loops)
        doc.Regenerate()

        if item_msg is None:
            try:
                soil_y = filled_region_bottom_y(fr)
                if soil_y is None:
                    soil_y = poly_bottom_y(poly)
                if soil_y is None:
                    raise Exception("could not read bottom line of 026 filled region")
                log("026 bottom horizontal line y {:.3f}; 005 at y {:.3f} (95 mm down)".format(
                    soil_y, soil_y - BOTTOM_ITEM_OFFSET))
                sym = item_syms[0][0]
                if not sym.IsActive:
                    sym.Activate()
                    doc.Regenerate()
                y = soil_y - BOTTOM_ITEM_OFFSET
                p0 = to_3d(bottom[0], y)
                p1 = to_3d(bottom[1], y)
                doc.Create.NewFamilyInstance(Line.CreateBound(p0, p1), sym, view)

                if item_006_msg is None:
                    try:
                        x0_006 = bottom[0] + LAYER_006_BOTTOM_INSET
                        x1_006 = bottom[1] - LAYER_006_BOTTOM_INSET
                        if (x1_006 - x0_006) < MM:
                            raise Exception("006 span is too short after 10 mm side offsets")
                        y_006 = y + LAYER_006_ABOVE_005
                        # sides: straight from the top of 026 to the 006 bottom line
                        side_segs = straight_sides_006(poly, y_006, LAYER_006_SIDE_OFFSET)
                        log("006 bottom y {:.3f} (10 mm above 005), x {:.3f}..{:.3f}".format(
                            y_006, x0_006, x1_006))
                        log("006 side segments (straight, 7.5 mm inside 026 outer edge): {}"
                              .format(len(side_segs)))
                        for a_, b_ in side_segs:
                            for px, py in (a_, b_):
                                if abs(py - y_006) < 1e-6 and not (x0_006 - 1e-6 <= px <= x1_006 + 1e-6):
                                    log("WARNING: a 006 side ends at x {:.3f}, outside the 006 "
                                          "bottom line x range".format(px))
                        sym006 = item_006_syms[0][0]
                        if not sym006.IsActive:
                            sym006.Activate()
                            doc.Regenerate()
                        place_line_detail(sym006, x0_006, y_006, x1_006, y_006)
                        place_detail_on_segs(sym006, side_segs)
                    except Exception as ex:
                        item_006_msg = "could not place 006: {}".format(ex)
            except Exception as ex:
                item_msg = "could not place the detail item: {}".format(ex)
                if item_006_msg is None:
                    item_006_msg = "005 was not placed, so 006 was skipped"

        if item_008_msg is None:
            try:
                segs_008 = offset_poly_edges(poly, LAYER_008_OFFSET, ("side", "bottom"))
                if not segs_008:
                    raise Exception("no 026 edges to offset for 008")
                log("008 segments (15 mm from 026, no top): {}".format(len(segs_008)))
                sym008 = item_008_syms[0][0]
                if not sym008.IsActive:
                    sym008.Activate()
                    doc.Regenerate()
                if place_detail_on_segs(sym008, segs_008) < 1:
                    raise Exception("008 segments were too short to place")
            except Exception as ex:
                item_008_msg = "could not place 008: {}".format(ex)

        t.Commit()
    except Exception as ex:
        t.RollBack()
        forms.alert("Error creating Filled Region:\n{}".format(ex), exitscript=True)

    if SHOW_RESULT:
        msg = "Planter cavity filled ({:.3f} m2).".format(abs(area(poly)) * FT2_TO_M2)
        notes = []
        if item_msg is None:
            msg += "\n005 placed."
        else:
            notes.append("005 NOT placed: " + item_msg)
        if item_006_msg is None:
            msg += "\n006 placed (bottom + two straight sides)."
        else:
            notes.append("006 NOT placed: " + item_006_msg)
        if item_008_msg is None:
            msg += "\n008 placed (15 mm from 026, except top)."
        else:
            notes.append("008 NOT placed: " + item_008_msg)
        if notes:
            forms.alert(msg + "\n\n" + "\n".join(notes), title="Planter Fill")
        else:
            forms.toast(msg, title="Planter Fill")


if __name__ == "__main__":
    main()