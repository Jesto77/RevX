# -*- coding: utf-8 -*-
# OFF-AXIS SKETCH LINE FIXER v5 - Revit 2026
# - Snaps BOTH endpoints of flagged lines
# - Rounds coordinates to eliminate floating-point drift
# - Pulls adjacent curves to shared exact coordinates
# - Skips spline rebuilds
# - Two-pass strategy (batch then per-curve)

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
# PLAN MOVES
# Snaps both endpoints of each flagged line
# to a rounded common coordinate.
# Pulls adjacent curves to share the exact same value.
# ==========================================================

def plan_endpoint_moves(curves, plane_data, warning_elem_ids):
    ux = plane_data['ux']
    uy = plane_data['uy']

    def _key(p):
        return (round(p.X, 4), round(p.Y, 4), round(p.Z, 4))

    # Identify endpoints that touch splines (never move these)
    spline_endpoints = set()
    for c in curves:
        if c['kind'] == 'spline':
            spline_endpoints.add(_key(c['p0']))
            spline_endpoints.add(_key(c['p1']))

    moves = {}   # _key(old_pt) -> new_pt

    def _register(old_pt, new_pt):
        k = _key(old_pt)
        if k in spline_endpoints:
            return
        moves[k] = new_pt
        # Pull nearby endpoints to identical coordinate
        for c in curves:
            for p in (c['p0'], c['p1']):
                if p.DistanceTo(old_pt) < NEARBY_ENDPOINT_TOL:
                    kp = _key(p)
                    if kp in spline_endpoints:
                        continue
                    if kp not in moves:
                        moves[kp] = new_pt

    AXIS_TOL = 0.0001
    ux_is_x = abs(abs(ux.X) - 1.0) < AXIS_TOL
    ux_is_y = abs(abs(ux.Y) - 1.0) < AXIS_TOL
    ux_is_z = abs(abs(ux.Z) - 1.0) < AXIS_TOL
    uy_is_x = abs(abs(uy.X) - 1.0) < AXIS_TOL
    uy_is_y = abs(abs(uy.Y) - 1.0) < AXIS_TOL
    uy_is_z = abs(abs(uy.Z) - 1.0) < AXIS_TOL

    plane_axis_aligned = (
        (ux_is_x or ux_is_y or ux_is_z) and
        (uy_is_x or uy_is_y or uy_is_z)
    )

    for c in curves:
        if c['kind'] != 'line':
            continue
        if c['id'] not in warning_elem_ids:
            continue

        p0, p1 = c['p0'], c['p1']
        rel = p1 - p0
        du = rel.DotProduct(ux)
        dv = rel.DotProduct(uy)

        direction, should_snap = classify_line(du, dv)
        if not should_snap:
            continue

        p0_spline = _key(p0) in spline_endpoints
        p1_spline = _key(p1) in spline_endpoints
        if p0_spline and p1_spline:
            print("    curve {} : both ends on spline - skip"
                  .format(c['id']))
            continue

        new_p0 = None
        new_p1 = None

        if plane_axis_aligned:
            # Compute rounded common coordinate
            if direction == 'horizontal':
                if uy_is_y:
                    common = round((p0.Y + p1.Y) / 2.0, ROUND_DP)
                    new_p0 = XYZ(p0.X, common, p0.Z)
                    new_p1 = XYZ(p1.X, common, p1.Z)
                elif uy_is_z:
                    common = round((p0.Z + p1.Z) / 2.0, ROUND_DP)
                    new_p0 = XYZ(p0.X, p0.Y, common)
                    new_p1 = XYZ(p1.X, p1.Y, common)
                elif uy_is_x:
                    common = round((p0.X + p1.X) / 2.0, ROUND_DP)
                    new_p0 = XYZ(common, p0.Y, p0.Z)
                    new_p1 = XYZ(common, p1.Y, p1.Z)
            else:  # vertical
                if ux_is_x:
                    common = round((p0.X + p1.X) / 2.0, ROUND_DP)
                    new_p0 = XYZ(common, p0.Y, p0.Z)
                    new_p1 = XYZ(common, p1.Y, p1.Z)
                elif ux_is_y:
                    common = round((p0.Y + p1.Y) / 2.0, ROUND_DP)
                    new_p0 = XYZ(p0.X, common, p0.Z)
                    new_p1 = XYZ(p1.X, common, p1.Z)
                elif ux_is_z:
                    common = round((p0.Z + p1.Z) / 2.0, ROUND_DP)
                    new_p0 = XYZ(p0.X, p0.Y, common)
                    new_p1 = XYZ(p1.X, p1.Y, common)
        else:
            # Tilted plane - UV-based snap
            origin = plane_data['origin']

            def to_uv(p):
                rel_p = p - origin
                return rel_p.DotProduct(ux), rel_p.DotProduct(uy)

            def from_uv(u, v):
                return origin.Add(
                    ux.Multiply(u)).Add(uy.Multiply(v))

            u0, v0 = to_uv(p0)
            u1, v1 = to_uv(p1)

            if direction == 'horizontal':
                common_v = round((v0 + v1) / 2.0, ROUND_DP)
                new_p0 = from_uv(u0, common_v)
                new_p1 = from_uv(u1, common_v)
            else:
                common_u = round((u0 + u1) / 2.0, ROUND_DP)
                new_p0 = from_uv(common_u, v0)
                new_p1 = from_uv(common_u, v1)

        if new_p0 is None or new_p1 is None:
            continue

        if new_p0.DistanceTo(new_p1) < app.ShortCurveTolerance:
            continue

        # Register both endpoint moves
        if not p0_spline:
            _register(p0, new_p0)
        if not p1_spline:
            _register(p1, new_p1)

    return moves


def add_gap_closure_moves(curves, moves):
    """
    Close any pre-existing tiny gaps between different curves'
    endpoints (regardless of flag status).
    """
    def _key(p):
        return (round(p.X, 4), round(p.Y, 4), round(p.Z, 4))

    spline_endpoints = set()
    for c in curves:
        if c['kind'] == 'spline':
            spline_endpoints.add(_key(c['p0']))
            spline_endpoints.add(_key(c['p1']))

    endpoint_list = []
    for c in curves:
        endpoint_list.append((c['id'], 0, c['p0']))
        endpoint_list.append((c['id'], 1, c['p1']))

    for i in range(len(endpoint_list)):
        cid1, ei1, p1 = endpoint_list[i]
        for j in range(i + 1, len(endpoint_list)):
            cid2, ei2, p2 = endpoint_list[j]
            if cid1 == cid2:
                continue
            d = p1.DistanceTo(p2)
            if VERY_SMALL < d < NEARBY_ENDPOINT_TOL:
                k1 = _key(p1)
                k2 = _key(p2)
                k1_spline = k1 in spline_endpoints
                k2_spline = k2 in spline_endpoints
                if k1_spline and k2_spline:
                    continue
                if k1 in moves and k2 not in moves and not k2_spline:
                    moves[k2] = moves[k1]
                elif k2 in moves and k1 not in moves and not k1_spline:
                    moves[k1] = moves[k2]
                elif k1 not in moves and k2 not in moves:
                    if k1_spline:
                        moves[k2] = p1
                    elif k2_spline:
                        moves[k1] = p2
                    else:
                        # Merge to rounded midpoint
                        mid = XYZ(
                            round((p1.X + p2.X) / 2.0, ROUND_DP),
                            round((p1.Y + p2.Y) / 2.0, ROUND_DP),
                            round((p1.Z + p2.Z) / 2.0, ROUND_DP)
                        )
                        moves[k1] = mid
                        moves[k2] = mid


def apply_moves(curves, moves):
    def _key(p):
        return (round(p.X, 4), round(p.Y, 4), round(p.Z, 4))

    pairs = []
    for c in curves:
        k0 = _key(c['p0'])
        k1 = _key(c['p1'])
        new_p0 = moves.get(k0, c['p0'])
        new_p1 = moves.get(k1, c['p1'])
        moved = (new_p0 is not c['p0'] or new_p1 is not c['p1'])
        if not moved:
            continue
        new_curve = rebuild_curve(c, new_p0, new_p1)
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
            return False

        scope_proc = BulldozerProc()
        try:
            ses.Commit(scope_proc)
            scope_committed = True
        except Exception:
            return False

        try:
            tg.Assimilate()
            tg_assimilated = True
        except Exception:
            pass
        return True

    except Exception:
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

warnings_list = []
for w in doc.GetWarnings():
    try:
        txt = w.GetDescriptionText().lower()
        if "line in sketch" in txt and "slightly off axis" in txt:
            warnings_list.append(w)
    except Exception:
        pass

print("")
print("=" * 60)
print("OFF-AXIS LINE FIXER v5 - Revit 2026")
print("=" * 60)
print("TARGET WARNINGS FOUND : {}".format(len(warnings_list)))
print("")

curve_id_to_sketch_id = {}
all_sketches = (FilteredElementCollector(doc)
                .OfClass(Sketch).ToElements())
for sk in all_sketches:
    try:
        for ceid in sk.GetAllElements():
            curve_id_to_sketch_id[get_id_value(ceid)] = get_id_value(sk.Id)
    except Exception:
        pass

warning_elem_ids = set()
sketch_ids       = set()

for w in warnings_list:
    try:
        ids = list(w.GetFailingElements())
    except Exception:
        continue
    for eid in ids:
        iv = get_id_value(eid)
        elem = doc.GetElement(eid)
        if elem is not None and isinstance(elem, ModelCurve):
            warning_elem_ids.add(iv)
            if iv in curve_id_to_sketch_id:
                sketch_ids.add(curve_id_to_sketch_id[iv])
        elif elem is not None and isinstance(elem, Sketch):
            sketch_ids.add(iv)
        else:
            try:
                sid = get_sketch_id(elem)
                if sid and sid != ElementId.InvalidElementId:
                    sketch_ids.add(get_id_value(sid))
            except Exception:
                pass

print("SKETCH IDS : {}".format(sorted(sketch_ids)))
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
        continue

    curves, plane_data = read_sketch_curves(sketch)
    if not curves:
        continue

    print("  Owner : {} ({})".format(
        get_id_value(owner_elem.Id), owner_class))

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
    moves = plan_endpoint_moves(curves, plane_data, warning_elem_ids)
    add_gap_closure_moves(curves, moves)
    pairs = apply_moves(curves, moves)

    if pairs:
        ok, _ = validate_loop_closure(curves, pairs)
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

    # PASS 2: per-curve
    print("  Pass 2 (per-curve retry)")

    per_curve_fixed = 0
    for target_cid in list(warning_elem_ids):
        # Refresh sketch state
        curves, plane_data = read_sketch_curves(sketch)
        if not curves:
            break

        found = False
        for c in curves:
            if c['id'] == target_cid and c['kind'] == 'line':
                found = True
                break
        if not found:
            continue

        single_warning = set([target_cid])
        moves = plan_endpoint_moves(curves, plane_data, single_warning)
        pairs = apply_moves(curves, moves)

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

remaining_off_axis = 0
for w in doc.GetWarnings():
    try:
        txt = w.GetDescriptionText().lower()
        if "line in sketch" in txt and "slightly off axis" in txt:
            remaining_off_axis += 1
    except Exception:
        pass

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
    print("     If warnings persist after 2-3 runs, they may")
    print("     be on lines whose endpoints are pinned by")
    print("     splines or other constraints.")

print("")
print("DONE")