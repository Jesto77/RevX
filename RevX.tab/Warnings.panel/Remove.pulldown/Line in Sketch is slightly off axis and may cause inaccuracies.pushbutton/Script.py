# -*- coding: utf-8 -*-

from pyrevit import revit
from Autodesk.Revit.DB import *
import math

doc = revit.doc

VERY_SMALL               = 0.000000001
SNAP_ANGLE_THRESHOLD_DEG = 0.5   # only fix lines within 0.5 degrees of axis


def get_id_value(element_id):
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


# ==========================================================
# FAILURE PROCESSOR
# ==========================================================

class FailureProcessor(IFailuresPreprocessor):
    def PreprocessFailures(self, failuresAccessor):
        needs_rollback = False
        for f in failuresAccessor.GetFailureMessages():
            try:
                severity = f.GetSeverity()
            except Exception:
                severity = None
            if severity == FailureSeverity.Warning:
                try:
                    failuresAccessor.DeleteWarning(f)
                except Exception:
                    pass
            else:
                needs_rollback = True
        if needs_rollback:
            return FailureProcessingResult.ProceedWithRollBack
        return FailureProcessingResult.Continue


# ==========================================================
# CLASSIFY LINE
# ==========================================================

def classify_line(du, dv):
    length = math.sqrt(du * du + dv * dv)
    if length < VERY_SMALL:
        return None, False
    angle_from_horiz = math.degrees(math.atan2(abs(dv), abs(du)))
    if angle_from_horiz < SNAP_ANGLE_THRESHOLD_DEG:
        return 'horizontal', True
    if angle_from_horiz > (90.0 - SNAP_ANGLE_THRESHOLD_DEG):
        return 'vertical', True
    return None, False


# ==========================================================
# SNAP A SINGLE LINE ABOUT ITS MIDPOINT
#
# Strategy: keep the midpoint fixed, extend both endpoints
# symmetrically along the snapped axis.
# This means BOTH endpoints move equally → minimum disruption
# to adjacent curves → loop stays closed (approximately).
# ==========================================================

def snap_line_about_midpoint(p0, p1, direction):
    """
    Returns (new_p0, new_p1) where the line is perfectly
    horizontal or vertical, centred on the original midpoint.

    direction = 'horizontal' → zero out Y difference (keep X span)
    direction = 'vertical'   → zero out X difference (keep Y span)
    """
    mx = (p0.X + p1.X) / 2.0
    my = (p0.Y + p1.Y) / 2.0
    mz = (p0.Z + p1.Z) / 2.0

    dx = p1.X - p0.X
    dy = p1.Y - p0.Y
    dz = p1.Z - p0.Z

    if direction == 'horizontal':
        # keep the X span, zero the Y deviation
        # half_length along X axis only
        half = abs(dx) / 2.0
        sign = 1.0 if dx >= 0 else -1.0
        new_p0 = XYZ(mx - sign * half, my, mz)
        new_p1 = XYZ(mx + sign * half, my, mz)
    else:
        # keep the Y span, zero the X deviation
        half = abs(dy) / 2.0
        sign = 1.0 if dy >= 0 else -1.0
        new_p0 = XYZ(mx, my - sign * half, mz - dz / 2.0)
        new_p1 = XYZ(mx, my + sign * half, mz + dz / 2.0)

    return new_p0, new_p1


# ==========================================================
# FIND TARGET WARNINGS
# ==========================================================

warnings_list = []
for w in doc.GetWarnings():
    try:
        txt = w.GetDescriptionText().lower()
        if "line in sketch" in txt and "slightly off axis" in txt:
            warnings_list.append(w)
    except Exception:
        pass

print("")
print("TARGET WARNINGS FOUND : {}".format(len(warnings_list)))
print("")

# ==========================================================
# BUILD CURVE-ID -> SKETCH-ID LOOKUP
# ==========================================================

curve_id_to_sketch_id = {}
all_sketches = (FilteredElementCollector(doc)
                .OfClass(Sketch)
                .ToElements())

for sk in all_sketches:
    try:
        for ceid in sk.GetAllElements():
            curve_id_to_sketch_id[get_id_value(ceid)] = sk.Id
    except Exception:
        pass

print("SKETCHES SCANNED : {}".format(len(all_sketches)))
print("CURVE IDS MAPPED : {}".format(len(curve_id_to_sketch_id)))
print("")

# ==========================================================
# COLLECT EXACTLY WHICH ELEMENTS ARE FLAGGED
# ==========================================================

warning_elem_ids = set()
sketch_ids       = set()
unmatched        = 0

for w in warnings_list:
    try:
        ids = w.GetFailingElements()
    except Exception:
        continue
    for eid in ids:
        iv   = get_id_value(eid)
        warning_elem_ids.add(iv)
        elem = doc.GetElement(eid)
        if iv in curve_id_to_sketch_id:
            sketch_ids.add(get_id_value(curve_id_to_sketch_id[iv]))
            continue
        if elem is not None and isinstance(elem, Sketch):
            sketch_ids.add(iv)
            continue
        try:
            sid = elem.SketchId
            if sid and sid != ElementId.InvalidElementId:
                sketch_ids.add(get_id_value(sid))
                continue
        except Exception:
            pass
        unmatched += 1

print("WARNING ELEMENT IDS : {}".format(sorted(warning_elem_ids)))
print("")
print("SKETCH IDS TO FIX                        : {}"
      .format(len(sketch_ids)))
print("FAILING ELEMENTS NOT MATCHED TO A SKETCH : {}"
      .format(unmatched))
print("")

# ==========================================================
# BUILD WORK LIST
# Only process the EXACT flagged elements.
# Use midpoint-rotation so the loop stays closed.
# ==========================================================

work_items = []   # list of (elem, new_curve, sid_int)
skipped    = []

for sid_int in sketch_ids:
    sid    = ElementId(sid_int)
    sketch = doc.GetElement(sid)

    if not sketch:
        print("SKETCH {} : not found - skipped".format(sid_int))
        skipped.append(sid_int)
        continue

    try:
        plane  = sketch.SketchPlane.GetPlane()
        origin = plane.Origin
        ux     = plane.XVec
        uy     = plane.YVec
        nz     = plane.Normal
    except Exception as ex:
        print("SKETCH {} : cannot read plane ({}) - skipped"
              .format(sid_int, ex))
        skipped.append(sid_int)
        continue

    try:
        all_elem_ids = sketch.GetAllElements()
    except Exception as ex:
        print("SKETCH {} : cannot read elements ({}) - skipped"
              .format(sid_int, ex))
        skipped.append(sid_int)
        continue

    sketch_items = []

    for eid in all_elem_ids:
        iv = get_id_value(eid)
        if iv not in warning_elem_ids:
            continue                          # only touch flagged lines

        elem = doc.GetElement(eid)
        if elem is None or not isinstance(elem, ModelCurve):
            continue
        curve = elem.GeometryCurve
        if curve is None or not isinstance(curve, Line):
            continue

        p0 = curve.GetEndPoint(0)
        p1 = curve.GetEndPoint(1)

        # project into sketch plane UV space
        def to_uv(p):
            rel = p - origin
            return rel.DotProduct(ux), rel.DotProduct(uy)

        u0, v0 = to_uv(p0)
        u1, v1 = to_uv(p1)
        du = u1 - u0
        dv = v1 - v0

        direction, should_snap = classify_line(du, dv)
        if not should_snap:
            print("  SKETCH {} elem {} : angle {:.4f}° - outside "
                  "threshold, skipped"
                  .format(sid_int, iv,
                          math.degrees(math.atan2(abs(dv), abs(du)))))
            continue

        new_p0, new_p1 = snap_line_about_midpoint(p0, p1, direction)

        # sanity: must not collapse
        if new_p0.DistanceTo(new_p1) < VERY_SMALL:
            print("  SKETCH {} elem {} : would collapse - skipped"
                  .format(sid_int, iv))
            continue

        try:
            new_curve = Line.CreateBound(new_p0, new_p1)
        except Exception as ex:
            print("  SKETCH {} elem {} : Line.CreateBound failed: {}"
                  .format(sid_int, iv, ex))
            continue

        sketch_items.append((elem, new_curve))
        print("  SKETCH {} elem {} : {} snap  "
              "dv={:.9f} du={:.9f}  max_shift={:.9f} ft"
              .format(sid_int, iv, direction,
                      dv, du,
                      max(p0.DistanceTo(new_p0),
                          p1.DistanceTo(new_p1))))

    if sketch_items:
        work_items.append((sid_int, sketch_items))

print("")
print("SKETCHES WITH WORK : {}".format(len(work_items)))
print("TOTAL CURVES TO FIX: {}".format(
    sum(len(si) for _, si in work_items)))
print("")

# ==========================================================
# WRITE  —  one Transaction per sketch, LocationCurve only
# ==========================================================

fixed       = 0
write_fails = 0

for sid_int, items in work_items:

    t = None
    try:
        t = Transaction(doc,
                        "Fix Off-Axis Lines {}".format(sid_int))
        t.Start()

        opts = t.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(FailureProcessor())
        opts.SetForcedModalHandling(False)
        t.SetFailureHandlingOptions(opts)

        write_ok  = True
        fix_count = 0

        for elem, new_curve in items:
            eid_str = get_id_value(elem.Id)
            try:
                loc = elem.Location
                if loc is None or not isinstance(loc, LocationCurve):
                    print("  SKETCH {} elem {} : no LocationCurve"
                          .format(sid_int, eid_str))
                    continue
                loc.Curve = new_curve
                fix_count += 1
            except Exception as ex:
                print("  SKETCH {} elem {} write error : {}"
                      .format(sid_int, eid_str, ex))
                write_ok = False
                break

        if write_ok:
            status = t.Commit()
            if status == TransactionStatus.Committed:
                fixed += fix_count
                print("SKETCH {} : {} curve(s) committed"
                      .format(sid_int, fix_count))
            else:
                print("SKETCH {} : status={} rolling back"
                      .format(sid_int, status))
                try:
                    t.RollBack()
                except Exception:
                    pass
                write_fails += 1
        else:
            t.RollBack()
            print("SKETCH {} : rolled back".format(sid_int))
            write_fails += 1

    except Exception as ex:
        print("SKETCH {} : unexpected error : {}"
              .format(sid_int, ex))
        write_fails += 1
        try:
            if t is not None and t.HasStarted() \
                    and not t.HasEnded():
                t.RollBack()
        except Exception:
            pass

# ==========================================================
# FINAL CHECK
# ==========================================================

remaining          = 0
remaining_warnings = []

for w in doc.GetWarnings():
    try:
        txt = w.GetDescriptionText().lower()
        if "line in sketch" in txt and "slightly off axis" in txt:
            remaining += 1
            remaining_warnings.append(w)
    except Exception:
        pass

print("")
print("==========================================")
print("CURVES FIXED       : {}".format(fixed))
print("SKETCHES SKIPPED   : {}".format(len(skipped)))
print("WRITE FAILURES     : {}".format(write_fails))
print("REMAINING WARNINGS : {}".format(remaining))
print("==========================================")
print("")

# ==========================================================
# DIAGNOSTICS
# ==========================================================

if remaining_warnings:

    remaining_sketch_ids = set()
    remaining_elem_ids   = set()

    for w in remaining_warnings:
        try:
            ids = w.GetFailingElements()
        except Exception:
            continue
        for eid in ids:
            iv   = get_id_value(eid)
            remaining_elem_ids.add(iv)
            elem = doc.GetElement(eid)
            if iv in curve_id_to_sketch_id:
                remaining_sketch_ids.add(
                    get_id_value(curve_id_to_sketch_id[iv]))
                continue
            if elem is not None and isinstance(elem, Sketch):
                remaining_sketch_ids.add(iv)
                continue
            try:
                sid = elem.SketchId
                if sid and sid != ElementId.InvalidElementId:
                    remaining_sketch_ids.add(get_id_value(sid))
            except Exception:
                pass

    print("REMAINING FLAGGED IDS : {}"
          .format(sorted(remaining_elem_ids)))
    print("")
    print("======== DIAGNOSTICS: STILL-FAILING SKETCHES ========")

    for sid_int in remaining_sketch_ids:
        sid    = ElementId(sid_int)
        sketch = doc.GetElement(sid)
        print("")
        print("SKETCH {} :".format(sid_int))
        if not sketch:
            print("  (not found)")
            continue
        try:
            plane  = sketch.SketchPlane.GetPlane()
            origin = plane.Origin
            ux     = plane.XVec
            uy     = plane.YVec
        except Exception as ex:
            print("  plane error: {}".format(ex))
            continue

        for eid in sketch.GetAllElements():
            try:
                elem  = doc.GetElement(eid)
                if not isinstance(elem, ModelCurve):
                    continue
                curve = elem.GeometryCurve
                if curve is None:
                    continue
                p0  = curve.GetEndPoint(0)
                p1  = curve.GetEndPoint(1)
                rel = p1 - p0
                du  = rel.DotProduct(ux)
                dv  = rel.DotProduct(uy)
                try:
                    length = curve.Length
                except Exception:
                    length = -1
                try:
                    angle_deg = math.degrees(math.atan2(dv, du))
                except Exception:
                    angle_deg = None
                flagged = (
                    " <<<< STILL FLAGGED"
                    if get_id_value(elem.Id) in remaining_elem_ids
                    else "")
                kind = ("Line" if isinstance(curve, Line)
                        else type(curve).__name__)
                print("  {} {} : length={:.6f}  "
                      "du={:.9f}  dv={:.9f}  angle={}{}"
                      .format(kind, get_id_value(elem.Id),
                              length, du, dv,
                              angle_deg, flagged))
            except Exception:
                pass

    print("")
    print("=====================================================")

print("")
print("DONE")