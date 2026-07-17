# -*- coding: utf-8 -*-

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ObjectType
from System.Collections.Generic import List

doc = revit.doc
uidoc = revit.uidoc

app = doc.Application
version = int(app.VersionNumber)

# ----------------------------------------------------------
# SAFE CURVE EXTRACTOR (CRITICAL FIX)
# ----------------------------------------------------------

def get_curve(el):
    """Extract geometry curve from any line‑type element."""
    try:
        if isinstance(el, ModelCurve):
            return el.GeometryCurve
        if isinstance(el, DetailCurve):
            return el.GeometryCurve
        if isinstance(el, CurveElement):
            return el.GeometryCurve
    except:
        pass
    return None

# ----------------------------------------------------------
# BUILD A VALID, CLOSED, HORIZONTAL CURVE LOOP
# ----------------------------------------------------------

def build_boundary_loop(curves, target_elevation, tol=1e-6):
    """
    Takes a list of un-ordered boundary curves, flattens them
    to the given elevation, sorts them into a continuous closed loop,
    and returns a valid CurveLoop.
    Handles single closed curves (e.g. full circles) directly.
    Returns None if impossible.
    """
    if not curves:
        return None

    # Flatten curves to horizontal plane at target_elevation
    flat_curves = []
    for crv in curves:
        try:
            flat = flatten_curve(crv, target_elevation)
            if flat:
                flat_curves.append(flat)
        except:
            continue

    if not flat_curves:
        return None

    # --- Handle a single closed curve (e.g. a full circle) ---
    if len(flat_curves) == 1:
        single_crv = flat_curves[0]
        start = single_crv.GetEndPoint(0)
        end = single_crv.GetEndPoint(1)
        if start.DistanceTo(end) < tol:   # closed curve
            loop = CurveLoop()
            loop.Append(single_crv)
            return loop
        else:
            # Single open curve cannot form a closed loop
            return None

    # --- Sort multiple curves to form a continuous chain ---
    remaining = flat_curves[:]
    sorted_curves = [remaining.pop(0)]
    while remaining:
        last_end = sorted_curves[-1].GetEndPoint(1)
        found = False
        for i, crv in enumerate(remaining):
            start = crv.GetEndPoint(0)
            end = crv.GetEndPoint(1)
            if last_end.DistanceTo(start) < tol:
                sorted_curves.append(crv)
                remaining.pop(i)
                found = True
                break
            elif last_end.DistanceTo(end) < tol:
                sorted_curves.append(crv.CreateReversed())
                remaining.pop(i)
                found = True
                break
        if not found:
            return None   # chain broken

    # Check closure: last curve's end must match first curve's start
    first_start = sorted_curves[0].GetEndPoint(0)
    last_end = sorted_curves[-1].GetEndPoint(1)
    if last_end.DistanceTo(first_start) > 0.01:   # ~1 cm gap
        return None

    loop = CurveLoop()
    for crv in sorted_curves:
        loop.Append(crv)
    return loop

def flatten_curve(crv, z):
    """
    Project a curve onto the horizontal plane at Z = z.
    Works for Line and Arc (including full circles).
    """
    if isinstance(crv, Line):
        sp = crv.GetEndPoint(0)
        ep = crv.GetEndPoint(1)
        return Line.CreateBound(
            XYZ(sp.X, sp.Y, z),
            XYZ(ep.X, ep.Y, z)
        )
    elif isinstance(crv, Arc):
        sp = crv.GetEndPoint(0)
        ep = crv.GetEndPoint(1)
        mp = crv.Evaluate(0.5, True)
        return Arc.Create(
            XYZ(sp.X, sp.Y, z),
            XYZ(ep.X, ep.Y, z),
            XYZ(mp.X, mp.Y, z)
        )
    else:
        # Unsupported curve type – skip
        return None

# ----------------------------------------------------------
# PICK CONTOUR LINES
# ----------------------------------------------------------

try:
    refs = uidoc.Selection.PickObjects(
        ObjectType.Element,
        "Select contour model lines"
    )
except:
    script.exit()

model_curves = []
for r in refs:
    el = doc.GetElement(r.ElementId)
    crv = get_curve(el)
    if crv:
        model_curves.append(crv)

if len(model_curves) == 0:
    forms.alert(
        "No valid curves detected.\n"
        "You may have selected Detail Lines or non‑model curves."
    )
    script.exit()

# ----------------------------------------------------------
# PICK OUTER BOUNDARY
# ----------------------------------------------------------

forms.alert("Select OUTER boundary model lines in any order.\n"
            "They will be sorted automatically.")

try:
    brefs = uidoc.Selection.PickObjects(
        ObjectType.Element,
        "Select outer boundary lines"
    )
except:
    script.exit()

boundary_curves = []
for r in brefs:
    el = doc.GetElement(r.ElementId)
    crv = get_curve(el)
    if crv:
        boundary_curves.append(crv)

if len(boundary_curves) == 0:
    forms.alert("No boundary curves found.")
    script.exit()

# ----------------------------------------------------------
# EXTRACT TOPO POINTS
# ----------------------------------------------------------

all_points = []
for crv in model_curves:
    try:
        pts = crv.Tessellate()
        all_points.extend(pts)
    except:
        pass

unique_pts = []
tol = 0.2
for p in all_points:
    if not any(p.DistanceTo(q) < tol for q in unique_pts):
        unique_pts.append(p)

if len(unique_pts) < 3:
    forms.alert(
        "Not enough topo points!\n\n"
        "Points found: {}\n\n"
        "Fix: Ensure you selected REAL model lines."
        .format(len(unique_pts))
    )
    script.exit()

# ----------------------------------------------------------
# CREATE TOPO / TOPOSOLID
# ----------------------------------------------------------

t = Transaction(doc, "Create Topography")
t.Start()

try:
    if version >= 2024:
        topo_type = FilteredElementCollector(doc)\
            .OfClass(ToposolidType)\
            .FirstElement()
        level = FilteredElementCollector(doc)\
            .OfClass(Level)\
            .FirstElement()

        target_z = level.Elevation
        loop = build_boundary_loop(boundary_curves, target_z)

        if loop is None:
            t.RollBack()
            forms.alert(
                "Cannot create a valid boundary loop.\n"
                "Check that your boundary lines:\n"
                "- are continuous and closed\n"
                "- can be flattened to a single horizontal plane\n"
                "- do not self‑intersect"
            )
            script.exit()

        boundaries = List[CurveLoop]()
        boundaries.Add(loop)

        topo = Toposolid.Create(
            doc,
            boundaries,
            List[XYZ](unique_pts),
            topo_type.Id,
            level.Id
        )

    else:
        topo = TopographySurface.Create(
            doc,
            List[XYZ](unique_pts)
        )

    t.Commit()
    forms.alert("Topography created successfully.")

except Exception as e:
    t.RollBack()
    forms.alert(str(e))