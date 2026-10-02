# -*- coding: utf-8 -*-
"""
Tag Layers

1. Click the button, then pick ONE existing tag (for example the "004" tag).
2. The script tags every other Filled Region and Detail Component in the active view whose
   TYPE NAME contains a three-digit number from 000 to 099 (026, 005, 099 ... but not 26,
   5, 100, 1026).
   - tags use the same tag type, orientation and leader style as the picked tag
   - they are stacked in the same column as the picked tag, directly UNDER it, with an equal
     gap between them (tag height x TAG_SPACING)
   - each tag has a leader (horizontal run + 45 degree finish) whose arrowhead ends ON the
     element, on the side facing the column. Elements are matched to tag positions so that
     leaders never cross each other.
   - nothing is tagged twice: elements that already carry a tag are skipped, and with
     ONE_TAG_PER_TYPE = True only one element per type is tagged (one "005", one "026" ...)
   - elements whose tag would show no text are skipped

Works in Revit 2022 - 2026.
"""
import clr

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System')

from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, FilledRegion, FamilyInstance, IndependentTag,
    Transaction, Reference, XYZ, TagMode, LeaderEndCondition, Curve, Line,
    GeometryInstance, GeometryElement, Options, Solid, ElementId, Element, BuiltInParameter
)
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from pyrevit import forms, script

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView

v_origin = view.Origin
v_x = view.RightDirection
v_y = view.UpDirection

# ---- settings -------------------------------------------------------------------
ONE_TAG_PER_TYPE = True     # True: one tag per type number; False: tag every element
TAG_SPACING = 1.15          # centre-to-centre distance between stacked tags = tag height x this
ALSO_CHECK_FAMILY_NAME = True   # detail components: if the TYPE name has no 000-099 number, try the family name
SHOW_RESULT = False         # True: small result message at the end
MM = 1.0 / 304.8


def to_2d(pt):
    vec = pt - v_origin
    return XYZ(vec.DotProduct(v_x), vec.DotProduct(v_y), 0)


def to_3d(x, y):
    return v_origin + (x * v_x) + (y * v_y)


def eid_val(eid):
    try:
        return eid.Value          # Revit 2024+
    except AttributeError:
        return eid.IntegerValue   # older


# =============================================================================
# PURE LOGIC (tested outside Revit)
# =============================================================================
import re
import math

# three-digit code 000-099 inside a type name; NOT part of a longer digit run
CODE_RE = re.compile(r'(?<!\d)0\d\d(?!\d)')


def type_code(name):
    m = CODE_RE.search(name or "")
    return m.group(0) if m else None


def attach_point(segs, side):
    """
    Point ON the element's outline, on the side facing the tag column
    (side=+1: right-most edge, side=-1: left-most edge), near the vertical middle.
    segs: list of ((x0, y0), (x1, y1)).  Returns (x, y).
    """
    pts = [p for s in segs for p in s]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    ext = max(xs) if side > 0 else min(xs)
    width = max(xs) - min(xs)
    tol = max(0.003, 0.02 * width)
    ymid = 0.5 * (min(ys) + max(ys))

    best = None
    for a, b in segs:
        if abs(a[0] - ext) > tol or abs(b[0] - ext) > tol:
            continue
        lo, hi = min(a[1], b[1]), max(a[1], b[1])
        dist = max(0.0, lo - ymid, ymid - hi)
        key = (dist, -(hi - lo))
        if best is None or key < best[0]:
            best = (key, a, b, lo, hi)
    if best is not None:
        _, a, b, lo, hi = best
        y = min(max(ymid, lo), hi)
        dy = b[1] - a[1]
        x = a[0] if abs(dy) < 1e-9 else a[0] + (y - a[1]) * (b[0] - a[0]) / dy
        return (x, y)
    cand = [p for p in pts if abs(p[0] - ext) <= tol]
    p = min(cand, key=lambda q: abs(q[1] - ymid))
    return (p[0], p[1])


def get_x_range_at_y(segs, ay):
    xs = []
    for a, b in segs:
        x0, y0 = a
        x1, y1 = b
        if min(y0, y1) - 1e-6 <= ay <= max(y0, y1) + 1e-6:
            if abs(y1 - y0) < 1e-9:
                xs.extend([x0, x1])
            else:
                x = x0 + (ay - y0) * (x1 - x0) / (y1 - y0)
                xs.append(x)
    if not xs:
        pts = [p for s in segs for p in s]
        return min(p[0] for p in pts), max(p[0] for p in pts)
    return min(xs), max(xs)


def straight_pts(it, t, xc):
    """90 degree leader: horizontal from tag, vertical to element."""
    return [(xc, t), (it["ax"], t), (it["ax"], it["ay"])]


def elbow_pts(it, t, xc):
    """90 degree leader: horizontal from tag, vertical to element."""
    return straight_pts(it, t, xc)


def _orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _seg_cross(p, q, r, s):
    d1, d2 = _orient(p, q, r), _orient(p, q, s)
    d3, d4 = _orient(r, s, p), _orient(r, s, q)
    return (d1 * d2 < -1e-12) and (d3 * d4 < -1e-12)


def polys_cross(pa, pb):
    for a in range(len(pa) - 1):
        for b in range(len(pb) - 1):
            if _seg_cross(pa[a], pa[a + 1], pb[b], pb[b + 1]):
                return True
    return False


def count_crossings(polys):
    n = 0
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            if polys_cross(polys[i], polys[j]):
                n += 1
    return n


def assign_slots(items, slots, xc, picked_ax, x_offset):
    n = len(items)
    for it in items:
        box = element_box(it["el"])
        if box:
            it["y_min"] = box[1]
            it["y_max"] = box[3]
            it["ny"] = (box[1] + box[3]) / 2.0
        else:
            it["y_min"] = it["ay"]
            it["y_max"] = it["ay"]
            it["ny"] = it["ay"]
            
    # Strictly preserve top-to-bottom layout by matching sorted elements directly to sorted slots.
    # This prevents leader lines from crossing or having overly long diagonals.
    order = sorted(range(n), key=lambda i: -items[i]["ny"])
    slot_of = {}
    for rank, i in enumerate(order):
        slot_of[i] = slots[rank]

    for i, it in enumerate(items):
        it["ty"] = slot_of[i]
        
    items_by_y = sorted(items, key=lambda it: -it["ty"])
    current_ax = picked_ax
    pen_y = 0.3 * x_offset  # about 0.24 * h
    pen_x = 1.0 * x_offset  # about 0.8 * h
    
    for rank, it in enumerate(items_by_y):
        ty = it["ty"]
        y_min, y_max = it["y_min"], it["y_max"]
        
        safe_y_min = y_min + pen_y
        safe_y_max = y_max - pen_y
        if safe_y_max < safe_y_min:
            safe_y_min = safe_y_max = (y_min + y_max) / 2.0
            
        if safe_y_min <= ty <= safe_y_max:
            is_straight = True
            ay = ty
        elif ty > safe_y_max:
            is_straight = False
            ay = safe_y_max
        else:
            is_straight = False
            ay = safe_y_min

        x0, x1 = get_x_range_at_y(it["segs"], ay)
        
        safe_x_min = x0 + pen_x
        safe_x_max = x1 - pen_x
        if safe_x_max < safe_x_min:
            safe_x_min = safe_x_max = (x0 + x1) / 2.0
            
        target_ax = current_ax - x_offset
        if target_ax > safe_x_max:
            target_ax = safe_x_max
        elif target_ax < safe_x_min:
            target_ax = safe_x_min
            
        it["ax"] = target_ax
        it["ay"] = ay
        current_ax = target_ax
        
        if is_straight:
            it["poly"] = [(xc, ty), (target_ax, ty)]
        else:
            it["poly"] = [(xc, ty), (target_ax, ty), (target_ax, ay)]

    return 0


# =============================================================================
# REVIT SIDE
# =============================================================================
class TagFilter(ISelectionFilter):
    def AllowElement(self, e):
        return isinstance(e, IndependentTag)

    def AllowReference(self, r, p):
        return False


def tagged_ids(tag):
    ids = []
    try:
        for i in tag.GetTaggedLocalElementIds():
            ids.append(i)
    except Exception:
        try:
            ids.append(tag.TaggedLocalElementId)
        except Exception:
            pass
    return ids


def curve_to_segments(c):
    segs = []
    try:
        if isinstance(c, Line):
            a, b = to_2d(c.GetEndPoint(0)), to_2d(c.GetEndPoint(1))
            pts = [(a.X, a.Y), (b.X, b.Y)]
        else:
            pts = []
            for p in c.Tessellate():
                q = to_2d(p)
                pts.append((q.X, q.Y))
        for k in range(len(pts) - 1):
            if math.hypot(pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1]) > 0.002:
                segs.append((pts[k], pts[k + 1]))
    except Exception:
        pass
    return segs


def element_box(el):
    try:
        bb = el.get_BoundingBox(view)
        if bb is None:
            return None
        xs, ys = [], []
        for x in (bb.Min.X, bb.Max.X):
            for y in (bb.Min.Y, bb.Max.Y):
                for z in (bb.Min.Z, bb.Max.Z):
                    q = to_2d(XYZ(x, y, z))
                    xs.append(q.X)
                    ys.append(q.Y)
        return (min(xs), min(ys), max(xs), max(ys))
    except Exception:
        return None


def element_segments(el):
    """Outline of an element in view coordinates (boundary for filled regions)."""
    segs = []
    if isinstance(el, FilledRegion):
        try:
            for loop in el.GetBoundaries():
                for c in loop:
                    segs.extend(curve_to_segments(c))
        except Exception:
            pass
        if segs:
            return segs

    opt = Options()
    opt.ComputeReferences = False
    opt.View = view

    def walk(g):
        if isinstance(g, Curve):
            segs.extend(curve_to_segments(g))
        elif isinstance(g, GeometryInstance):
            for child in g.GetInstanceGeometry():
                walk(child)
        elif isinstance(g, GeometryElement):
            for child in g:
                walk(child)
        elif isinstance(g, Solid):
            try:
                for edge in g.Edges:
                    segs.extend(curve_to_segments(edge.AsCurve()))
            except Exception:
                pass

    try:
        geom = el.get_Geometry(opt)
        if geom:
            for g in geom:
                walk(g)
    except Exception:
        pass

    if not segs:                                  # fallback: bounding box
        b = element_box(el)
        if b is not None:
            x0, y0, x1, y1 = b
            segs = [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                    ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
    return segs


def type_name_of(el):
    """Type name read through parameters (the .Name property is unreliable in IronPython)."""
    try:
        t = doc.GetElement(el.GetTypeId())
    except Exception:
        return ""
    if t is None:
        return ""
    for bip in (BuiltInParameter.ALL_MODEL_TYPE_NAME, BuiltInParameter.SYMBOL_NAME_PARAM):
        try:
            p = t.get_Parameter(bip)
            if p is not None and p.AsString():
                return p.AsString()
        except Exception:
            pass
    try:
        return Element.Name.GetValue(t)
    except Exception:
        pass
    try:
        return t.Name
    except Exception:
        return ""


def family_name_of(el):
    try:
        return el.Symbol.Family.Name
    except Exception:
        return ""


def code_for(el):
    c = type_code(type_name_of(el))
    if c is None and ALSO_CHECK_FAMILY_NAME and not isinstance(el, FilledRegion):
        c = type_code(family_name_of(el))
    return c


def collect_candidates():
    found = {}
    try:
        for el in FilteredElementCollector(doc, view.Id).OfClass(FilledRegion):
            found[eid_val(el.Id)] = el
    except Exception:
        pass
    try:
        col = FilteredElementCollector(doc, view.Id)\
            .OfCategory(BuiltInCategory.OST_DetailComponents).WhereElementIsNotElementType()
        for el in col:
            if isinstance(el, (FamilyInstance, FilledRegion)):
                found[eid_val(el.Id)] = el
    except Exception:
        pass
    return list(found.values())


def create_tag(el, type_id, orientation, head3d):
    ref = Reference(el)
    try:
        tag = IndependentTag.Create(doc, type_id, view.Id, ref, True, orientation, head3d)
    except Exception:
        tag = IndependentTag.Create(doc, view.Id, ref, True, TagMode.TM_ADDBY_CATEGORY,
                                    orientation, head3d)
        tag.ChangeTypeId(type_id)
    return tag, ref


def set_leader(tag, ref, end3d, elbow3d):
    try:
        tag.HasLeader = True
    except Exception:
        pass
    try:
        tag.LeaderEndCondition = LeaderEndCondition.Free
    except Exception:
        pass
    doc.Regenerate()
    r = ref
    try:
        refs = list(tag.GetTaggedReferences())
        if refs:
            r = refs[0]
    except Exception:
        pass
    try:
        tag.SetLeaderEnd(r, end3d)
    except Exception:
        try:
            tag.LeaderEnd = end3d
        except Exception:
            pass
    if elbow3d is not None:
        try:
            tag.SetLeaderElbow(r, elbow3d)
        except Exception:
            try:
                tag.LeaderElbow = elbow3d
            except Exception:
                pass


def main():
    try:
        picked_ref = uidoc.Selection.PickObject(
            ObjectType.Element, TagFilter(), "Pick ONE existing tag (e.g. the 004 tag)")
    except Exception:
        script.exit()
    picked = doc.GetElement(picked_ref.ElementId)

    head = to_2d(picked.TagHeadPosition)
    col_x = head.X
    type_id = picked.GetTypeId()
    try:
        orientation = picked.TagOrientation
    except Exception:
        from Autodesk.Revit.DB import TagOrientation
        orientation = TagOrientation.Horizontal

    pbox = element_box(picked)
    h = (pbox[3] - pbox[1]) if pbox else 0.0
    if h <= 1e-6:
        h = 0.02 * view.Scale
    spacing = TAG_SPACING * h

    picked_ax = col_x - 3.0 * h
    try:
        if picked.HasLeader:
            try:
                picked_ax = to_2d(picked.LeaderEnd).X
            except Exception:
                refs = list(picked.GetTaggedReferences())
                if refs:
                    picked_ax = to_2d(picked.GetLeaderEnd(refs[0])).X
    except Exception:
        pass
    x_offset = 0.8 * h

    # ---- what is already tagged in this view
    tagged_el = set()
    tagged_types = set()
    fixed_ys = []
    for tg in FilteredElementCollector(doc, view.Id).OfClass(IndependentTag):
        for i in tagged_ids(tg):
            tagged_el.add(eid_val(i))
            e = doc.GetElement(i)
            if e is not None:
                try:
                    tagged_types.add(eid_val(e.GetTypeId()))
                except Exception:
                    pass
        try:
            p = to_2d(tg.TagHeadPosition)
            if abs(p.X - col_x) <= max(h, 0.02):
                fixed_ys.append(p.Y)          # tags already in the column: keep clear of them
        except Exception:
            pass

    # ---- elements to tag: filled regions + detail components with a 000-099 type name
    cands = []
    all_found = collect_candidates()
    
    all_xs = []
    for el in all_found:
        b = element_box(el)
        if b:
            all_xs.extend([b[0], b[2]])
    mid_x = (min(all_xs) + max(all_xs)) / 2.0 if all_xs else col_x

    with_code = 0
    for el in all_found:
        code = code_for(el)
        if code is None:
            continue
        with_code += 1
        if eid_val(el.Id) in tagged_el:
            continue
            
        segs = element_segments(el)
        if not segs:
            continue
        xs = [p[0] for s_ in segs for p in s_]
        if not xs:
            continue
        side = 1 if col_x >= 0.5 * (min(xs) + max(xs)) else -1
        ax, ay = attach_point(segs, side)
        
        if ay > head.Y + 0.02:
            continue
            
        x_min, x_max = get_x_range_at_y(segs, ay)
        
        if x_max < mid_x:
            continue
            
        cands.append({"el": el, "code": code, "side": side, "ax": ax, "ay": ay, "x_min": x_min, "x_max": x_max, "segs": segs})

    items = []
    seen_types = set(tagged_types) if ONE_TAG_PER_TYPE else set()
    # biggest element first, so the most visible one of a type carries the tag
    def area_of(item):
        b = element_box(item["el"])
        return (b[2] - b[0]) * (b[3] - b[1]) + (b[2] - b[0]) + (b[3] - b[1]) if b else 0.0
    for item in sorted(cands, key=area_of, reverse=True):
        tid = eid_val(item["el"].GetTypeId())
        if ONE_TAG_PER_TYPE and tid in seen_types:
            continue
        seen_types.add(tid)
        items.append(item)

    if not items:
        forms.alert(
            "Nothing to tag.\n\n"
            "Filled Regions / Detail Components found in this view: {}\n"
            "...with a 000-099 number in the type name: {}\n"
            "...not tagged yet and below selected tag: {}\n"
            "...left after one-tag-per-type: {}".format(
                len(all_found), with_code, len(cands), len(items)),
            title="Tag Layers")
        return

    # ---- create the tags (phase A) - tags that would show no text are removed again
    done, failed = 0, []
    t = Transaction(doc, "Tag Layers")
    t.Start()
    try:
        provisional = to_3d(col_x, head.Y)
        made = []
        for it in items:
            try:
                tag, ref = create_tag(it["el"], type_id, orientation, provisional)
                blank = False
                try:
                    blank = not (tag.TagText or "").strip()
                except Exception:
                    blank = False
                if blank:
                    doc.Delete(tag.Id)
                    failed.append("{} ({}): the tag would show no text".format(
                        it["code"], type_name_of(it["el"])))
                    continue
                it["tag"], it["ref"] = tag, ref
                made.append(it)
            except Exception as ex:
                failed.append("{} ({}): {}".format(it["code"], type_name_of(it["el"]), ex))

        # ---- layout (phase B): stack under the lowest tag already in the column
        y_start = min([head.Y] + fixed_ys)
        
        # calculate natural Y for each made item
        for it in made:
            box = element_box(it["el"])
            if box:
                it["ny"] = (box[3] + box[1]) / 2.0
            else:
                it["ny"] = it["ay"]
                
        # sort by natural Y descending
        made.sort(key=lambda it: -it["ny"])
        
        slots = []
        curr_y = y_start - spacing
        for it in made:
            # tag wants to be at natural Y, but cannot be higher than curr_y
            ty = min(it["ny"], curr_y)
            slots.append(ty)
            curr_y = ty - spacing

        assign_slots(made, slots, col_x, picked_ax, x_offset)

        for it in made:
            try:
                tag = it["tag"]
                head3d = to_3d(col_x, it["ty"])
                try:
                    tag.TagHeadPosition = head3d
                except Exception:
                    pass
                poly = it["poly"]
                elbow3d = to_3d(poly[1][0], poly[1][1]) if len(poly) == 3 else None
                set_leader(tag, it["ref"], to_3d(it["ax"], it["ay"]), elbow3d)
                done += 1
            except Exception as ex:
                failed.append("{} ({}): {}".format(it["code"], type_name_of(it["el"]), ex))
        t.Commit()
    except Exception as ex:
        t.RollBack()
        forms.alert("Tagging failed:\n{}".format(ex), exitscript=True)

    if failed:
        forms.alert("Tagged {}, could not tag {}:\n\n{}".format(
            done, len(failed), "\n".join(failed[:12])), title="Tag Layers")
    elif SHOW_RESULT:
        forms.toast("Tagged {} element(s).".format(done), title="Tag Layers")


if __name__ == "__main__":
    main()