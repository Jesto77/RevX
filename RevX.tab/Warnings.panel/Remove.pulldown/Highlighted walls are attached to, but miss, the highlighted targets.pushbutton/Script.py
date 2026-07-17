# -*- coding: utf-8 -*-

from pyrevit import revit
from Autodesk.Revit.DB import *

doc = revit.doc
view = doc.ActiveView

# ==========================================================
# FIND SOLID FILL PATTERN
# ==========================================================

solid_fill = None

for fp in FilteredElementCollector(doc).OfClass(FillPatternElement):

    try:

        if fp.GetFillPattern().IsSolidFill:
            solid_fill = fp
            break

    except:
        pass

# ==========================================================
# OVERRIDE SETTINGS (RED)
# ==========================================================

ogs = OverrideGraphicSettings()

red = Color(255, 0, 0)

try:
    ogs.SetSurfaceForegroundPatternId(solid_fill.Id)
    ogs.SetSurfaceForegroundPatternColor(red)
except:
    pass

try:
    ogs.SetCutForegroundPatternId(solid_fill.Id)
    ogs.SetCutForegroundPatternColor(red)
except:
    pass

try:
    ogs.SetProjectionLineColor(red)
except:
    pass

# ==========================================================
# GET WALLS WITH WARNING
# ==========================================================

warning_walls = set()
warning_count = 0

for w in doc.GetWarnings():

    try:

        txt = w.GetDescriptionText().lower()

        if (
            "walls are attached to" in txt and
            "miss" in txt
        ):

            warning_count += 1

            for eid in w.GetFailingElements():

                elem = doc.GetElement(eid)

                if isinstance(elem, Wall):
                    warning_walls.add(elem.Id.IntegerValue)

    except:
        pass

print("")
print("WARNINGS FOUND : {}".format(warning_count))
print("WALLS FOUND    : {}".format(len(warning_walls)))
print("")

# ==========================================================
# REBUILD WALLS
# ==========================================================

t = Transaction(doc, "Rebuild Problem Walls")
t.Start()

rebuilt = 0
highlighted = 0

for wid_int in warning_walls:

    old_wall = doc.GetElement(ElementId(wid_int))

    if not old_wall:
        continue

    try:

        loc = old_wall.Location

        if not isinstance(loc, LocationCurve):
            continue

        curve = loc.Curve

        wall_type = old_wall.WallType.Id
        level_id = old_wall.LevelId

        height = 10.0
        offset = 0.0

        # --------------------------------------------------
        # HEIGHT
        # --------------------------------------------------

        try:

            p = old_wall.get_Parameter(
                BuiltInParameter.WALL_USER_HEIGHT_PARAM
            )

            if p:
                height = p.AsDouble()

        except:
            pass

        # --------------------------------------------------
        # OFFSET
        # --------------------------------------------------

        try:

            p = old_wall.get_Parameter(
                BuiltInParameter.WALL_BASE_OFFSET
            )

            if p:
                offset = p.AsDouble()

        except:
            pass

        # --------------------------------------------------
        # CREATE NEW WALL
        # --------------------------------------------------

        new_wall = Wall.Create(
            doc,
            curve,
            wall_type,
            level_id,
            height,
            offset,
            False,
            False
        )

        # --------------------------------------------------
        # COPY COMMENTS / MARK
        # --------------------------------------------------

        for bip in [
            BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS,
            BuiltInParameter.ALL_MODEL_MARK
        ]:

            try:

                old_p = old_wall.get_Parameter(bip)
                new_p = new_wall.get_Parameter(bip)

                if old_p and new_p:

                    if not new_p.IsReadOnly:
                        new_p.Set(old_p.AsString())

            except:
                pass

        # --------------------------------------------------
        # APPLY RED OVERRIDE
        # --------------------------------------------------

        try:

            view.SetElementOverrides(
                new_wall.Id,
                ogs
            )

            highlighted += 1

        except:
            pass

        # --------------------------------------------------
        # DELETE OLD WALL
        # --------------------------------------------------

        doc.Delete(old_wall.Id)

        rebuilt += 1

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

        txt = w.GetDescriptionText().lower()

        if (
            "walls are attached to" in txt and
            "miss" in txt
        ):

            remaining += 1

    except:
        pass

print("")
print("===================================")
print("WALLS REBUILT : {}".format(rebuilt))
print("WALLS COLORED : {}".format(highlighted))
print("REMAINING     : {}".format(remaining))
print("ACTIVE VIEW   : {}".format(view.Name))
print("===================================")
print("")
print("DONE")