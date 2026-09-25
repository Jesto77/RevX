# -*- coding: utf-8 -*-
"""Floor Edge Walls  (pyRevit, Revit 2024+)

Creates walls of a chosen wall type around a selected floor:
  * one wall per side face of the floor (layer-split faces are merged)
  * wall bottom = floor bottom, wall top = floor top, taken from the
    real face geometry, so it works for any thickness and also for
    sloped / shape-edited floors (those get a profile-sketched wall)
  * wall sits OUTSIDE the floor edge: its interior face touches the
    floor edge face, exterior face points away from the floor
  * curved edges get curved walls

Select one or more floors first (or you will be asked to pick).
"""
import math
from System.Collections.Generic import List
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from pyrevit import revit, forms

doc   = revit.doc
uidoc = revit.uidoc

KEY_DP     = 4        # rounding for geometry keys (ft)
PT_TOL     = 2e-4     # endpoint match tolerance (ft)
Z_TOL      = 1e-3     # elevation tolerance (ft)
PLACE_TOL  = 1e-3     # wall placement tolerance (ft)
SIDE_NZ    = 0.2      # |normal.Z| below this = side face


# ==========================================================
# SELECTION
# ==========================================================

class FloorFilter(ISelectionFilter):
    def AllowElement(self, e):
        return isinstance(e, Floor) or e.GetType().Name == 'Toposolid'

    def AllowReference(self, r, p):
        return False


def get_floors():
    floors = []
    for eid in uidoc.Selection.GetElementIds():
        el = doc.GetElement(eid)
        if isinstance(el, Floor) or el.GetType().Name == 'Toposolid':
            floors.append(el)
    if floors:
        return floors
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element, FloorFilter(),
            "Select floor(s), then click Finish")
    except Exception:
        return []
    return [doc.GetElement(r) for r in refs]


def pick_wall_type():
    names = {}
    for wt in FilteredElementCollector(doc).OfClass(WallType):
        if wt.Kind != WallKind.Basic:
            continue
        p = wt.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        nm = p.AsString() if p else "?"
        names["{} : {}".format(wt.FamilyName, nm)] = wt
    if not names:
        return None
    sel = forms.SelectFromList.show(
        sorted(names.keys()), title="Select wall type",
        multiselect=False, button_name="Create walls")
    return names.get(sel) if sel else None


# ==========================================================
# GEOMETRY HELPERS
# ==========================================================

def rk(v):
    return round(v, KEY_DP)


def pkey(p, use_z=True):
    if use_z:
        return (rk(p.X), rk(p.Y), rk(p.Z))
    return (rk(p.X), rk(p.Y))


def ckey(c, use_z=True):
    a = pkey(c.GetEndPoint(0), use_z)
    b = pkey(c.GetEndPoint(1), use_z)
    m = pkey(c.Evaluate(0.5, True), use_z)
    return (tuple(sorted([a, b])), m, c.GetType().Name)


def get_solids(elem):
    opt = Options()
    opt.DetailLevel = ViewDetailLevel.Fine
    opt.ComputeReferences = False
    out = []

    def walk(geo):
        for g in geo:
            if isinstance(g, Solid):
                if g.Faces.Size > 0 and g.Volume > 1e-9:
                    out.append(g)
            elif isinstance(g, GeometryInstance):
                walk(g.GetInstanceGeometry())

    walk(elem.get_Geometry(opt))
    return out


def face_normal_mid(face):
    if isinstance(face, PlanarFace):
        return face.FaceNormal
    bb = face.GetBoundingBox()
    uv = UV((bb.Min.U + bb.Max.U) / 2.0, (bb.Min.V + bb.Max.V) / 2.0)
    return face.ComputeNormal(uv)


def loop_area(loop):
    pts = []
    for c in loop:
        t = list(c.Tessellate())
        pts.extend(t[:-1])
    a = 0.0
    n = len(pts)
    for i in range(n):
        p = pts[i]
        q = pts[(i + 1) % n]
        a += p.X * q.Y - q.X * p.Y
    return abs(a) / 2.0


def outer_bottom_keys(solids):
    """XY keys of the outer boundary of the floor's bottom face(s).
    Side faces whose edge is not in this set belong to openings."""
    keys = set()
    for s in solids:
        for f in s.Faces:
            if isinstance(f, PlanarFace) and f.FaceNormal.Z < -0.3:
                loops = list(f.GetEdgesAsCurveLoops())
                if not loops:
                    continue
                best = max(loops, key=loop_area)
                for c in best:
                    keys.add(ckey(c, False))
    return keys


def chain_loops(curves):
    """Chain unordered curves into closed, consistently oriented loops."""
    used = [False] * len(curves)
    loops = []
    for i in range(len(curves)):
        if used[i]:
            continue
        used[i] = True
        loop = [curves[i]]
        start = curves[i].GetEndPoint(0)
        end = curves[i].GetEndPoint(1)
        closed = False
        while True:
            if end.DistanceTo(start) < PT_TOL:
                closed = True
                break
            found = False
            for j in range(len(curves)):
                if used[j]:
                    continue
                c = curves[j]
                a = c.GetEndPoint(0)
                b = c.GetEndPoint(1)
                if a.DistanceTo(end) < PT_TOL:
                    nxt, new_end = c, b
                elif b.DistanceTo(end) < PT_TOL:
                    nxt, new_end = c.CreateReversed(), a
                else:
                    continue
                used[j] = True
                loop.append(nxt)
                end = new_end
                found = True
                break
            if not found:
                break
        if closed:
            loops.append(loop)
    return loops


def analyse_loop(loop):
    """-> (zmin, zmax, flat, bottom_curves)
    flat = only horizontal curves at zmin/zmax plus vertical lines
    (i.e. the face is a plain extrusion of its bottom curve)."""
    zs = []
    for c in loop:
        for p in (c.GetEndPoint(0), c.GetEndPoint(1),
                  c.Evaluate(0.5, True)):
            zs.append(p.Z)
    zmin, zmax = min(zs), max(zs)
    flat = True
    bottoms = []
    for c in loop:
        p0 = c.GetEndPoint(0)
        p1 = c.GetEndPoint(1)
        pm = c.Evaluate(0.5, True)
        horiz = abs(p0.Z - p1.Z) < Z_TOL and abs(p0.Z - pm.Z) < Z_TOL
        if horiz:
            if abs(p0.Z - zmin) < Z_TOL:
                bottoms.append(c)
            elif abs(p0.Z - zmax) >= Z_TOL:
                flat = False
        else:
            vertical = (isinstance(c, Line)
                        and abs(p0.X - p1.X) < Z_TOL
                        and abs(p0.Y - p1.Y) < Z_TOL)
            if not vertical:
                flat = False
    return zmin, zmax, flat, bottoms


def horizontal(v):
    h = XYZ(v.X, v.Y, 0)
    if h.GetLength() < 1e-9:
        return None
    return h.Normalize()


def outward_normal(group, curve):
    if group['planar']:
        return group['nh']
    mid = curve.Evaluate(0.5, True)
    best = None
    for f in group['faces']:
        r = f.Project(mid)
        if r is None:
            continue
        if best is None or r.Distance < best[0]:
            best = (r.Distance, f, r.UVPoint)
    if best is None:
        return None
    return horizontal(best[1].ComputeNormal(best[2]))


# ==========================================================
# PLAN: floor -> list of wall tasks
# ==========================================================

def plan_floor(floor):
    solids = get_solids(floor)
    if not solids:
        return [], ["no solid geometry"]
    notes = []
    outer_keys = outer_bottom_keys(solids)

    groups = {}
    for s in solids:
        for f in s.Faces:
            n = face_normal_mid(f)
            if abs(n.Z) >= SIDE_NZ:
                continue
            if isinstance(f, PlanarFace):
                nh = horizontal(f.FaceNormal)
                if nh is None:
                    continue
                d = nh.DotProduct(f.Origin)
                key = ('pl', rk(nh.X), rk(nh.Y), rk(d))
                g = groups.setdefault(key, {
                    'faces': [], 'planar': True, 'nh': nh,
                    'n3': f.FaceNormal})
            elif isinstance(f, CylindricalFace):
                o = f.Origin
                try:
                    rad_obj = f.Radius[0]
                except Exception:
                    rad_obj = f.get_Radius(0) if hasattr(f, 'get_Radius') else f.Radius
                
                # If rad_obj is an XYZ (vector), use its length
                try:
                    rad_val = rad_obj.GetLength()
                except Exception:
                    try:
                        rad_val = float(rad_obj)
                    except Exception:
                        rad_val = 0.0
                        
                key = ('cyl', rk(o.X), rk(o.Y), rk(rad_val))
                g = groups.setdefault(key, {
                    'faces': [], 'planar': False, 'nh': None,
                    'n3': None})
            else:
                key = ('other', id(f))
                g = groups.setdefault(key, {
                    'faces': [], 'planar': False, 'nh': None,
                    'n3': None})
            g['faces'].append(f)

    tasks = []
    for key, g in groups.items():
        edges = []
        for f in g['faces']:
            for loop in f.GetEdgesAsCurveLoops():
                for c in loop:
                    edges.append(c)
        # edges shared by two faces of the group are internal
        # (layer boundaries) - cancel them, keep the outer outline
        bucket = {}
        for c in edges:
            bucket.setdefault(ckey(c), []).append(c)
        remaining = [lst[0] for lst in bucket.values()
                     if len(lst) % 2 == 1]

        for loop in chain_loops(remaining):
            zmin, zmax, flat, bottoms = analyse_loop(loop)
            if zmax - zmin < Z_TOL:
                continue

            if flat and bottoms:
                ref = bottoms[0]
            else:
                ref = None
                for c in loop:
                    if isinstance(c, Line) and abs(
                            c.GetEndPoint(0).Z - c.GetEndPoint(1).Z) > Z_TOL:
                        continue
                    ref = c
                    break
                if ref is None:
                    ref = loop[0]
            is_opening = bool(outer_keys) and \
                ckey(ref, False) not in outer_keys

            if flat:
                for bc in bottoms:
                    nh = outward_normal(g, bc)
                    if nh is None:
                        notes.append("could not get outward direction")
                        continue
                    tasks.append({'kind': 'straight', 'curve': bc,
                                  'zmin': zmin, 'zmax': zmax, 'nh': nh,
                                  'opening': is_opening})
            elif g['planar'] and abs(g['n3'].Z) < 1e-3:
                tasks.append({'kind': 'profile', 'loop': loop,
                              'zmin': zmin, 'zmax': zmax,
                              'nh': g['nh'], 'n3': g['n3'],
                              'opening': is_opening})
            else:
                notes.append("curved/tilted edge gets standard wall (attach top manually)")
                bc_orig = None
                for c in loop:
                    if not isinstance(c, Line):
                        bc_orig = c
                        break
                if not bc_orig:
                    bc_orig = loop[0]
                
                nh = outward_normal(g, bc_orig)
                
                # Project the curve to Z=zmin
                bc = bc_orig
                if isinstance(bc, Arc):
                    p0, p1, pm = bc.GetEndPoint(0), bc.GetEndPoint(1), bc.Evaluate(0.5, True)
                    try:
                        bc = Arc.Create(XYZ(p0.X, p0.Y, zmin), XYZ(p1.X, p1.Y, zmin), XYZ(pm.X, pm.Y, zmin))
                    except Exception:
                        bc = bc.CreateTransformed(Transform.CreateTranslation(XYZ(0, 0, zmin - p0.Z)))
                else:
                    bc = bc.CreateTransformed(Transform.CreateTranslation(XYZ(0, 0, zmin - bc.GetEndPoint(0).Z)))
                
                if nh:
                    tasks.append({'kind': 'straight', 'curve': bc, 'zmin': zmin, 'zmax': zmax, 'nh': nh, 'opening': is_opening})
    return tasks, notes


# ==========================================================
# CREATE
# ==========================================================

def floor_level(floor, zmin):
    lvl = doc.GetElement(floor.LevelId)
    if lvl is not None and isinstance(lvl, Level):
        return lvl
    levels = sorted(FilteredElementCollector(doc).OfClass(Level),
                    key=lambda l: l.Elevation)
    below = [l for l in levels if l.Elevation <= zmin + 0.01]
    return below[-1] if below else levels[0]


def wall_points(wall):
    opt = Options()
    opt.DetailLevel = ViewDetailLevel.Fine
    opt.ComputeReferences = False
    pts = []

    def walk(geo):
        for g in geo:
            if isinstance(g, Solid):
                if g.Volume > 1e-9:
                    for e in g.Edges:
                        pts.extend(list(e.Tessellate()))
            elif isinstance(g, GeometryInstance):
                walk(g.GetInstanceGeometry())

    walk(wall.get_Geometry(opt))
    return pts


def place_outside(wall, ref_curve, nh):
    """Make the wall body lie outside the floor edge with its exterior
    face pointing outward. Measured on the real wall geometry."""
    mid = ref_curve.Evaluate(0.5, True)
    is_line = isinstance(ref_curve, Line)
    
    for _i in range(5):
        doc.Regenerate()
        pts = wall_points(wall)
        if not pts:
            return False

        ori_ok = True
        try:
            ori_ok = wall.Orientation.DotProduct(nh) > 0
        except Exception:
            pass
        if not ori_ok:
            wall.Flip()
            continue

        if not is_line:
            # Curved walls are perfectly placed by CreateOffset; 
            # do not linearly translate them as it causes non-uniform gaps!
            return True

        mn = min((p.X - mid.X) * nh.X + (p.Y - mid.Y) * nh.Y
                 for p in pts)
        if abs(mn) > PLACE_TOL:
            ElementTransformUtils.MoveElement(
                doc, wall.Id, nh.Multiply(-mn))
            continue
        return True
    return False


def set_location_line(wall):
    p = wall.get_Parameter(BuiltInParameter.WALL_KEY_REF_PARAM)
    if p and not p.IsReadOnly:
        p.Set(int(WallLocationLine.FinishFaceInterior))


def create_straight(task, floor, wtype, chosen_level):
    lvl = chosen_level
    c = task['curve']
    dz = lvl.Elevation - c.GetEndPoint(0).Z
    flat_curve = c.CreateTransformed(
        Transform.CreateTranslation(XYZ(0, 0, dz)))
        
    width = wtype.Width
    offset_dist = width / 2.0
    
    offset_curve = None
    try:
        temp_off = flat_curve.CreateOffset(offset_dist, XYZ.BasisZ)
        if temp_off:
            mid_orig = flat_curve.Evaluate(0.5, True)
            mid_off = temp_off.Evaluate(0.5, True)
            # Ensure the offset is in the outward direction (nh)
            if (mid_off - mid_orig).DotProduct(task['nh']) < 0:
                offset_curve = flat_curve.CreateOffset(-offset_dist, XYZ.BasisZ)
            else:
                offset_curve = temp_off
    except Exception:
        pass
        
    if offset_curve:
        wall = Wall.Create(doc, offset_curve, wtype.Id, lvl.Id,
                           task['zmax'] - task['zmin'],
                           task['zmin'] - lvl.Elevation, False, False)
        set_location_line(wall)
    else:
        wall = Wall.Create(doc, flat_curve, wtype.Id, lvl.Id,
                           task['zmax'] - task['zmin'],
                           task['zmin'] - lvl.Elevation, False, False)
        set_location_line(wall)
        if hasattr(wall.Location, 'Curve'):
            try:
                wall.Location.Curve = flat_curve
            except Exception:
                pass
                
    ok = place_outside(wall, c, task['nh'])
    return wall, ok


def create_profile(task, floor, wtype, chosen_level):
    lvl = chosen_level
    prof = List[Curve]()
    for c in task['loop']:
        prof.Add(c)
    
    wall = Wall.Create(doc, prof, wtype.Id, lvl.Id, False, task['n3'])
    
    # Check for vertical inversion (Revit sometimes creates profile walls upside down)
    doc.Regenerate()
    pts = wall_points(wall)
    if pts:
        expected_cz = sum(c.Evaluate(0.5, True).Z for c in task['loop']) / len(task['loop'])
        z_min_wall = min(p.Z for p in pts)
        dz = task['zmin'] - z_min_wall
        wall_cz = (sum(p.Z for p in pts) / len(pts)) + dz
        
        if abs(wall_cz - expected_cz) > 1.0:
            # Wall is inverted! Delete and recreate with reversed curve loop
            doc.Delete(wall.Id)
            prof = List[Curve]()
            for c in reversed(task['loop']):
                prof.Add(c.CreateReversed())
            wall = Wall.Create(doc, prof, wtype.Id, lvl.Id, False, task['n3'])
            doc.Regenerate()
            pts = wall_points(wall)
            z_min_wall = min(p.Z for p in pts)
            dz = task['zmin'] - z_min_wall
            
        if abs(dz) > Z_TOL:
            # Apply the required vertical shift via Base Offset parameter
            p_base = wall.get_Parameter(BuiltInParameter.WALL_BASE_OFFSET)
            if p_base and not p_base.IsReadOnly:
                p_base.Set(dz)
            else:
                ElementTransformUtils.MoveElement(doc, wall.Id, XYZ(0, 0, dz))
            doc.Regenerate()
            
    set_location_line(wall)
    ok = place_outside(wall, task['loop'][0], task['nh'])
    return wall, ok


class WarnSwallower(IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        for m in list(fa.GetFailureMessages()):
            if m.GetSeverity() == FailureSeverity.Warning:
                fa.DeleteWarning(m)
        return FailureProcessingResult.Continue


# ==========================================================
# MAIN
# ==========================================================

def pick_level():
    levels = FilteredElementCollector(doc).OfClass(Level).ToElements()
    if not levels:
        return None
    names = {"{} (Elevation: {})".format(l.Name, round(l.Elevation, 2)): l for l in sorted(levels, key=lambda l: l.Elevation)}
    sel = forms.SelectFromList.show(
        names.keys(), title="Select Base Level for Walls",
        multiselect=False, button_name="Select Level")
    return names.get(sel) if sel else None

def main():
    floors = get_floors()
    if not floors:
        forms.alert("Select at least one floor.", exitscript=True)

    wtype = pick_wall_type()
    if wtype is None:
        return

    chosen_level = pick_level()
    if chosen_level is None:
        return

    plans = []
    for fl in floors:
        tasks, notes = plan_floor(fl)
        plans.append((fl, tasks, notes))

    has_openings = any(t['opening'] for _f, ts, _n in plans for t in ts)
    include_openings = False
    if has_openings:
        include_openings = forms.alert(
            "The floor has openings.\n\nAlso create walls around the "
            "openings (placed inside the opening)?",
            yes=True, no=True)

    made = 0
    failed = 0
    misplaced = 0

    t = Transaction(doc, "Walls on floor edges")
    t.Start()
    try:
        fho = t.GetFailureHandlingOptions()
        fho.SetFailuresPreprocessor(WarnSwallower())
        t.SetFailureHandlingOptions(fho)

        for fl, tasks, notes in plans:
            print("Floor {} : {} edge wall(s) planned".format(
                fl.Id.IntegerValue if hasattr(fl.Id, 'IntegerValue')
                else fl.Id.Value, len(tasks)))
            for n in sorted(set(notes)):
                print("   note: {}".format(n))
            for task in tasks:
                if task['opening'] and not include_openings:
                    continue
                try:
                    if task['kind'] == 'straight':
                        wall, ok = create_straight(task, fl, wtype, chosen_level)
                    else:
                        wall, ok = create_profile(task, fl, wtype, chosen_level)
                    made += 1
                    if not ok:
                        misplaced += 1
                except Exception as ex:
                    failed += 1
                    print("   wall failed: {}".format(ex))
        doc.Regenerate()
        t.Commit()
    except Exception as ex:
        t.RollBack()
        print("Aborted: {}".format(ex))
        return

    print("")
    print("Walls created : {}".format(made))
    if misplaced:
        print("Check placement of {} wall(s) (curved / could not "
              "verify)".format(misplaced))
    if failed:
        print("Failed        : {}".format(failed))


main()