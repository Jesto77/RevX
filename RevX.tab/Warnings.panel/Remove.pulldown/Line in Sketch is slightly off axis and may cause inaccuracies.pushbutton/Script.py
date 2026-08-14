# -*- coding: utf-8 -*-

from pyrevit import revit
from Autodesk.Revit.DB import *
import math

doc = revit.doc

# ==========================================================
# SETTINGS
# ==========================================================

VERY_SMALL = 0.000000001


def get_id_value(element_id):
    # Revit 2024+ renamed ElementId.IntegerValue to ElementId.Value.
    # Try the new property first, fall back to the old one so this
    # runs on either API version.
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue

# ==========================================================
# FAILURE PROCESSOR
# ==========================================================

class FailureProcessor(IFailuresPreprocessor):

    def PreprocessFailures(self, failuresAccessor):
        return FailureProcessingResult.Continue

# ==========================================================
# HELPERS
# ==========================================================

def vkey(pt):
    # identifies a vertex by rounded coordinates so shared sketch
    # endpoints (which should be exactly coincident) match up
    return (round(pt.X, 6), round(pt.Y, 6), round(pt.Z, 6))


def fix_sketch_lines(sketch):
    """Force nearly-horizontal/vertical lines in this sketch onto
    the true axis WITHOUT breaking the loop.

    Two things this has to get right:

    1. Sketch lines share endpoints with their neighbors, so moving
       one line's endpoint without moving the connected neighbor's
       matching endpoint opens a gap in the loop (this is why the
       edit scope commit used to fail). We build a small vertex
       graph for the whole sketch first, so a moved endpoint drags
       its connected neighbor(s) along with it.

    2. "Horizontal/vertical" only means "constant global X or Y" for
       a sketch that lies flat on the ground plane. For a sketch on
       any other plane (a roof, a sloped/vertical family profile, a
       wall sweep, etc.) the relevant axes are the SKETCH PLANE's
       own local U/V directions, not global X/Y - so all the axis
       math below is done in the plane's local (u, v) coordinates,
       then converted back to global XYZ.
    """

    try:
        plane  = sketch.SketchPlane.GetPlane()
        origin = plane.Origin
        ux     = plane.XVec
        uy     = plane.YVec
        nz     = plane.Normal
    except Exception:
        print("SKETCH PLANE UNAVAILABLE for sketch {} - skipped"
              .format(get_id_value(sketch.Id)))
        return 0

    def to_uvw(p):
        rel = p - origin
        return (rel.DotProduct(ux), rel.DotProduct(uy),
                rel.DotProduct(nz))

    def from_uvw(u, v, w):
        return (origin
                .Add(ux.Multiply(u))
                .Add(uy.Multiply(v))
                .Add(nz.Multiply(w)))

    curve_infos   = []
    endpoint_key  = {}
    vertices      = {}

    for eid in sketch.GetAllElements():

        elem = doc.GetElement(eid)

        if not isinstance(elem, ModelCurve):
            continue

        curve = elem.GeometryCurve

        if curve is None:
            continue

        is_line = isinstance(curve, Line)
        p0 = curve.GetEndPoint(0)
        p1 = curve.GetEndPoint(1)

        # for arcs, remember a point on the original curve so we can
        # rebuild it through the same shape if an endpoint has to move
        mid_pt = None
        if not is_line:
            try:
                mid_pt = curve.Evaluate(0.5, True)
            except Exception:
                mid_pt = None

        idx = len(curve_infos)
        curve_infos.append({
            'elem': elem,
            'is_line': is_line,
            'orig_p0': p0,
            'orig_p1': p1,
            'mid_pt': mid_pt,
        })

        for end_idx, p in ((0, p0), (1, p1)):
            k = vkey(p)
            endpoint_key[(idx, end_idx)] = k
            if k not in vertices:
                vertices[k] = {
                    'uvw': to_uvw(p),
                    'line_refs': 0,
                    'has_arc': False,
                }
            if is_line:
                vertices[k]['line_refs'] += 1
            else:
                vertices[k]['has_arc'] = True

    # a few relaxation passes so chains of several near-axis
    # segments in a row converge to consistent shared vertices
    for _pass in range(3):

        for idx, info in enumerate(curve_infos):

            if not info['is_line']:
                continue

            k0 = endpoint_key[(idx, 0)]
            k1 = endpoint_key[(idx, 1)]
            v0 = vertices[k0]
            v1 = vertices[k1]
            u0, vv0, w0 = v0['uvw']
            u1, vv1, w1 = v1['uvw']

            du = u1 - u0
            dv = vv1 - vv0

            if abs(du) < VERY_SMALL or abs(dv) < VERY_SMALL:
                continue  # already axis-aligned (or degenerate)

            horizontal = abs(du) > abs(dv)  # dominant along local u

            # Prefer moving whichever end does NOT touch an arc.
            # If both (or neither) touch an arc, fall back to
            # moving the less-connected vertex. Arc-touching
            # vertices are still movable now - the arc gets rebuilt
            # through its original midpoint afterward so its shape
            # is preserved for the tiny nudge this requires.
            prefer1 = (not v1['has_arc']) and v0['has_arc']
            prefer0 = (not v0['has_arc']) and v1['has_arc']

            if prefer1:
                move1 = True
            elif prefer0:
                move1 = False
            else:
                move1 = v1['line_refs'] <= v0['line_refs']

            if move1:
                if horizontal:
                    v1['uvw'] = (u1, vv0, w1)
                else:
                    v1['uvw'] = (u0, vv1, w1)
            else:
                if horizontal:
                    v0['uvw'] = (u0, vv1, w0)
                else:
                    v0['uvw'] = (u1, vv0, w0)

    # apply the resolved vertex positions back onto the actual
    # curves - lines get re-created straight between the new
    # endpoints; arcs get rebuilt through their original midpoint
    # so a moved endpoint just nudges the arc rather than reshaping it
    changed = 0

    for idx, info in enumerate(curve_infos):

        k0 = endpoint_key[(idx, 0)]
        k1 = endpoint_key[(idx, 1)]
        new_p0 = from_uvw(*vertices[k0]['uvw'])
        new_p1 = from_uvw(*vertices[k1]['uvw'])
        old_p0 = info['orig_p0']
        old_p1 = info['orig_p1']

        moved = (
            old_p0.DistanceTo(new_p0) > VERY_SMALL or
            old_p1.DistanceTo(new_p1) > VERY_SMALL)

        if not moved:
            continue

        try:
            if info['is_line']:
                new_curve = Line.CreateBound(new_p0, new_p1)
            else:
                if info['mid_pt'] is None:
                    continue
                new_curve = Arc.CreateBound(
                    new_p0, info['mid_pt'], new_p1)
        except Exception:
            continue

        info['elem'].SetGeometryCurve(new_curve, True)
        changed += 1

    return changed

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
# BUILD A CURVE-ID -> SKETCH-ID LOOKUP
# ==========================================================
# NOTE: the elements returned by w.GetFailingElements() for this
# warning are NOT guaranteed to expose a ".SketchId" property (that
# was the bug - it doesn't exist on the returned objects, so the old
# "elem.SketchId" lookup silently failed every time and sketch_ids
# stayed empty). Instead we scan every Sketch in the model once and
# map each curve id it owns back to the sketch, which is robust
# regardless of what GetFailingElements() actually hands back.

curve_id_to_sketch_id = {}

for sk in FilteredElementCollector(doc).OfClass(Sketch):
    try:
        for ceid in sk.GetAllElements():
            curve_id_to_sketch_id[get_id_value(ceid)] = sk.Id
    except:
        pass

print("SKETCHES SCANNED : {}".format(
    len(FilteredElementCollector(doc).OfClass(Sketch).ToElementIds())))
print("CURVE IDS MAPPED TO SKETCHES : {}".format(
    len(curve_id_to_sketch_id)))
print("")

# ==========================================================
# COLLECT SKETCH IDS FROM THE WARNINGS
# ==========================================================

sketch_ids = set()
unmatched  = 0

for w in warnings:

    try:
        ids = w.GetFailingElements()
    except:
        continue

    for eid in ids:

        iv = get_id_value(eid)

        # Case 1: failing element IS a curve that belongs to a sketch
        if iv in curve_id_to_sketch_id:
            sketch_ids.add(get_id_value(curve_id_to_sketch_id[iv]))
            continue

        # Case 2: failing element IS the Sketch itself
        elem = doc.GetElement(eid)

        if elem is not None and isinstance(elem, Sketch):
            sketch_ids.add(iv)
            continue

        # Case 3: fallback - some element types really do expose
        # SketchId directly, so still try it, just don't rely on it
        try:
            sid = elem.SketchId
            if sid and sid != ElementId.InvalidElementId:
                sketch_ids.add(get_id_value(sid))
                continue
        except:
            pass

        unmatched += 1

print("SKETCH IDS TO FIX : {}".format(len(sketch_ids)))
print("FAILING ELEMENTS NOT MATCHED TO A SKETCH : {}".format(unmatched))
print("")

# ==========================================================
# FIX
# ==========================================================

fixed = 0

for sid_int in sketch_ids:

    sid = ElementId(sid_int)

    sketch = doc.GetElement(sid)

    if not sketch:
        continue

    try:
        # Using "with" here matters: SketchEditScope/Transaction are
        # IDisposable, and Dispose() reliably releases the edit
        # scope even if something inside throws. The old manual
        # ses.Cancel() in an except block was NOT reliably releasing
        # it after a failed commit, which is why every sketch after
        # the first failure got "another edit mode active".
        with SketchEditScope(doc, "Fix Off Axis Sketch") as ses:

            ses.Start(sid)

            with Transaction(doc, "Force Orthogonal") as t:
                t.Start()
                sketch_changed = fix_sketch_lines(sketch)
                doc.Regenerate()
                t.Commit()

            ses.Commit(FailureProcessor())

        fixed += sketch_changed

    except Exception as ex:

        print("FAILED SKETCH : {}".format(get_id_value(sid)))
        print(str(ex))

# ==========================================================
# FINAL CHECK
# ==========================================================

remaining = 0
remaining_warnings = []

for w in doc.GetWarnings():

    try:

        txt = w.GetDescriptionText().lower()

        if (
            "line in sketch" in txt and
            "slightly off axis" in txt
        ):

            remaining += 1
            remaining_warnings.append(w)

    except:
        pass

print("")
print("===================================")
print("LINES FORCED TO AXIS : {}".format(fixed))
print("REMAINING WARNINGS   : {}".format(remaining))
print("===================================")
print("")

# ==========================================================
# DIAGNOSTICS FOR ANY STILL-FAILING SKETCHES
# ==========================================================
# For anything still flagged after the fix pass, dump exactly what
# each of its lines looks like in the sketch plane's local (u, v)
# coordinates, so we can see actual numbers instead of guessing.

if remaining_warnings:

    remaining_sketch_ids = set()

    for w in remaining_warnings:
        try:
            ids = w.GetFailingElements()
        except:
            continue
        for eid in ids:
            iv = get_id_value(eid)
            if iv in curve_id_to_sketch_id:
                remaining_sketch_ids.add(
                    get_id_value(curve_id_to_sketch_id[iv]))
                continue
            elem = doc.GetElement(eid)
            if elem is not None and isinstance(elem, Sketch):
                remaining_sketch_ids.add(iv)
                continue
            try:
                sid = elem.SketchId
                if sid and sid != ElementId.InvalidElementId:
                    remaining_sketch_ids.add(get_id_value(sid))
            except:
                pass

    print("=========== DIAGNOSTICS: STILL-FAILING SKETCHES ===========")

    for sid_int in remaining_sketch_ids:

        sid = ElementId(sid_int)
        sketch = doc.GetElement(sid)

        print("")
        print("SKETCH {} :".format(sid_int))

        if not sketch:
            print("  (sketch element not found)")
            continue

        try:
            plane  = sketch.SketchPlane.GetPlane()
            origin = plane.Origin
            ux     = plane.XVec
            uy     = plane.YVec
        except Exception as ex:
            print("  could not read sketch plane: {}".format(ex))
            continue

        print("  plane origin=({:.4f},{:.4f},{:.4f})  "
              "ux=({:.4f},{:.4f},{:.4f})  uy=({:.4f},{:.4f},{:.4f})"
              .format(origin.X, origin.Y, origin.Z,
                      ux.X, ux.Y, ux.Z, uy.X, uy.Y, uy.Z))

        for eid in sketch.GetAllElements():

            elem = doc.GetElement(eid)

            if not isinstance(elem, ModelCurve):
                continue

            curve = elem.GeometryCurve

            if curve is None:
                continue

            p0 = curve.GetEndPoint(0)
            p1 = curve.GetEndPoint(1)
            rel = p1 - p0
            du = rel.DotProduct(ux)
            dv = rel.DotProduct(uy)

            try:
                length = curve.Length
            except Exception:
                length = -1

            try:
                angle_deg = math.degrees(math.atan2(dv, du))
            except Exception:
                angle_deg = None

            kind = "Line" if isinstance(curve, Line) else \
                type(curve).__name__

            print("  {} {} : length={:.6f}  du={:.9f}  dv={:.9f}  "
                  "angle_deg={}"
                  .format(kind, get_id_value(elem.Id), length,
                          du, dv, angle_deg))

    print("")
    print("=============================================================")

print("")
print("DONE")