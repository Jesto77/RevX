# -*- coding: utf-8 -*-

from pyrevit import revit
from Autodesk.Revit.DB import *
import math

doc = revit.doc

# ==========================================================
# SETTINGS
# ==========================================================

VERY_SMALL = 0.000000001

# ==========================================================
# FAILURE PROCESSOR
# ==========================================================

class FailureProcessor(IFailuresPreprocessor):

    def PreprocessFailures(self, failuresAccessor):
        return FailureProcessingResult.Continue

# ==========================================================
# HELPERS
# ==========================================================

def force_axis(line):

    p1 = line.GetEndPoint(0)
    p2 = line.GetEndPoint(1)

    dx = p2.X - p1.X
    dy = p2.Y - p1.Y

    # perfectly vertical already
    if abs(dx) < VERY_SMALL:
        return None

    # perfectly horizontal already
    if abs(dy) < VERY_SMALL:
        return None

    # determine dominant direction
    if abs(dx) > abs(dy):

        # FORCE HORIZONTAL
        np2 = XYZ(
            p2.X,
            p1.Y,
            p2.Z
        )

        return Line.CreateBound(p1, np2)

    else:

        # FORCE VERTICAL
        np2 = XYZ(
            p1.X,
            p2.Y,
            p2.Z
        )

        return Line.CreateBound(p1, np2)

# ==========================================================
# FIND TARGET WARNINGS
# ==========================================================

warnings = []

for w in doc.GetWarnings():

    try:

        txt = w.GetDescriptionText().lower()

        if (
            "line in sketch" in txt and
            "slightly off axis" in txt
        ):

            warnings.append(w)

    except:
        pass

print("")
print("TARGET WARNINGS FOUND : {}".format(len(warnings)))
print("")

# ==========================================================
# COLLECT SKETCH IDS
# ==========================================================

sketch_ids = set()

for w in warnings:

    try:

        ids = w.GetFailingElements()

        for eid in ids:

            elem = doc.GetElement(eid)

            if not elem:
                continue

            try:

                sid = elem.SketchId

                if sid != ElementId.InvalidElementId:
                    sketch_ids.add(sid.IntegerValue)

            except:
                pass

    except:
        pass

# ==========================================================
# FIX
# ==========================================================

fixed = 0

for sid_int in sketch_ids:

    sid = ElementId(sid_int)

    sketch = doc.GetElement(sid)

    if not sketch:
        continue

    ses = None
    t = None

    try:

        ses = SketchEditScope(doc, "Fix Off Axis Sketch")

        ses.Start(sid)

        t = Transaction(doc, "Force Orthogonal")
        t.Start()

        for eid in sketch.GetAllElements():

            elem = doc.GetElement(eid)

            if not isinstance(elem, ModelCurve):
                continue

            curve = elem.GeometryCurve

            if not isinstance(curve, Line):
                continue

            new_line = force_axis(curve)

            if not new_line:
                continue

            elem.SetGeometryCurve(new_line, True)

            fixed += 1

        doc.Regenerate()

        t.Commit()

        ses.Commit(FailureProcessor())

    except Exception as ex:

        print("FAILED SKETCH : {}".format(sid.IntegerValue))
        print(str(ex))

        try:
            if t and t.HasStarted():
                t.RollBack()
        except:
            pass

        try:
            if ses:
                ses.Cancel()
        except:
            pass

# ==========================================================
# FINAL CHECK
# ==========================================================

remaining = 0

for w in doc.GetWarnings():

    try:

        txt = w.GetDescriptionText().lower()

        if (
            "line in sketch" in txt and
            "slightly off axis" in txt
        ):

            remaining += 1

    except:
        pass

print("")
print("===================================")
print("LINES FORCED TO AXIS : {}".format(fixed))
print("REMAINING WARNINGS   : {}".format(remaining))
print("===================================")
print("")
print("DONE")