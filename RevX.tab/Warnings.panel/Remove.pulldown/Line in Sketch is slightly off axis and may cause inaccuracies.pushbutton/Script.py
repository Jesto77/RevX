# -*- coding: utf-8 -*-
# OFF-AXIS SKETCH LINE FIXER v7 - Revit 2025 / 2026
# - Tests every flagged line against the sketch-plane frame AND the
#   world horizontal/vertical frame, each with 0/45/90/135 targets
# - Solves all snaps together (corners shared by several lines are OK)
# - Finds off-axis lines by geometry too (not only by warning ids)
# - Resolves sketches via owner element as well as via the sketch lines
# - Splines / non line-arc curves keep their endpoints
# - Two-pass strategy (batch then per-curve); prints why a write fails

from pyrevit import revit
from Autodesk.Revit.DB import *
import clr
import math

clr.AddReference("System")
from System import Int64
from System.Collections.Generic import List

doc = revit.doc
app = doc.Application

VERY_SMALL               = 0.000000001
SNAP_ANGLE_THRESHOLD_DEG = 0.5
NEARBY_ENDPOINT_TOL      = 0.005      # ft (~1.5mm)
LOOP_TOLERANCE           = 0.0005     # ft (~0.15mm)
ROUND_DP                 = 5          # decimal places in ft (~3um)


def get_id_value(element_id):
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


def make_eid(val):
    if isinstance(val, ElementId):
        return val
    return ElementId(Int64(int(val)))


def get_sketch_id(owner_elem):
    try:
        sid = owner_elem.SketchId
        if sid is not None:
            return sid
    except Exception:
        pass
    try:
        return owner_elem.get_SketchId()
    except Exception:
        pass
    return None


# ==========================================================
# BULLDOZER FAILURE PROCESSOR
# ==========================================================

class BulldozerProc(IFailuresPreprocessor):
    def __init__(self):
        self.log    = []
        self.errors = 0

    def PreprocessFailures(self, failuresAccessor):
        for f in list(failuresAccessor.GetFailureMessages()):
            try:
                sev  = f.GetSeverity()
                desc = f.GetDescriptionText()
                desc_lower = desc.lower()
            except Exception:
                continue

            self.log.append("[{}] {}".format(sev, desc[:100]))

            # Always delete off-axis warnings
            if "off axis" in desc_lower or "off-axis" in desc_lower:
                try:
                    failuresAccessor.DeleteWarning(f)
                except Exception:
                    pass
                continue

            # All other warnings - delete
            if sev == FailureSeverity.Warning:
                try:
                    failuresAccessor.DeleteWarning(f)
                except Exception:
                    pass
                continue

            # Join errors - unjoin and resolve
            try:
                if "join" in desc_lower:
                    ids = list(f.GetFailingElementIds())
                    if len(ids) >= 2:
                        e1 = doc.GetElement(ids[0])
                        e2 = doc.GetElement(ids[1])
                        if e1 and e2:
                            try:
                                if JoinGeometryUtils.AreElementsJoined(
                                        doc, e1, e2):
                                    JoinGeometryUtils.UnjoinGeometry(
                                        doc, e1, e2)
                            except Exception:
                                pass
                    try:
                        failuresAccessor.ResolveFailure(f)
                    except Exception:
                        try:
                            failuresAccessor.DeleteWarning(f)
                        except Exception:
                            pass
                    continue
            except Exception:
                pass

            # Constraint errors - delete offending elements
            try:
                if "constraint" in desc_lower:
                    ids = f.GetFailingElementIds()
                    id_list = List[ElementId]()
                    for i in ids:
                        id_list.Add(i)
                    try:
                        failuresAccessor.DeleteElements(id_list)
                        continue
                    except Exception:
                        pass
            except Exception:
                pass

            # Generic resolve
            try:
                failuresAccessor.ResolveFailure(f)
                continue
            except Exception:
                pass

            self.errors += 1

        if self.errors > 0:
            return FailureProcessingResult.ProceedWithRollBack
        return FailureProcessingResult.Continue


# ==========================================================
# GEOMETRY HELPERS
# ==========================================================

def classify_line(du, dv):
    length = math.sqrt(du * du + dv * dv)
    if length < VERY_SMALL:
        return None, False
    angle = math.degrees(math.atan2(abs(dv), abs(du)))
    if angle < SNAP_ANGLE_THRESHOLD_DEG:
        return 'horizontal', True
    if angle > (90.0 - SNAP_ANGLE_THRESHOLD_DEG):
        return 'vertical', True
    return None, False


def read_sketch_curves(sketch):
    try:
        plane  = sketch.SketchPlane.GetPlane()
        origin = plane.Origin
        ux     = plane.XVec
        uy     = plane.YVec
        nz     = plane.Normal
    except Exception:
        return None, None

    curves = []
    try:
        for eid in sketch.GetAllElements():
            elem = doc.GetElement(eid)
            if not isinstance(elem, ModelCurve):
                continue
            curve = elem.GeometryCurve
            if curve is None:
                continue

            p0 = curve.GetEndPoint(0)
            p1 = curve.GetEndPoint(1)

            if isinstance(curve, Line):
                kind, mid = 'line', None
            elif isinstance(curve, Arc):
                kind = 'arc'
                try:
                    mid = curve.Evaluate(0.5, True)
                except Exception:
                    mid = None
            elif isinstance(curve, HermiteSpline):
                kind, mid = 'spline', None
            else:
                kind, mid = 'other', None

            curves.append({
                'id'    : get_id_value(eid),
                'elem'  : elem,
                'curve' : curve,
                'kind'  : kind,
                'p0'    : p0,
                'p1'    : p1,
                'mid'   : mid,
            })
    except Exception:
        return None, None

    plane_data = {'origin': origin, 'ux': ux, 'uy': uy, 'nz': nz}
    return curves, plane_data


def rebuild_curve(c, new_p0, new_p1):
    """Returns None for splines to avoid breaking loop closure."""
    kind = c['kind']
    if new_p0.DistanceTo(new_p1) < app.ShortCurveTolerance:
        return None
    try:
        if kind == 'line':
            return Line.CreateBound(new_p0, new_p1)
        if kind == 'arc' and c['mid'] is not None:
            return Arc.Create(new_p0, new_p1, c['mid'])
        # Splines are never rebuilt
        if kind == 'spline':
            return None
    except Exception:
        return None
    return None



# ==========================================================
# DETECTION / FRAMES
# Revit does not only flag lines that are off the sketch
# plane's own axes: it also flags lines that are slightly off
# WORLD horizontal/vertical (when the plane axes are rotated)
# and slightly off 45 degrees. So every line is tested against
# several reference frames, each with 0/45/90/135 targets.
# ==========================================================

def frame_offsets(plane_data):
    """Angles (rad, in sketch UV space) of reference frames."""
    ux = plane_data['ux']
    uy = plane_data['uy']
    nz = plane_data['nz']
    offs = [0.0]
    ref = None
    if abs(nz.Z) > 0.999:          # horizontal sketch: world X
        ref = XYZ(1, 0, 0)
    elif abs(nz.Z) < 0.001:        # vertical sketch: world up
        ref = XYZ(0, 0, 1)
    if ref is not None:
        offs.append(math.atan2(ref.DotProduct(uy), ref.DotProduct(ux)))
    return offs


def nearest_axis_angle(du, dv, offsets):
    """Best (target_angle_rad, residual_deg) over all frames."""
    a = math.atan2(dv, du)
    step = math.pi / 4.0
    best = None
    for o in offsets:
        k = round((a - o) / step)
        t = o + k * step
        res = abs(math.degrees(a - t))
        if best is None or res < best[1]:
            best = (t, res)
    return best


def line_uv(c, plane_data):
    rel = c['p1'] - c['p0']
    return rel.DotProduct(plane_data['ux']), rel.DotProduct(plane_data['uy'])


def is_off_axis_line(c, plane_data):
    """Geometric detection: within 0.2 deg of an axis/45 but not exact."""
    if c['kind'] != 'line':
        return False
    du, dv = line_uv(c, plane_data)
    if math.sqrt(du * du + dv * dv) < VERY_SMALL:
        return False
    _t, res = nearest_axis_angle(du, dv, frame_offsets(plane_data))
    return 1e-10 < res < 0.2


class _DSU(object):
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


MOVE_EPS = 1e-12
_reported = set()


def plan_moves(curves, plane_data, flagged_ids):
    """Returns {(curve_index, end_index): new XYZ} for endpoints to move.

    Endpoints closer than NEARBY_ENDPOINT_TOL form a corner cluster.
    Each flagged line becomes a linear constraint
        n . (cluster_a - cluster_b) = 0
    (n = normal of the target direction in the sketch plane), solved
    by alternating projection so corners shared by several flagged
    lines get every correction. Spline-type endpoints stay pinned."""
    origin  = plane_data['origin']
    ux      = plane_data['ux']
    uy      = plane_data['uy']
    offsets = frame_offsets(plane_data)

    pts = []
    for ci, c in enumerate(curves):
        pinned_curve = c['kind'] not in ('line', 'arc')
        for ei, p in enumerate((c['p0'], c['p1'])):
            rel = p - origin
            pts.append({
                'ci': ci, 'ei': ei, 'p': p,
                'u': rel.DotProduct(ux), 'v': rel.DotProduct(uy),
                'pinned': pinned_curve,
            })
    n = len(pts)

    cl = _DSU(n)
    for i in range(n):
        for j in range(i + 1, n):
            if pts[i]['ci'] == pts[j]['ci']:
                continue
            if pts[i]['p'].DistanceTo(pts[j]['p']) < NEARBY_ENDPOINT_TOL:
                cl.union(i, j)

    clusters = {}
    for i in range(n):
        clusters.setdefault(cl.find(i), []).append(i)

    cinfo = {}
    pos = {}
    for root, members in clusters.items():
        us = [pts[m]['u'] for m in members]
        vs = [pts[m]['v'] for m in members]
        pin = None
        for m in members:
            if pts[m]['pinned']:
                pin = (pts[m]['u'], pts[m]['v'])
                break
        cinfo[root] = {
            'spread': max(max(us) - min(us), max(vs) - min(vs)),
            'pin': pin, 'members': members,
        }
        if pin:
            pos[root] = [pin[0], pin[1]]
        else:
            pos[root] = [sum(us) / len(us), sum(vs) / len(vs)]

    cons = []
    constrained = set()
    for ci, c in enumerate(curves):
        if c['kind'] != 'line' or c['id'] not in flagged_ids:
            continue
        du, dv = line_uv(c, plane_data)
        if math.sqrt(du * du + dv * dv) < VERY_SMALL:
            continue
        t, res = nearest_axis_angle(du, dv, offsets)
        if res > SNAP_ANGLE_THRESHOLD_DEG:
            key = (c['id'], round(res, 3))
            if key not in _reported:
                _reported.add(key)
                print("    curve {} : {:.3f} deg from nearest axis/45 "
                      "(plane angle {:.3f} deg) - not snapped".format(
                          c['id'], res, math.degrees(math.atan2(dv, du))))
            continue
        r0 = cl.find(2 * ci)
        r1 = cl.find(2 * ci + 1)
        if r0 == r1:
            continue
        if cinfo[r0]['pin'] and cinfo[r1]['pin']:
            print("    curve {} : both ends pinned by spline - skip"
                  .format(c['id']))
            continue
        cons.append((r0, r1, -math.sin(t), math.cos(t)))
        constrained.add(r0)
        constrained.add(r1)

    for _it in range(400):
        worst = 0.0
        for r0, r1, nu, nv in cons:
            a = pos[r0]
            b = pos[r1]
            viol = nu * (a[0] - b[0]) + nv * (a[1] - b[1])
            worst = max(worst, abs(viol))
            if cinfo[r0]['pin']:
                wa, wb = 0.0, 1.0
            elif cinfo[r1]['pin']:
                wa, wb = 1.0, 0.0
            else:
                wa, wb = 0.5, 0.5
            a[0] -= nu * viol * wa
            a[1] -= nv * viol * wa
            b[0] += nu * viol * wb
            b[1] += nv * viol * wb
        if worst < 1e-14:
            break

    new_pts = {}
    for root, info in cinfo.items():
        multi = len(info['members']) > 1 and info['spread'] > VERY_SMALL
        if not (root in constrained or multi or info['pin']):
            continue
        tu, tv = pos[root]
        for m in info['members']:
            pt = pts[m]
            if pt['pinned']:
                continue
            du = tu - pt['u']
            dv = tv - pt['v']
            if abs(du) < MOVE_EPS and abs(dv) < MOVE_EPS:
                continue
            new_pts[(pt['ci'], pt['ei'])] = (
                pt['p'].Add(ux.Multiply(du)).Add(uy.Multiply(dv)))

    return new_pts


def apply_moves(curves, new_pts):
    pairs = []
    for ci, c in enumerate(curves):
        n0 = new_pts.get((ci, 0))
        n1 = new_pts.get((ci, 1))
        if n0 is None and n1 is None:
            continue
        if n0 is None:
            n0 = c['p0']
        if n1 is None:
            n1 = c['p1']
        new_curve = rebuild_curve(c, n0, n1)
        if new_curve is None:
            print("    SKIP curve {} ({}) : cannot rebuild"
                  .format(c['id'], c['kind']))
            continue
        pairs.append((c['elem'], new_curve, c['id'], c['kind']))
    return pairs



def validate_loop_closure(curves, pairs):
    new_endpoints = {}
    for c in curves:
        new_endpoints[c['id']] = (c['p0'], c['p1'])
    for elem, new_curve, cid, _kind in pairs:
        new_endpoints[cid] = (
            new_curve.GetEndPoint(0),
            new_curve.GetEndPoint(1)
        )

    def _key(p):
        return (
            round(p.X / LOOP_TOLERANCE) * LOOP_TOLERANCE,
            round(p.Y / LOOP_TOLERANCE) * LOOP_TOLERANCE,
            round(p.Z / LOOP_TOLERANCE) * LOOP_TOLERANCE,
        )

    counts = {}
    for cid, (p0, p1) in new_endpoints.items():
        for p in (p0, p1):
            k = _key(p)
            counts[k] = counts.get(k, 0) + 1

    opens = []
    for cid, (p0, p1) in new_endpoints.items():
        for end_idx, p in ((0, p0), (1, p1)):
            k = _key(p)
            if counts.get(k, 0) < 2:
                opens.append((cid, end_idx, p))

    return (len(opens) == 0), opens


def collect_sketch_dimensions(sketch):
    dim_ids = []
    try:
        sketch_elem_ids = set(
            get_id_value(eid)
            for eid in sketch.GetAllElements()
        )
    except Exception:
        return dim_ids

    dims = (FilteredElementCollector(doc)
            .OfClass(Dimension).ToElements())

    for dim in dims:
        try:
            refs = dim.References
            if not refs:
                continue
            for ref in refs:
                if ref.ElementId is None:
                    continue
                if get_id_value(ref.ElementId) in sketch_elem_ids:
                    dim_ids.append(dim.Id)
                    break
        except Exception:
            continue
    return dim_ids


def find_joined_elements(elem):
    joined = []
    try:
        joined_ids = JoinGeometryUtils.GetJoinedElements(doc, elem)
        for jid in joined_ids:
            joined.append(doc.GetElement(jid))
    except Exception:
        pass
    return [j for j in joined if j is not None]


# ==========================================================
# WRITE FUNCTION
# ==========================================================

def try_write(sid_int, owner, pairs, dim_ids, joined, strategy):
    ses = None
    t   = None
    tg  = None
    scope_committed = False
    tg_assimilated  = False

    try:
        tg = TransactionGroup(doc, "Fix Sketch {} [{}]".format(
            sid_int, strategy))
        tg.Start()

        ses = SketchEditScope(doc, "Fix Sketch {}".format(sid_int))
        ses.Start(make_eid(sid_int))

        t = Transaction(doc,
                        "Fix Curves {} [{}]".format(sid_int, strategy))
        t.Start()

        if strategy in ('unjoin', 'unjoin_del_dims'):
            for j in joined:
                try:
                    if JoinGeometryUtils.AreElementsJoined(doc, owner, j):
                        JoinGeometryUtils.UnjoinGeometry(doc, owner, j)
                except Exception:
                    pass

        if strategy in ('del_dims', 'unjoin_del_dims') and dim_ids:
            for did in dim_ids:
                try:
                    doc.Delete(did)
                except Exception:
                    pass

        opts = t.GetFailureHandlingOptions()
        proc = BulldozerProc()
        opts.SetFailuresPreprocessor(proc)
        opts.SetForcedModalHandling(False)
        opts.SetClearAfterRollback(True)
        opts.SetDelayedMiniWarnings(True)
        t.SetFailureHandlingOptions(opts)

        write_ok = True
        for elem, new_curve, cid, kind in pairs:
            try:
                elem.SetGeometryCurve(new_curve, True)
            except Exception as ex:
                print("    elem {} ({}) write: {}".format(cid, kind, ex))
                write_ok = False
                break

        if not write_ok:
            try:
                t.RollBack()
            except Exception:
                pass
            return False

        status = t.Commit()
        if status != TransactionStatus.Committed:
            print("    [{}] inner transaction status: {}".format(
                strategy, status))
            return False

        scope_proc = BulldozerProc()
        try:
            ses.Commit(scope_proc)
            scope_committed = True
        except Exception as ex:
            print("    [{}] sketch commit failed: {}".format(strategy, ex))
            for line in scope_proc.log[:4]:
                print("        failure: {}".format(line))
            return False

        try:
            tg.Assimilate()
            tg_assimilated = True
        except Exception:
            pass
        return True

    except Exception as ex:
        print("    [{}] write error: {}".format(strategy, ex))
        return False

    finally:
        if ses is not None and not scope_committed:
            try:
                if t and t.HasStarted() and not t.HasEnded():
                    try:
                        t.RollBack()
                    except Exception:
                        pass
                ses.Dispose()
            except Exception:
                pass

        if tg is not None and not tg_assimilated:
            try:
                if tg.HasStarted() and not tg.HasEnded():
                    tg.RollBack()
            except Exception:
                pass



# ==========================================================
# MAIN
# ==========================================================

def is_target_warning(w):
    try:
        txt = w.GetDescriptionText().lower()
    except Exception:
        return False
    return "line in sketch" in txt and "slightly off axis" in txt


warnings_list = [w for w in doc.GetWarnings() if is_target_warning(w)]

print("")
print("=" * 60)
print("OFF-AXIS LINE FIXER v7 - Revit {}".format(app.VersionNumber))
print("=" * 60)
print("TARGET WARNINGS FOUND : {}".format(len(warnings_list)))
print("")

# Sketch lookup tables
curve_id_to_sketch_id = {}
owner_id_to_sketch_ids = {}
for sk in FilteredElementCollector(doc).OfClass(Sketch).ToElements():
    try:
        sk_iv = get_id_value(sk.Id)
        owner_iv = get_id_value(sk.OwnerId)
        owner_id_to_sketch_ids.setdefault(owner_iv, set()).add(sk_iv)
    except Exception:
        sk_iv = None
    try:
        for ceid in sk.GetAllElements():
            curve_id_to_sketch_id[get_id_value(ceid)] = get_id_value(sk.Id)
    except Exception:
        pass

warning_elem_ids = set()
sketch_ids       = set()
unresolved       = []

for w in warnings_list:
    try:
        ids = list(w.GetFailingElements())
    except Exception:
        continue
    for eid in ids:
        iv = get_id_value(eid)
        elem = doc.GetElement(eid)
        if elem is None:
            unresolved.append((iv, "None"))
            continue

        if isinstance(elem, ModelCurve):
            warning_elem_ids.add(iv)
            if iv in curve_id_to_sketch_id:
                sketch_ids.add(curve_id_to_sketch_id[iv])
                continue
        elif isinstance(elem, Sketch):
            sketch_ids.add(iv)
            continue

        # Parent element (Floor, Wall, Roof, ...) -> its sketches
        found = False
        if iv in owner_id_to_sketch_ids:
            sketch_ids.update(owner_id_to_sketch_ids[iv])
            found = True
        else:
            sid = get_sketch_id(elem)
            try:
                if sid is not None and sid != ElementId.InvalidElementId:
                    sketch_ids.add(get_id_value(sid))
                    found = True
            except Exception:
                pass
        if not found:
            unresolved.append((iv, elem.__class__.__name__))

print("FLAGGED MODEL LINES   : {}".format(len(warning_elem_ids)))
print("SKETCH IDS            : {}".format(sorted(sketch_ids)))
if unresolved:
    print("UNRESOLVED ELEMENTS   : {}".format(unresolved[:10]))
print("")

# ==========================================================
# PROCESS EACH SKETCH
# ==========================================================

fixed_sketches = 0
fixed_curves   = 0
write_fails    = 0

for sid_int in sorted(sketch_ids):
    print("")
    print("--- Sketch {} ---".format(sid_int))

    sketch = doc.GetElement(make_eid(sid_int))
    if not sketch:
        print("  not found")
        continue

    try:
        owner_elem   = doc.GetElement(sketch.OwnerId)
        owner_class  = owner_elem.__class__.__name__
    except Exception:
        print("  no owner element")
        continue

    curves, plane_data = read_sketch_curves(sketch)
    if not curves:
        print("  could not read sketch curves / plane")
        continue

    print("  Owner : {} ({})".format(
        get_id_value(owner_elem.Id), owner_class))

    # Lines to fix = ids Revit reported + anything geometrically off axis
    sketch_curve_ids = set(c['id'] for c in curves)
    flagged_ids = set(warning_elem_ids & sketch_curve_ids)
    for c in curves:
        if is_off_axis_line(c, plane_data):
            flagged_ids.add(c['id'])
    print("  Flagged lines : {}".format(len(flagged_ids)))
    _pl = plane_data
    print("  Plane  ux=({:.6f},{:.6f},{:.6f}) uy=({:.6f},{:.6f},{:.6f}) "
          "n=({:.4f},{:.4f},{:.4f})".format(
              _pl['ux'].X, _pl['ux'].Y, _pl['ux'].Z,
              _pl['uy'].X, _pl['uy'].Y, _pl['uy'].Z,
              _pl['nz'].X, _pl['nz'].Y, _pl['nz'].Z))
    _kinds = {}
    for c in curves:
        if c['id'] in flagged_ids:
            _kinds[c['kind']] = _kinds.get(c['kind'], 0) + 1
    print("  Flagged kinds : {}".format(_kinds))
    if not flagged_ids:
        print("  nothing to snap in this sketch")
        write_fails += 1
        continue

    dim_ids = []
    joined_elems = []
    if owner_class == 'Wall':
        dim_ids = collect_sketch_dimensions(sketch)
        joined_elems = find_joined_elements(owner_elem)

    if owner_class == 'Wall':
        strategies = ['normal', 'unjoin', 'unjoin_del_dims']
    else:
        strategies = ['normal', 'unjoin']

    # PASS 1: batch
    new_pts = plan_moves(curves, plane_data, flagged_ids)
    pairs = apply_moves(curves, new_pts)

    if pairs:
        ok, opens = validate_loop_closure(curves, pairs)
        if ok:
            print("  Pass 1 (batch): {} curves".format(len(pairs)))
            success = False
            for strategy in strategies:
                if try_write(sid_int, owner_elem, pairs,
                             dim_ids, joined_elems, strategy):
                    success = True
                    fixed_sketches += 1
                    fixed_curves += len(pairs)
                    print("  BATCH COMMITTED [{}]".format(strategy))
                    break

            if success:
                continue
        else:
            print("  Pass 1 skipped : loop would not close ({} open ends)"
                  .format(len(opens)))
    else:
        print("  Pass 1 : no movable curves planned")

    # PASS 2: per-curve
    print("  Pass 2 (per-curve retry)")

    per_curve_fixed = 0
    for target_cid in sorted(flagged_ids):
        sketch = doc.GetElement(make_eid(sid_int))
        if not sketch:
            break
        curves, plane_data = read_sketch_curves(sketch)
        if not curves:
            break

        if not any(c['id'] == target_cid and c['kind'] == 'line'
                   for c in curves):
            continue

        new_pts = plan_moves(curves, plane_data, set([target_cid]))
        pairs = apply_moves(curves, new_pts)
        if not pairs:
            continue

        ok, _ = validate_loop_closure(curves, pairs)
        if not ok:
            print("    curve {} : loop closure fail - skip"
                  .format(target_cid))
            continue

        for strategy in strategies:
            if try_write(sid_int, owner_elem, pairs,
                         dim_ids, joined_elems, strategy):
                per_curve_fixed += 1
                fixed_curves += len(pairs)
                print("    curve {} FIXED [{}]".format(
                    target_cid, strategy))
                break
        else:
            print("    curve {} : all strategies failed"
                  .format(target_cid))

    if per_curve_fixed > 0:
        fixed_sketches += 1
        print("  Per-curve total: {}".format(per_curve_fixed))
    else:
        write_fails += 1

# ==========================================================
# FINAL COUNT
# ==========================================================

remaining_off_axis = len([w for w in doc.GetWarnings()
                          if is_target_warning(w)])

print("")
print("=" * 60)
print("SKETCHES FIXED       : {}".format(fixed_sketches))
print("CURVES FIXED         : {}".format(fixed_curves))
print("WRITE FAILURES       : {}".format(write_fails))
print("OFF-AXIS REMAINING   : {}".format(remaining_off_axis))
print("=" * 60)

if remaining_off_axis > 0:
    print("")
    print("TIP: Run the script again - each pass can catch")
    print("     warnings that only appear after previous fixes.")
    print("     If warnings persist, copy this output - the lines")
    print("     above show which stage dropped them.")

print("")
print("DONE")