# -*- coding: utf-8 -*-

from pyrevit import revit
from Autodesk.Revit.DB import *

doc = revit.doc

# ==========================================================
# GET OFF AXIS WALLS
# ==========================================================

walls = set()
warning_count = 0

for w in doc.GetWarnings():

    try:

        txt = w.GetDescriptionText().lower()

        if "wall is slightly off axis" in txt:

            warning_count += 1

            for eid in w.GetFailingElements():

                e = doc.GetElement(eid)

                if isinstance(e, Wall):
                    walls.add(e.Id)

    except:
        pass

print("")
print("OFF AXIS WARNINGS :", warning_count)
print("WALLS FOUND       :", len(walls))
print("")

# ==========================================================
# HELP: SNAP TO ORTHO
# ==========================================================

def fix_line(line):

    p1 = line.GetEndPoint(0)
    p2 = line.GetEndPoint(1)

    dx = abs(p2.X - p1.X)
    dy = abs(p2.Y - p1.Y)

    # decide dominant direction
    if dx > dy:
        return Line.CreateBound(
            XYZ(p1.X, p1.Y, p1.Z),
            XYZ(p2.X, p1.Y, p2.Z)
        )
    else:
        return Line.CreateBound(
            XYZ(p1.X, p1.Y, p1.Z),
            XYZ(p1.X, p2.Y, p2.Z)
        )

# ==========================================================
# REBUILD WALLS (REAL FIX)
# ==========================================================

t = Transaction(doc, "Fix Off Axis Walls (Rebuild)")
t.Start()

fixed = 0

for wid in walls:

    old = doc.GetElement(wid)

    if not old:
        continue

    try:

        loc = old.Location

        if not isinstance(loc, LocationCurve):
            continue

        curve = loc.Curve

        if not isinstance(curve, Line):
            continue

        new_curve = fix_line(curve)

        wall_type = old.WallType.Id
        level_id = old.LevelId

        height = 10.0
        offset = 0.0

        try:
            p = old.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM)
            if p:
                height = p.AsDouble()
        except:
            pass

        try:
            p = old.get_Parameter(BuiltInParameter.WALL_BASE_OFFSET)
            if p:
                offset = p.AsDouble()
        except:
            pass

        # CREATE CLEAN WALL
        new_wall = Wall.Create(
            doc,
            new_curve,
            wall_type,
            level_id,
            height,
            offset,
            False,
            False
        )

        # DELETE OLD WALL
        doc.Delete(old.Id)

        fixed += 1

    except:
        pass

doc.Regenerate()
t.Commit()

# ==========================================================
# FINAL CHECK
# ==========================================================

remaining = 0

for w in doc.GetWarnings():

    try:

        if "wall is slightly off axis" in w.GetDescriptionText().lower():
            remaining += 1

    except:
        pass

print("")
print("===================================")
print("WALLS FIXED   :", fixed)
print("REMAINING     :", remaining)
print("===================================")
print("")
print("DONE")