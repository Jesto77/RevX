# -*- coding: utf-8 -*-
"""
OrganicMound.py
---------------
Creates a smooth organic toposolid/topography mound from selected
boundary model lines.

Modes:
  Slope Mode      - height derived from slope ratio (1:N)
  Max Height Mode - user enters height in MILLIMETRES; converted to feet
                    internally (Revit internal unit = feet)

Smoothing uses a quintic smooth-step blended with a Gaussian falloff
for a natural real-world hill profile with no flat top, no centre spike,
and a gentle zero-gradient landing at the boundary edge.

Compatible: Revit 2018-2025+  |  IronPython 2.7 (PyRevit)
"""

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import (
    FilteredElementCollector, Level, Transaction,
    CurveLoop, XYZ, Line, Arc,
)
from Autodesk.Revit.UI.Selection import ObjectType
from System.Collections.Generic import List
import math

# TopographySurface moved to Autodesk.Revit.DB.Architecture in some builds
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
# DOCUMENT / VERSION
# ----------------------------------------------------------

doc     = revit.doc
uidoc   = revit.uidoc
version = int(doc.Application.VersionNumber)

try:
    from Autodesk.Revit.DB import Toposolid, ToposolidType
    HAS_TOPOSOLID = True
except ImportError:
    HAS_TOPOSOLID = False

# ----------------------------------------------------------
# UNIT CONVERSION
# Revit internal unit is always FEET regardless of project units
# ----------------------------------------------------------

MM_TO_FT = 1.0 / 304.8   # 1 mm = 0.00328084 ft

# ----------------------------------------------------------
# SETTINGS
# ----------------------------------------------------------

GRID_SPACING  = 2.0   # feet - grid point density (reduce for smoother but slower)
BOUNDARY_DIVS = 150   # samples per boundary curve

# ----------------------------------------------------------
# MODE SELECTION
# ----------------------------------------------------------

mode = forms.CommandSwitchWindow.show(
    ["Slope Mode", "Max Height Mode"],
    message="Choose mound creation mode"
)

if not mode:
    script.exit()

SLOPE_RATIO    = None
MAX_HEIGHT_FT  = None   # always stored in FEET internally

if mode == "Slope Mode":
    raw = forms.ask_for_string(
        default="5",
        prompt="Enter slope ratio (e.g. 5 = 1:5, rise 1 for every 5 of run):",
        title="Slope Ratio"
    )
    if not raw:
        script.exit()
    try:
        SLOPE_RATIO = float(raw)
        if SLOPE_RATIO <= 0:
            raise ValueError
    except Exception:
        forms.alert("Invalid slope ratio. Enter a positive number.")
        script.exit()

else:  # Max Height Mode
    raw = forms.ask_for_string(
        default="1500",
        prompt="Enter maximum mound height in MILLIMETRES (mm):",
        title="Maximum Height (mm)"
    )
    if not raw:
        script.exit()
    try:
        height_mm = float(raw)
        if height_mm <= 0:
            raise ValueError
        MAX_HEIGHT_FT = height_mm * MM_TO_FT   # convert mm -> feet
    except Exception:
        forms.alert("Invalid height. Enter a positive number in mm.")
        script.exit()

# ----------------------------------------------------------
# CURVE HELPERS
# ----------------------------------------------------------

def get_curve(element):
    try:
        return element.GeometryCurve
    except Exception:
        return None


def sample_curve(curve, divisions=150):
    pts = []
    try:
        for i in range(divisions + 1):
            pts.append(curve.Evaluate(float(i) / divisions, True))
    except Exception:
        try:
            pts = list(curve.Tessellate())
        except Exception:
            pass
    return pts


def remove_duplicates(points, tol=0.05):
    cleaned = []
    for p in points:
        dup = False
        for q in cleaned:
            if p.DistanceTo(q) < tol:
                dup = True
                break
        if not dup:
            cleaned.append(p)
    return cleaned

# ----------------------------------------------------------
# NEW: VALID BOUNDARY LOOP BUILDER (fixes the error)
# ----------------------------------------------------------

def flatten_curve_segments(crv, target_z, tol=1e-6):
    """
    Return a list of Curve objects (typically one) that are
    the projection of the input curve onto the horizontal plane Z=target_z.
    For Line and Arc we create a clean flattened copy.
    For any other type (spline, ellipse, etc.) we tessellate and return
    a series of straight line segments.
    """
    if isinstance(crv, Line):
        sp = crv.GetEndPoint(0)
        ep = crv.GetEndPoint(1)
        return [Line.CreateBound(XYZ(sp.X, sp.Y, target_z),
                                 XYZ(ep.X, ep.Y, target_z))]
    elif isinstance(crv, Arc):
        sp = crv.GetEndPoint(0)
        ep = crv.GetEndPoint(1)
        mp = crv.Evaluate(0.5, True)  # midpoint on curve
        return [Arc.Create(XYZ(sp.X, sp.Y, target_z),
                           XYZ(ep.X, ep.Y, target_z),
                           XYZ(mp.X, mp.Y, target_z))]
    else:
        # For HermiteSpline, Ellipse, etc., tessellate and return line segments
        try:
            pts = list(crv.Tessellate())
            segments = []
            for i in range(len(pts) - 1):
                a = pts[i]
                b = pts[i+1]
                segments.append(Line.CreateBound(
                    XYZ(a.X, a.Y, target_z),
                    XYZ(b.X, b.Y, target_z)))
            return segments
        except Exception:
            return []  # skip unsupported curves


def build_boundary_loop(curves, target_z, tol=1e-6):
    """
    Flatten all curves, sort them into a continuous closed loop,
    and return a valid CurveLoop.
    Returns None if the loop cannot be closed.
    """
    if not curves:
        return None

    # Flatten every input curve into one or more segment curves
    all_segments = []
    for crv in curves:
        segs = flatten_curve_segments(crv, target_z)
        all_segments.extend(segs)

    if len(all_segments) < 2:
        return None

    # Sort segments to form a continuous chain
    remaining = all_segments[:]
    ordered = [remaining.pop(0)]
    while remaining:
        last_end = ordered[-1].GetEndPoint(1)
        found = False
        for i, seg in enumerate(remaining):
            start = seg.GetEndPoint(0)
            end   = seg.GetEndPoint(1)
            if last_end.DistanceTo(start) < tol:
                ordered.append(seg)
                remaining.pop(i)
                found = True
                break
            elif last_end.DistanceTo(end) < tol:
                ordered.append(seg.CreateReversed())
                remaining.pop(i)
                found = True
                break
        if not found:
            # Chain broken – maybe remaining segments belong to a separate loop
            # This is not a single closed boundary -> invalid
            return None

    # Verify closure
    first_start = ordered[0].GetEndPoint(0)
    last_end    = ordered[-1].GetEndPoint(1)
    gap = last_end.DistanceTo(first_start)
    if gap > 0.01:   # > 1 cm gap
        # Optionally auto-close tiny gaps (uncomment if needed)
        # if gap < 0.2:
        #     ordered.append(Line.CreateBound(last_end, first_start))
        # else:
        return None

    # Build the CurveLoop
    loop = CurveLoop()
    for seg in ordered:
        loop.Append(seg)
    return loop

# ----------------------------------------------------------
# GEOMETRY HELPERS
# ----------------------------------------------------------

def point_inside_boundary(x, y, pts):
    """Ray-casting 2-D point-in-polygon."""
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


def nearest_boundary_distance(x, y, boundary_pts):
    min_d = 1e18
    for p in boundary_pts:
        dx = p.X - x
        dy = p.Y - y
        d  = math.sqrt(dx * dx + dy * dy)
        if d < min_d:
            min_d = d
    return min_d

# ----------------------------------------------------------
# SMOOTHING PROFILE
# ----------------------------------------------------------

def quintic_smooth(t):
    """
    Quintic smooth-step:  6t^5 - 15t^4 + 10t^3
    - f(0) = 0,  f(1) = 1
    - f'(0) = 0, f'(1) = 0   (zero slope at both ends)
    - f''(0)= 0, f''(1)= 0   (zero curvature at both ends)
    Much smoother than a cosine and matches how real terrain transitions.
    """
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def gaussian_peak(t, sigma=0.42):
    """
    Gaussian bell centred at t=1 (the interior peak).
    sigma controls how quickly it falls from the peak.
    Normalised so gaussian_peak(1) = 1.
    """
    return math.exp(-((t - 1.0) ** 2) / (2.0 * sigma * sigma))


def mound_height(d, max_interior, target_height):
    """
    Blended profile for a natural-looking hill:

      t = d / max_interior  in [0, 1]

    We blend two curves:
      A) Quintic smooth-step rising from 0 at the boundary (t=0)
         to 1 at the peak (t=1).  Controls the outer slope behaviour.
      B) Gaussian bell centred at the peak (t=1).
         Controls the smooth rounding at the top.

    Blend weight: near boundary -> mostly quintic (respects slope),
                  near peak     -> mostly gaussian (natural rounding).

    Result: zero at edge, smooth slope up, rounded organic peak,
            no flat top, no crater, no spike.
    """
    if max_interior <= 0:
        return 0.0

    t = min(d / max_interior, 1.0)

    # Quintic component  (dominates at low t = near boundary)
    q = quintic_smooth(t)

    # Gaussian component (dominates at high t = near peak)
    g = gaussian_peak(t)

    # Blend: use t itself as the blend weight so the outer slope is
    # governed by quintic and the top is shaped by the gaussian
    blended = (1.0 - t) * q + t * g

    # Normalise so blended(1) = 1  (gaussian_peak(1) = 1, quintic(1) = 1 -> blend = 1)
    return target_height * blended

# ----------------------------------------------------------
# BUILD MOUND POINT GRID
# ----------------------------------------------------------

def create_mound_points(boundary_pts, base_z):
    """
    Generate grid points with height based on distance from boundary.
    base_z is the common Z elevation for all boundary points.
    """

    min_x  = min(p.X for p in boundary_pts)
    max_x  = max(p.X for p in boundary_pts)
    min_y  = min(p.Y for p in boundary_pts)
    max_y  = max(p.Y for p in boundary_pts)

    # ── Pass 1: find max interior distance ───────────────────────────────────
    max_interior = 0.0
    x = min_x
    while x <= max_x + 1e-6:
        y = min_y
        while y <= max_y + 1e-6:
            if point_inside_boundary(x, y, boundary_pts):
                d = nearest_boundary_distance(x, y, boundary_pts)
                if d > max_interior:
                    max_interior = d
            y += GRID_SPACING
        x += GRID_SPACING

    if max_interior <= 0:
        forms.alert(
            "Could not determine interior distances.\n"
            "Make sure the boundary forms a closed loop."
        )
        script.exit()

    # ── Determine peak height in feet ────────────────────────────────────────
    if MAX_HEIGHT_FT is not None:
        # Max Height Mode: user's mm value already converted to feet
        target_height = MAX_HEIGHT_FT
    else:
        # Slope Mode: match 1:SLOPE_RATIO at the boundary.
        target_height = max_interior / SLOPE_RATIO

    # ── Pass 2: generate XYZ grid ────────────────────────────────────────────
    topo_pts = []
    x = min_x
    while x <= max_x + 1e-6:
        y = min_y
        while y <= max_y + 1e-6:
            if point_inside_boundary(x, y, boundary_pts):
                d = nearest_boundary_distance(x, y, boundary_pts)
                h = mound_height(d, max_interior, target_height)
                topo_pts.append(XYZ(x, y, base_z + h))
            y += GRID_SPACING
        x += GRID_SPACING

    return topo_pts

# ----------------------------------------------------------
# SELECT BOUNDARY MODEL LINES
# ----------------------------------------------------------

try:
    refs = uidoc.Selection.PickObjects(
        ObjectType.Element,
        "Select OUTER boundary model lines, then click Finish"
    )
except Exception:
    script.exit()

boundary_curves = []
for ref in refs:
    el    = doc.GetElement(ref.ElementId)
    curve = get_curve(el)
    if curve:
        boundary_curves.append(curve)

if not boundary_curves:
    forms.alert("No valid curves selected.")
    script.exit()

# ----------------------------------------------------------
# EXTRACT & VALIDATE BOUNDARY POINTS
# ----------------------------------------------------------

boundary_pts = []
for c in boundary_curves:
    boundary_pts.extend(sample_curve(c, BOUNDARY_DIVS))

boundary_pts = remove_duplicates(boundary_pts)

if len(boundary_pts) < 3:
    forms.alert("Not enough boundary points (need at least 3).")
    script.exit()

# ----------------------------------------------------------
# COMMON BASE ELEVATION (flatten boundary loops here)
# ----------------------------------------------------------
base_z = min(p.Z for p in boundary_pts)

# ----------------------------------------------------------
# GENERATE MOUND GRID (now uses pre‑computed base_z)
# ----------------------------------------------------------

graded_pts = create_mound_points(boundary_pts, base_z)

if len(graded_pts) < 3:
    forms.alert(
        "Failed to generate interior grid points.\n"
        "Make sure the boundary encloses a large enough area "
        "relative to the grid spacing ({} ft).".format(GRID_SPACING)
    )
    script.exit()

# ----------------------------------------------------------
# BUILD A VALID CURVE LOOP (FIXED – no more "invalid boundary" error)
# ----------------------------------------------------------

loop = None
if version >= 2024 and HAS_TOPOSOLID:
    loop = build_boundary_loop(boundary_curves, base_z)
    if loop is None:
        forms.alert(
            "Cannot create a valid boundary loop for Toposolid.\n"
            "Your boundary lines must:\n"
            "  - form a single closed loop\n"
            "  - be continuous (no gaps)\n"
            "  - all lie in a horizontal plane (flattened automatically)\n\n"
            "Please check the selection and try again."
        )
        script.exit()
else:
    # For older TopographySurface we don't need a loop, but we still
    # build an empty one to keep the code structure consistent.
    loop = CurveLoop()

boundaries = List[CurveLoop]()
boundaries.Add(loop)

# ----------------------------------------------------------
# CREATE TOPOSOLID / TOPOGRAPHY SURFACE
# ----------------------------------------------------------

# Build summary string for success alert
if MAX_HEIGHT_FT is not None:
    height_mm = MAX_HEIGHT_FT / MM_TO_FT
    mode_detail = "Max Height: {} mm  ({:.4f} ft)".format(
        int(round(height_mm)), MAX_HEIGHT_FT
    )
else:
    mode_detail = "Slope Ratio: 1:{}".format(SLOPE_RATIO)

t = Transaction(doc, "Organic Mound")
t.Start()

try:
    if version >= 2024 and HAS_TOPOSOLID:
        topo_type = (
            FilteredElementCollector(doc)
            .OfClass(ToposolidType)
            .FirstElement()
        )
        level = (
            FilteredElementCollector(doc)
            .OfClass(Level)
            .FirstElement()
        )
        Toposolid.Create(
            doc,
            boundaries,
            List[XYZ](graded_pts),
            topo_type.Id,
            level.Id
        )
    else:
        if TopographySurface is None:
            raise Exception(
                "TopographySurface could not be imported.\n"
                "This Revit version may not support it. "
                "Try Revit 2024+ for Toposolid support."
            )
        TopographySurface.Create(
            doc,
            List[XYZ](graded_pts)
        )

    t.Commit()
    forms.alert(
        "Mound created successfully!\n\n"
        "Mode         : {}\n"
        "{}\n"
        "Grid points  : {}\n"
        "Boundary pts : {}".format(
            mode,
            mode_detail,
            len(graded_pts),
            len(boundary_pts)
        )
    )

except Exception as e:
    t.RollBack()
    forms.alert("Failed to create mound:\n\n{}".format(str(e)))