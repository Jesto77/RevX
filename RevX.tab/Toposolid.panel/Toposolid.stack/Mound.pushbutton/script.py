# -*- coding: utf-8 -*-
"""
Smooth Dome Topography / Toposolid from Contour Lines
Uses Monotonic Cubic Hermite Splines (PCHIP) for true organic curvature.
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
GRID_SPACING = 1.0           # ft – smaller = ultra-smooth surface mesh
CONTOUR_SAMPLE = 1.0         # ft – sampling density along curves
DOME_TOP_BUMP_RATIO = 0.35   # Extra curvature rounding above highest contour
MAX_ARC_ANGLE_STEP = 0.08

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
# PCHIP MONOTONIC CUBIC INTERPOLATION (PERFECT S-CURVE)
# ----------------------------------------------------------

def create_pchip_spline(d_vals, z_vals):
    """
    Creates a smooth Monotonic Cubic Hermite Spline (PCHIP).
    Guarantees tangent-continuous curves without flat plateaus or wiggles.
    """
    n = len(d_vals)
    if n == 1:
        return lambda d: z_vals[0]

    dx = [d_vals[i+1] - d_vals[i] for i in range(n-1)]
    dz = [z_vals[i+1] - z_vals[i] for i in range(n-1)]
    S = [dz[i] / dx[i] if dx[i] > 1e-9 else 0.0 for i in range(n-1)]

    m = [0.0] * n
    m[0] = 0.0   # Flat tangent at grade boundary
    m[-1] = 0.0  # Flat tangent at dome top crest

    for i in range(1, n - 1):
        if S[i-1] * S[i] <= 0:
            m[i] = 0.0
        else:
            m[i] = (2.0 * S[i-1] * S[i]) / (S[i-1] + S[i])

    def evaluate(d):
        if d <= d_vals[0]:
            return z_vals[0]
        if d >= d_vals[-1]:
            return z_vals[-1]

        idx = 0
        for i in range(n - 1):
            if d_vals[i] <= d <= d_vals[i+1]:
                idx = i
                break

        x0, x1 = d_vals[idx], d_vals[idx+1]
        y0, y1 = z_vals[idx], z_vals[idx+1]
        h = x1 - x0
        if abs(h) < 1e-9:
            return y0

        t = (d - x0) / h
        h00 = (1.0 + 2.0 * t) * ((1.0 - t) ** 2)
        h10 = t * ((1.0 - t) ** 2)
        h01 = (t ** 2) * (3.0 - 2.0 * t)
        h11 = (t ** 2) * (t - 1.0)

        return h00 * y0 + h10 * h * m[idx] + h01 * y1 + h11 * h * m[idx+1]

    return evaluate

# ----------------------------------------------------------
# WAVE OF POINTS / CONCENTRIC RING LAYOUT
# ----------------------------------------------------------

def sample_loop_by_length(loop, step, start=0.0):
    pts = []
    next_s = start
    c0 = 0.0
    for crv in loop:
        try:
            length = crv.Length
        except Exception:
            continue
        if length <= 1e-9:
            continue
        seg_step = step
        try:
            radius = crv.Radius
            if radius and radius > 1e-6:
                seg_step = max(1e-4, min(step, radius * MAX_ARC_ANGLE_STEP))
        except Exception:
            pass
        while next_s < c0 + length - 1e-9:
            t = (next_s - c0) / length
            pts.append(crv.Evaluate(t, True))
            next_s += seg_step
        c0 += length
    return pts

def poly_area_xy(pts):
    a = 0.0
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        a += pts[i].X * pts[j].Y - pts[j].X * pts[i].Y
    return abs(a * 0.5)

def offset_loop(loop, dist):
    try:
        return CurveLoop.CreateViaOffset(loop, dist, XYZ.BasisZ)
    except Exception:
        return None

def build_rings(loop, row_step, point_step, max_rings=2000):
    seed = sample_loop_by_length(loop, point_step)
    base_area = poly_area_xy(seed)
    if base_area <= 1e-9:
        return []

    sign = None
    best_area = None
    for sg in (1.0, -1.0):
        test = offset_loop(loop, sg * row_step)
        if test is None:
            continue
        a = poly_area_xy(sample_loop_by_length(test, point_step))
        if a < base_area - 1e-6 and (best_area is None or a < best_area):
            sign, best_area = sg, a
    if sign is None:
        return []

    rings = []
    cur = loop
    prev_area = base_area
    for k in range(1, max_rings + 1):
        nxt = offset_loop(cur, sign * row_step)
        if nxt is None:
            break
        area = poly_area_xy(sample_loop_by_length(nxt, point_step))
        if area >= prev_area - 1e-6:
            break
        shift = point_step * 0.5 if (k % 2) else 0.0
        pts = sample_loop_by_length(nxt, point_step, shift)
        if len(pts) < 3:
            break
        rings.append(pts)
        cur = nxt
        prev_area = area
    return rings

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

model_curves = [get_curve(doc.GetElement(r.ElementId)) for r in refs if get_curve(doc.GetElement(r.ElementId))]
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

boundary_curves = [get_curve(doc.GetElement(r.ElementId)) for r in brefs if get_curve(doc.GetElement(r.ElementId))]
if not boundary_curves:
    forms.alert("No boundary curves found.", exitscript=True)

# Extract Densified Points
contour_pts = []
for crv in model_curves:
    contour_pts.extend(densify_curve(crv, CONTOUR_SAMPLE))

if len(contour_pts) < 3:
    forms.alert("Not enough contour points.", exitscript=True)

base_z = min(p.Z for p in contour_pts)

# Build Horizontal Boundary Loop
loop = build_boundary_loop(boundary_curves, base_z)
if loop is None:
    forms.alert("Boundary lines must form a single continuous closed shape.", exitscript=True)

boundary_poly = []
for crv in loop:
    for p in crv.Tessellate()[:-1]:
        boundary_poly.append((p.X, p.Y))

# ----------------------------------------------------------
# MAP CONTOURS TO RADIAL DISTANCES FOR SMOOTH SPLINE
# ----------------------------------------------------------

# Group contours by Z elevation
z_groups = {}
for p in contour_pts:
    z_key = round(p.Z, 3)
    if z_key not in z_groups:
        z_groups[z_key] = []
    z_groups[z_key].append(p)

sorted_zs = sorted(z_groups.keys())

# Build Spline Control Points: (Distance from boundary, Elevation)
d_control = [0.0]
z_control = [base_z]

for z_val in sorted_zs:
    pts = z_groups[z_val]
    # Calculate average distance of this contour level from the boundary
    avg_d = sum(min_dist_to_poly_edges(p.X, p.Y, boundary_poly) for p in pts) / float(len(pts))
    if avg_d > d_control[-1]:
        d_control.append(avg_d)
        z_control.append(z_val)

# Compute max interior distance across the entire boundary footprint
rings = build_rings(loop, GRID_SPACING * 0.866, GRID_SPACING)
max_interior_d = (len(rings) + 1) * (GRID_SPACING * 0.866) if rings else max(d_control) * 1.3

# Softly round the top crest (Dome Peak)
if max_interior_d > d_control[-1]:
    dz = (z_control[-1] - z_control[-2]) if len(z_control) > 1 else 0.5
    peak_z = z_control[-1] + dz * DOME_TOP_BUMP_RATIO
    d_control.append(max_interior_d)
    z_control.append(peak_z)

# Generate Continuous Spline Function
z_spline = create_pchip_spline(d_control, z_control)

# ----------------------------------------------------------
# BUILD DENSE SMOOTH MESH
# ----------------------------------------------------------

xy_pts = []
if len(rings) >= 2:
    for ring in rings:
        xy_pts.extend(ring)
else:
    # Grid Fallback
    xs = [p[0] for p in boundary_poly]
    ys = [p[1] for p in boundary_poly]
    x = min(xs)
    while x <= max(xs):
        y = min(ys)
        while y <= max(ys):
            if point_in_poly(x, y, boundary_poly):
                xy_pts.append(XYZ(x, y, 0))
            y += GRID_SPACING
        x += GRID_SPACING

# Calculate 3D Points using the Spline Function
smooth_pts = []
for p in xy_pts:
    d = min_dist_to_poly_edges(p.X, p.Y, boundary_poly)
    z = z_spline(d)
    smooth_pts.append(XYZ(p.X, p.Y, z))

# Add Boundary Anchor Ring (at base_z)
for crv in loop:
    for p in crv.Tessellate()[:-1]:
        smooth_pts.append(XYZ(p.X, p.Y, base_z))

# ----------------------------------------------------------
# CREATE TOPO / TOPOSOLID (NO SUCCESS POPUP)
# ----------------------------------------------------------

t = Transaction(doc, "Create Smooth Dome Topography")
t.Start()
try:
    if version >= 2024 and HAS_TOPOSOLID:
        topo_type = FilteredElementCollector(doc).OfClass(ToposolidType).FirstElement()
        level = FilteredElementCollector(doc).OfClass(Level).FirstElement()
        if topo_type is None or level is None:
            raise Exception("No ToposolidType or Level found in project.")
        
        boundaries = List[CurveLoop]()
        boundaries.Add(loop)
        
        Toposolid.Create(
            doc,
            boundaries,
            List[XYZ](smooth_pts),
            topo_type.Id,
            level.Id
        )
    else:
        if TopographySurface is None:
            raise Exception("TopographySurface not available in this Revit version.")
        TopographySurface.Create(doc, List[XYZ](smooth_pts))
        
    t.Commit()

except Exception as e:
    t.RollBack()
    forms.alert("Failed to create topography:\n\n{}".format(str(e)))