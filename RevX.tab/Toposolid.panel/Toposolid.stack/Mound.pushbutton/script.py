# -*- coding: utf-8 -*-
"""
Smooth Topography / Toposolid from Contour Lines
Thin-plate-spline (minimum-curvature) surface through the picked contours:
smooth in every direction, passes through every contour, works for any
boundary shape (concave / stepped / L-shaped).
"""

from __future__ import division
import math
from pyrevit import revit, forms, script
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ObjectType
from System.Collections.Generic import List

doc = revit.doc
uidoc = revit.uidoc
version = int(doc.Application.VersionNumber)

try:
    from Autodesk.Revit.DB import Toposolid, ToposolidType
    HAS_TOPOSOLID = True
except ImportError:
    HAS_TOPOSOLID = False

TopographySurface = None
try:
    from Autodesk.Revit.DB import TopographySurface
except ImportError:
    pass
if TopographySurface is None:
    try:
        from Autodesk.Revit.DB.Architecture import TopographySurface
    except ImportError:
        pass

# ----------------------------------------------------------
# SETTINGS
# ----------------------------------------------------------
MIN_SPACING = 1.0            # ft – finest mesh spacing allowed
MAX_GRID_POINTS = 3500       # interior mesh points (speed vs smoothness)
CONTROL_POINTS = 260         # points used to solve the spline
OVERSHOOT = 0.10             # allowed rise above top contour (x relief)
SMOOTHNESS = 1e-2            # spline relaxation: higher = softer, less exact (0 = exact)
SMOOTH_PASSES = 6            # mesh smoothing passes (removes steps / ripples)
SMOOTH_STRENGTH = 0.6        # 0..1 blend toward neighbour average per pass
EXACT_CONTOURS = False       # True = keep contour points at exact elevation

# ----------------------------------------------------------
# CURVE HELPERS
# ----------------------------------------------------------

def get_curve(el):
    try:
        if isinstance(el, (ModelCurve, DetailCurve, CurveElement)):
            return el.GeometryCurve
    except Exception:
        pass
    return None

def densify_curve(crv, spacing=1.0):
    pts = []
    try:
        length = crv.Length
        if length < 1e-9:
            return pts
        n = max(int(math.ceil(length / spacing)), 2)
        for i in range(n + 1):
            pts.append(crv.Evaluate(float(i) / n, True))
    except Exception:
        try:
            pts = list(crv.Tessellate())
        except Exception:
            pass
    return pts

def flatten_curve_segments(crv, target_z):
    is_bound = True
    try:
        is_bound = bool(crv.IsBound)
    except Exception:
        pass

    if is_bound and isinstance(crv, Line):
        sp, ep = crv.GetEndPoint(0), crv.GetEndPoint(1)
        a = XYZ(sp.X, sp.Y, target_z)
        b = XYZ(ep.X, ep.Y, target_z)
        if a.DistanceTo(b) < 1e-6:
            return []
        return [Line.CreateBound(a, b)]

    if is_bound and isinstance(crv, Arc):
        try:
            sp, ep = crv.GetEndPoint(0), crv.GetEndPoint(1)
            mp = crv.Evaluate(0.5, True)
            return [Arc.Create(
                XYZ(sp.X, sp.Y, target_z),
                XYZ(ep.X, ep.Y, target_z),
                XYZ(mp.X, mp.Y, target_z)
            )]
        except Exception:
            pass

    try:
        pts = list(crv.Tessellate())
        segs = []
        for i in range(len(pts) - 1):
            a = XYZ(pts[i].X, pts[i].Y, target_z)
            b = XYZ(pts[i + 1].X, pts[i + 1].Y, target_z)
            if a.DistanceTo(b) > 1e-6:
                segs.append(Line.CreateBound(a, b))
        return segs
    except Exception:
        return []

def build_boundary_loop(curves, target_z, tol=0.01):
    if not curves:
        return None

    segs = []
    for crv in curves:
        segs.extend(flatten_curve_segments(crv, target_z))
    if len(segs) < 1:
        return None

    if len(segs) == 1:
        c = segs[0]
        try:
            if c.IsClosed or c.GetEndPoint(0).DistanceTo(c.GetEndPoint(1)) < tol:
                loop = CurveLoop()
                loop.Append(c)
                return loop
        except Exception:
            pass
        return None

    remaining = segs[:]
    ordered = [remaining.pop(0)]
    while remaining:
        last_end = ordered[-1].GetEndPoint(1)
        found = False
        for i, seg in enumerate(remaining):
            s0 = seg.GetEndPoint(0)
            s1 = seg.GetEndPoint(1)
            if last_end.DistanceTo(s0) < tol:
                ordered.append(seg)
                remaining.pop(i)
                found = True
                break
            if last_end.DistanceTo(s1) < tol:
                ordered.append(seg.CreateReversed())
                remaining.pop(i)
                found = True
                break
        if not found:
            return None

    if ordered[-1].GetEndPoint(1).DistanceTo(ordered[0].GetEndPoint(0)) > 0.05:
        return None

    loop = CurveLoop()
    for s in ordered:
        loop.Append(s)
    return loop

# ----------------------------------------------------------
# GEOMETRY & DISTANCE UTILITIES
# ----------------------------------------------------------

def point_in_poly(px, py, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and \
           (px < (xj - xi) * (py - yi) / ((yj - yi) if abs(yj - yi) > 1e-15 else 1e-15) + xi):
            inside = not inside
        j = i
    return inside

def dist_point_to_segment_2d(px, py, ax, ay, bx, by):
    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay
    ab2 = abx * abx + aby * aby
    if ab2 < 1e-16:
        return math.hypot(apx, apy)
    t = (apx * abx + apy * aby) / ab2
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    qx, qy = ax + t * abx, ay + t * aby
    return math.hypot(px - qx, py - qy)

def min_dist_to_poly_edges(px, py, poly):
    n = len(poly)
    if n < 2:
        return 1e18
    best = 1e18
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        d = dist_point_to_segment_2d(px, py, ax, ay, bx, by)
        if d < best:
            best = d
    return best

# ----------------------------------------------------------
# THIN-PLATE SPLINE (minimum curvature surface)
# ----------------------------------------------------------

def solve_linear(A, b):
    """Gaussian elimination with partial pivoting (in place)."""
    n = len(b)
    for col in range(n):
        piv = col
        best = abs(A[col][col])
        for r in range(col + 1, n):
            v = abs(A[r][col])
            if v > best:
                best, piv = v, r
        if best < 1e-14:
            raise Exception("Contour data is degenerate (singular system).")
        if piv != col:
            A[col], A[piv] = A[piv], A[col]
            b[col], b[piv] = b[piv], b[col]
        prow = A[col]
        pv = prow[col]
        for r in range(col + 1, n):
            row = A[r]
            f = row[col] / pv
            if f != 0.0:
                for c in range(col, n):
                    row[c] -= f * prow[c]
                b[r] -= f * b[col]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        row = A[r]
        sm = b[r]
        for c in range(r + 1, n):
            sm -= row[c] * x[c]
        x[r] = sm / row[r]
    return x

def tps_phi(r2):
    if r2 < 1e-18:
        return 0.0
    return 0.5 * r2 * math.log(r2)

def build_tps(pts, vals, smoothing=1e-7):
    """pts: [(x, y)] normalised, vals: [z]. Returns f(x, y)."""
    n = len(pts)
    m = n + 3
    A = [[0.0] * m for _ in range(m)]
    b = [0.0] * m
    for i in range(n):
        xi, yi = pts[i]
        row = A[i]
        for j in range(n):
            dx = xi - pts[j][0]
            dy = yi - pts[j][1]
            row[j] = tps_phi(dx * dx + dy * dy)
        row[i] += smoothing
        row[n] = 1.0
        row[n + 1] = xi
        row[n + 2] = yi
        A[n][i] = 1.0
        A[n + 1][i] = xi
        A[n + 2][i] = yi
        b[i] = vals[i]
    sol = solve_linear(A, b)
    w = sol[:n]
    a0, a1, a2 = sol[n], sol[n + 1], sol[n + 2]

    def f(x, y):
        z = a0 + a1 * x + a2 * y
        for i in range(n):
            dx = x - pts[i][0]
            dy = y - pts[i][1]
            r2 = dx * dx + dy * dy
            if r2 > 1e-18:
                z += w[i] * 0.5 * r2 * math.log(r2)
        return z
    return f

# ----------------------------------------------------------
# SAMPLING HELPERS
# ----------------------------------------------------------

def sample_curve(crv, spacing):
    return densify_curve(crv, spacing)

def sample_loop(loop, spacing):
    pts = []
    for crv in loop:
        pts.extend(densify_curve(crv, spacing)[:-1])
    return pts

def poly_area(poly):
    a = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) * 0.5

def dedupe_xy(points, tol):
    seen = set()
    out = []
    for p in points:
        k = (int(round(p.X / tol)), int(round(p.Y / tol)))
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out

# ----------------------------------------------------------
# MAIN EXECUTION
# ----------------------------------------------------------

# 1. Pick Contours
try:
    refs = uidoc.Selection.PickObjects(
        ObjectType.Element,
        "1/2  Select CONTOUR model lines, then click Finish"
    )
except Exception:
    script.exit()

model_curves = [get_curve(doc.GetElement(r.ElementId)) for r in refs
                if get_curve(doc.GetElement(r.ElementId))]
if not model_curves:
    forms.alert("No valid contour curves detected.", exitscript=True)

# 2. Pick Boundary
try:
    brefs = uidoc.Selection.PickObjects(
        ObjectType.Element,
        "2/2  Select OUTER boundary model lines, then click Finish"
    )
except Exception:
    script.exit()

boundary_curves = [get_curve(doc.GetElement(r.ElementId)) for r in brefs
                   if get_curve(doc.GetElement(r.ElementId))]
if not boundary_curves:
    forms.alert("No boundary curves found.", exitscript=True)

all_z = []
for crv in model_curves:
    try:
        all_z.append(crv.GetEndPoint(0).Z)
    except Exception:
        pass
if not all_z:
    forms.alert("Could not read contour elevations.", exitscript=True)
base_z = min(all_z)
top_z = max(all_z)
relief = top_z - base_z
if relief < 1e-6:
    forms.alert("All contours are at the same elevation.", exitscript=True)

loop = build_boundary_loop(boundary_curves, base_z)
if loop is None:
    forms.alert("Boundary lines must form a single continuous closed shape.",
                exitscript=True)

boundary_poly = []
for crv in loop:
    for p in crv.Tessellate()[:-1]:
        boundary_poly.append((p.X, p.Y))

# ---- mesh spacing from the footprint area ----
area = poly_area(boundary_poly)
spacing = max(MIN_SPACING, math.sqrt(area / float(MAX_GRID_POINTS)))

# ---- contour points (own elevation, exact) ----
contour_pts = []
contour_len = 0.0
for crv in model_curves:
    contour_len += crv.Length
    for p in densify_curve(crv, spacing):
        contour_pts.append(p)
contour_pts = dedupe_xy(contour_pts, spacing * 0.25)

boundary_pts = [XYZ(p.X, p.Y, base_z) for p in sample_loop(loop, spacing)]
boundary_pts = dedupe_xy(boundary_pts, spacing * 0.25)

# ---- control points for the spline (limited count for speed) ----
ctrl_sp = max(spacing, contour_len / float(CONTROL_POINTS * 0.75))
ctrl = []
for crv in model_curves:
    ctrl.extend(densify_curve(crv, ctrl_sp))
ctrl = dedupe_xy(ctrl, ctrl_sp * 0.3)
bnd_sp = max(spacing, ctrl_sp * 1.5)
ctrl_b = dedupe_xy([XYZ(p.X, p.Y, base_z) for p in sample_loop(loop, bnd_sp)],
                   bnd_sp * 0.3)

# drop boundary controls sitting on top of contour controls (avoid conflicts)
filtered_b = []
for b in ctrl_b:
    clash = False
    for c in ctrl:
        if (b.X - c.X) ** 2 + (b.Y - c.Y) ** 2 < (bnd_sp * 0.5) ** 2:
            clash = True
            break
    if not clash:
        filtered_b.append(b)
ctrl_all = ctrl + filtered_b

if len(ctrl_all) < 4:
    forms.alert("Not enough contour points to build a surface.",
                exitscript=True)

# ---- normalise coordinates ----
xs = [p.X for p in ctrl_all] + [p[0] for p in boundary_poly]
ys = [p.Y for p in ctrl_all] + [p[1] for p in boundary_poly]
cx = (min(xs) + max(xs)) * 0.5
cy = (min(ys) + max(ys)) * 0.5
S = max(max(xs) - min(xs), max(ys) - min(ys)) * 0.5
if S < 1e-9:
    S = 1.0

def nx(x):
    return (x - cx) / S

def ny(y):
    return (y - cy) / S

tps_pts = [(nx(p.X), ny(p.Y)) for p in ctrl_all]
tps_vals = [(p.Z - base_z) / relief for p in ctrl_all]

try:
    z_fun = build_tps(tps_pts, tps_vals, SMOOTHNESS)
except Exception as e:
    forms.alert("Could not solve the surface:\n\n{}".format(e),
                exitscript=True)

def surface_z(x, y):
    z = base_z + z_fun(nx(x), ny(y)) * relief
    lo = base_z
    hi = top_z + relief * OVERSHOOT
    if z < lo:
        z = lo
    elif z > hi:
        z = hi
    return z

# ---- build mesh points: [x, y, z, locked] ----
cell = spacing
occupied = {}

def occ_key(x, y):
    return (int(math.floor(x / cell)), int(math.floor(y / cell)))

def near_occupied(x, y, r):
    ci, cj = occ_key(x, y)
    r2 = r * r
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for (px, py) in occupied.get((ci + di, cj + dj), ()):
                if (px - x) ** 2 + (py - y) ** 2 < r2:
                    return True
    return False

mesh = []          # [x, y, z, locked]
for p in boundary_pts:
    mesh.append([p.X, p.Y, base_z, True])          # boundary stays at base
    occupied.setdefault(occ_key(p.X, p.Y), []).append((p.X, p.Y))
for p in contour_pts:
    z = p.Z if EXACT_CONTOURS else surface_z(p.X, p.Y)
    mesh.append([p.X, p.Y, z, bool(EXACT_CONTOURS)])
    occupied.setdefault(occ_key(p.X, p.Y), []).append((p.X, p.Y))

bx = [p[0] for p in boundary_poly]
by = [p[1] for p in boundary_poly]
x = min(bx) + spacing * 0.5
while x <= max(bx):
    y = min(by) + spacing * 0.5
    while y <= max(by):
        if point_in_poly(x, y, boundary_poly) \
                and min_dist_to_poly_edges(x, y, boundary_poly) > spacing * 0.5 \
                and not near_occupied(x, y, spacing * 0.6):
            mesh.append([x, y, surface_z(x, y), False])
        y += spacing
    x += spacing

# ---- smoothing passes: remove sudden steps / ripples ----
nbr_hash = {}
for i, m in enumerate(mesh):
    nbr_hash.setdefault(occ_key(m[0], m[1]), []).append(i)

radius = spacing * 1.6
r2lim = radius * radius
neighbours = []
for i, m in enumerate(mesh):
    ci, cj = occ_key(m[0], m[1])
    lst = []
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for k in nbr_hash.get((ci + di, cj + dj), ()):
                if k != i:
                    o = mesh[k]
                    if (o[0] - m[0]) ** 2 + (o[1] - m[1]) ** 2 <= r2lim:
                        lst.append(k)
    neighbours.append(lst)

for _ in range(SMOOTH_PASSES):
    new_z = [m[2] for m in mesh]
    for i, m in enumerate(mesh):
        if m[3] or not neighbours[i]:
            continue
        avg = sum(mesh[k][2] for k in neighbours[i]) / float(len(neighbours[i]))
        new_z[i] = m[2] * (1.0 - SMOOTH_STRENGTH) + avg * SMOOTH_STRENGTH
    for i, m in enumerate(mesh):
        m[2] = new_z[i]

# never below the base level
for m in mesh:
    if m[2] < base_z:
        m[2] = base_z

mesh = [XYZ(m[0], m[1], m[2]) for m in mesh]

# ----------------------------------------------------------
# CREATE TOPO / TOPOSOLID
# ----------------------------------------------------------

t = Transaction(doc, "Create Smooth Topography")
t.Start()
try:
    if version >= 2024 and HAS_TOPOSOLID:
        topo_type = FilteredElementCollector(doc).OfClass(ToposolidType).FirstElement()
        levels = list(FilteredElementCollector(doc).OfClass(Level))
        level = min(levels, key=lambda l: abs(l.Elevation - base_z)) if levels else None
        if topo_type is None or level is None:
            raise Exception("No ToposolidType or Level found in project.")

        # thickness must comfortably exceed the relief
        needed = max(1.0, relief * 1.5)          # feet
        type_name = "Dome_{}mm".format(int(needed * 304.8))
        thick_type = None
        for tt in FilteredElementCollector(doc).OfClass(ToposolidType):
            if tt.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsString() == type_name:
                thick_type = tt
                break
        if thick_type is None:
            thick_type = topo_type.Duplicate(type_name)
            cs = thick_type.GetCompoundStructure()
            cs.SetLayerWidth(0, needed)
            thick_type.SetCompoundStructure(cs)
        topo_type = thick_type

        boundaries = List[CurveLoop]()
        boundaries.Add(loop)

        Toposolid.Create(
            doc,
            boundaries,
            List[XYZ](mesh),
            topo_type.Id,
            level.Id
        )
    else:
        if TopographySurface is None:
            raise Exception("TopographySurface not available in this Revit version.")
        TopographySurface.Create(doc, List[XYZ](mesh))

    t.Commit()

except Exception as e:
    t.RollBack()
    forms.alert("Failed to create topography:\n\n{}".format(str(e)))