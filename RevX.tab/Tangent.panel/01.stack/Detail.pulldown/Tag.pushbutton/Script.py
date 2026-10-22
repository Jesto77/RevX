# -*- coding: utf-8 -*-
"""
Tag Layers (Side Tag + Under Tag)

Click the button and choose the tagging style:

SIDE TAG  (the original "Tag Layers" script)
  Pick ONE existing tag (e.g. the "004" tag). Every other Filled Region / Detail Component in
  the active view whose TYPE NAME contains a three-digit code 000-099 gets a tag, stacked in
  the same column directly UNDER the picked tag with equal gaps. Each leader ends ON the
  element, on the side facing the column; leaders never cross.

UNDER TAG  (the original "Tag Layers (Section)" script)
  For wide section views. Pick ONE or TWO existing tags (e.g. 017 and 002) and click Finish.
  Every element with a 000-099 code gets a tag in columns to the right of the picked tags,
  from left to right. The leaders come up from below with uniform shoulder lengths and the
  arrowhead INSIDE a filled region / ON the line of a detail component.

Both modes: nothing is tagged twice, tags that would show no text are skipped, and the tag
type, orientation and leader style are copied from the tag(s) you pick.
Settings for each mode are at the top of the script.

Works in Revit 2022 - 2026.
"""
import clr

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System')

from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, FilledRegion, FamilyInstance, IndependentTag,
    Transaction, Reference, XYZ, TagMode, LeaderEndCondition, Curve, Line,
    GeometryInstance, GeometryElement, Options, Solid, ElementId, Element, BuiltInParameter,
    PolyLine, CategoryType
)
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from pyrevit import forms, script

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView

v_origin = view.Origin
v_x = view.RightDirection
v_y = view.UpDirection

# ---- settings: shared -----------------------------------------------------------
TAG_SPACING = 1.15              # centre-to-centre distance between stacked tags = tag height x this
ALSO_CHECK_FAMILY_NAME = True   # if the TYPE name has no 000-099 code, try the family name
SHOW_RESULT = False             # True: small result message at the end
MM = 1.0 / 304.8

# ---- settings: SIDE TAG ---------------------------------------------------------
ONE_TAG_PER_TYPE = True         # True: one tag per type number; False: tag every element

# ---- settings: UNDER TAG --------------------------------------------------------
ONE_TAG_PER_CODE = True         # True: each code is tagged ONCE in the whole view (no repeated tags)
MAX_ROWS = 8                    # most tags stacked in one column when space is short
EXCLUDE_CODES = []              # codes never tagged, e.g. ["005", "010"]
INCLUDE_MODEL_ELEMENTS = True   # also tag model elements (all model categories)

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
# PURE LOGIC - shared
# =============================================================================
import re
import math
import itertools

# three-digit code 000-099 inside a type name; NOT part of a longer digit run
CODE_RE = re.compile(r'(?<!\d)0\d\d(?!\d)')


def type_code(name):
    m = CODE_RE.search(name or "")
    return m.group(0) if m else None


def _orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _seg_cross(p, q, r, s):
    d1, d2 = _orient(p, q, r), _orient(p, q, s)
    d3, d4 = _orient(r, s, p), _orient(r, s, q)
    return (d1 * d2 < -1e-12) and (d3 * d4 < -1e-12)


def _seg_overlap(p, q, r, s_):
    """Collinear segments that share a stretch (two leaders lying on top of each other)."""
    if abs(_orient(p, q, r)) > 1e-9 or abs(_orient(p, q, s_)) > 1e-9:
        return False
    if abs(q[0] - p[0]) >= abs(q[1] - p[1]):
        a0, a1 = sorted((p[0], q[0]))
        b0, b1 = sorted((r[0], s_[0]))
    else:
        a0, a1 = sorted((p[1], q[1]))
        b0, b1 = sorted((r[1], s_[1]))
    return min(a1, b1) - max(a0, b0) > 1e-6


def polys_cross(pa, pb):
    for a in range(len(pa) - 1):
        for b in range(len(pb) - 1):
            if _seg_cross(pa[a], pa[a + 1], pb[b], pb[b + 1]):
                return True
            if _seg_overlap(pa[a], pa[a + 1], pb[b], pb[b + 1]):
                return True
    return False


def count_crossings(polys):
    n = 0
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            if polys_cross(polys[i], polys[j]):
                n += 1
    return n


# =============================================================================
# PURE LOGIC - SIDE TAG
# =============================================================================
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

def _layout_given(items, slot_of, xc, picked_ax, x_offset):
    """Tag y positions come from slot_of (index -> y). Computes attach points (staircase to the
    left, one step per tag) and the leader polylines. Returns the number of crossings."""
    n = len(items)
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

    return count_crossings([it["poly"] for it in items])


def _poly_length(poly):
    return sum(math.hypot(poly[k + 1][0] - poly[k][0], poly[k + 1][1] - poly[k][1])
               for k in range(len(poly) - 1))


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

    # Start: top-to-bottom order of the elements matched to the slots top-to-bottom.
    order = sorted(range(n), key=lambda i: -items[i]["ny"])
    best = {}
    for rank, i in enumerate(order):
        best[i] = slots[rank]

    crossings = _layout_given(items, best, xc, picked_ax, x_offset)
    if crossings == 0:
        return 0

    # Leaders clash: swap tag positions, changing as little as possible. A crossing / overlap
    # counts far more than anything else; among equal results the one that moves the fewest
    # tags (smallest total shift in the top-to-bottom order) wins.
    slot_rank = dict((slots[k], k) for k in range(n))
    start_rank = dict((i, slot_rank[best[i]]) for i in range(n))

    def cost(assign):
        shift = sum(abs(slot_rank[assign[i]] - start_rank[i]) for i in range(n))
        return (count_crossings([it["poly"] for it in items]) * 1.0e6 + shift)

    best_cost = cost(best)
    for _ in range(30):
        improved = False
        for i in range(n):
            for j in range(i + 1, n):
                trial = dict(best)
                trial[i], trial[j] = trial[j], trial[i]
                _layout_given(items, trial, xc, picked_ax, x_offset)
                c = cost(trial)
                if c < best_cost - 1e-9:
                    best_cost = c
                    best = trial
                    improved = True
        if not improved:
            break
    return _layout_given(items, best, xc, picked_ax, x_offset)


# =============================================================================
# PURE LOGIC - UNDER TAG
# =============================================================================
def attach_at_x(segs, x):
    """
    Point ON the outline at horizontal position x, on the LOWEST edge there (the leader comes
    from below). If the outline has nothing at x, the nearest outline point is used.
    segs: list of ((x0, y0), (x1, y1)).  Returns (x, y).
    """
    best = None
    for a, b in segs:
        x0, y0 = a
        x1, y1 = b
        if min(x0, x1) - 1e-9 <= x <= max(x0, x1) + 1e-9:
            if abs(x1 - x0) < 1e-9:
                y = min(y0, y1)
            else:
                y = y0 + (x - x0) * (y1 - y0) / (x1 - x0)
            if best is None or y < best:
                best = y
    if best is not None:
        return (x, best)
    pts = [p for s in segs for p in s]
    p = min(pts, key=lambda q: (abs(q[0] - x), q[1]))
    return (p[0], p[1])


def arrow_point(segs, x, pen):
    """
    Where the arrowhead ends for a leader coming up from below at horizontal position x:
    - several outline lines cross x (filled region, closed symbol): INSIDE the lowest band,
      `pen` above the lowest line (middle of the band when it is thinner than 2 x pen),
      never on the border
    - only one line at x (line-based detail component): exactly ON that line
    - nothing at x: the nearest point of the linework itself
    Returns (x, y).
    """
    ys = []
    for a, b in segs:
        x0, y0 = a
        x1, y1 = b
        if min(x0, x1) - 1e-9 <= x <= max(x0, x1) + 1e-9:
            if abs(x1 - x0) < 1e-9:
                ys.extend([y0, y1])
            else:
                ys.append(y0 + (x - x0) * (y1 - y0) / (x1 - x0))
    if not ys:
        pts = [p for sg in segs for p in sg]
        p = min(pts, key=lambda q: (abs(q[0] - x), q[1]))
        return (p[0], p[1])
    ys.sort()
    uniq = []
    for y in ys:
        if not uniq or y - uniq[-1] > 1e-4:
            uniq.append(y)
    if len(uniq) == 1:
        return (x, uniq[0])
    y0, y1 = uniq[0], uniq[1]
    return (x, y0 + min(pen, 0.5 * (y1 - y0)))



def dedupe_items(items):
    """Exact duplicates (same code, same outline box) keep one element. Returns (kept, dups)."""
    tol = 1.5 * MM
    kept, dups = [], []
    for it in items:
        xs = [p[0] for sg in it["segs"] for p in sg]
        ys = [p[1] for sg in it["segs"] for p in sg]
        box = (min(xs), min(ys), max(xs), max(ys))
        dup = False
        for k in kept:
            if k["code"] != it["code"]:
                continue
            kb = k["_box"]
            if all(abs(box[i] - kb[i]) <= tol for i in range(4)):
                dup = True
                break
        it["_box"] = box
        if dup:
            dups.append(it)
        else:
            kept.append(it)
    return kept, dups


def _column_x(S, c, run, step):
    """
    One tag column holding the elements S (rows from the top). Row k has the shoulder length
    run + k*step, so its leader end is at X - run_k. The element with the smallest right end
    takes the LOWEST row (leftmost leader), so the leaders form a clean staircase.
    X must keep every leader end inside its element's range [lo, hi], and the leftmost leader
    end at or right of c. Returns (X, [(item, row, leader_x), ...]) or None.
    """
    S = sorted(S, key=lambda it: it["hi"])
    cnt = len(S)
    runs = [run + k * step for k in range(cnt)]
    x_min = c + runs[cnt - 1]
    x_max = None
    rows = []
    for idx, it in enumerate(S):
        k = cnt - 1 - idx
        x_min = max(x_min, it["lo"] + runs[k])
        top = it["hi"] + runs[k]
        x_max = top if x_max is None else min(x_max, top)
        rows.append((it, k))
    if x_min > x_max + 1e-9:
        return None
    return x_min, [(it, k, x_min - runs[k]) for it, k in rows]


def plan_columns(items, c0, run, step, caps, h):
    """
    Left to right: every column takes the element that must be tagged soonest (smallest right
    end) plus as many of the next ones as fit, up to caps(g) rows for column number g.
    Returns (columns, dead, dead_at): dead = elements that could not be reached any more,
    dead_at = index of the last column placed before each one was lost.
    """
    remaining = sorted(items, key=lambda it: it["hi"])
    columns, dead, dead_at = [], [], []
    c = c0
    while remaining:
        while remaining and remaining[0]["hi"] < c:
            dead.append(remaining.pop(0))
            dead_at.append(len(columns) - 1)
        if not remaining:
            break
        first = remaining[0]
        rest = remaining[1:11]
        R = caps(len(columns))
        res = None
        for cnt in range(min(R, 1 + len(rest)), 0, -1):
            for combo in itertools.combinations(rest, cnt - 1):
                r = _column_x([first] + list(combo), c, run, step)
                if r is not None:
                    res = r
                    break
            if res is not None:
                break
        X, members = res
        columns.append({"x": X, "members": members})
        used = [m[0] for m in members]
        remaining = [it for it in remaining if all(it is not u for u in used)]
        c = X + 0.8 * h
    return columns, dead, dead_at


def find_plan(items, picked_x, n_rows, run, x_offset, h, max_rows):
    """
    Columns hold the picked pattern (n_rows tags). Only where an element would otherwise find
    no place, that column gets one more tag stacked down, again and again up to max_rows. If
    that is still not enough, the step between the leaders is reduced and it tries again.
    Returns (columns, dead, step, boost).
    """
    c0 = picked_x + 0.75 * h
    max_rows = max(n_rows, max_rows)
    steps = sorted(set([x_offset, max(0.3 * h, 0.65 * x_offset), max(0.3 * h, 0.45 * x_offset),
                        0.3 * h]), reverse=True)
    best = None
    for step in steps:
        boost = {}
        for _ in range(400):
            def caps(g, boost=boost):
                return min(max_rows, n_rows + boost.get(g, 0))
            columns, dead, dead_at = plan_columns(items, c0, run, step, caps, h)
            placed = sum(len(c["members"]) for c in columns)
            if best is None or placed > best[4]:
                best = (columns, dead, step, dict(boost), placed)
            if not dead:
                return columns, dead, step, boost
            cands = sorted(set(g for g in dead_at if g >= 0 and caps(g) < max_rows))
            if not cands:
                break
            boost[cands[0]] = boost.get(cands[0], 0) + 1
    return best[0], best[1], best[2], best[3]


# =============================================================================
# REVIT SIDE - shared
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


def leader_end_x(tag):
    """x (view coordinates) of the tag's leader end, or None."""
    try:
        if not tag.HasLeader:
            return None
    except Exception:
        return None
    try:
        refs = list(tag.GetTaggedReferences())
        if refs:
            return to_2d(tag.GetLeaderEnd(refs[0])).X
    except Exception:
        pass
    try:
        return to_2d(tag.LeaderEnd).X
    except Exception:
        return None


def element_segments(el, extended=False):
    """Outline of an element in view coordinates (boundary for filled regions).
    extended=True (Under Tag): line-based detail components use their exact location line and
    detail polylines are read as well."""
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

    if extended and isinstance(el, FamilyInstance):   # line-based detail component
        try:
            c = el.Location.Curve
            segs = curve_to_segments(c)
            if segs:
                return segs
        except Exception:
            pass

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
        elif extended and isinstance(g, PolyLine):
            try:
                pts = [to_2d(q) for q in g.GetCoordinates()]
                for k in range(len(pts) - 1):
                    if math.hypot(pts[k + 1].X - pts[k].X, pts[k + 1].Y - pts[k].Y) > 0.002:
                        segs.append(((pts[k].X, pts[k].Y), (pts[k + 1].X, pts[k + 1].Y)))
            except Exception:
                pass
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


def collect_candidates(include_model=False):
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
    if include_model:
        try:
            for el in FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType():
                try:
                    cat = el.Category
                    if cat is not None and cat.CategoryType == CategoryType.Model:
                        found[eid_val(el.Id)] = el
                except Exception:
                    pass
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


# =============================================================================
# SIDE TAG
# =============================================================================
def run_side_tag():
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


# =============================================================================
# UNDER TAG
# =============================================================================
def run_under_tag():
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element, TagFilter(),
            "Pick ONE or TWO existing tags (e.g. 017 and 002), then click Finish")
    except Exception:
        script.exit()
    picks = [doc.GetElement(r.ElementId) for r in refs]
    if not picks:
        script.exit()

    info = []
    for tg in picks:
        hd = to_2d(tg.TagHeadPosition)
        info.append((hd.Y, hd.X, tg))
    info.sort(key=lambda t: -t[0])               # top -> bottom
    rows = [i[0] for i in info]                  # tag heights of the pattern
    n_rows = len(rows)
    picked_x = max(i[1] for i in info)
    first = info[0][2]
    type_id = first.GetTypeId()
    try:
        orientation = first.TagOrientation
    except Exception:
        from Autodesk.Revit.DB import TagOrientation
        orientation = TagOrientation.Horizontal

    pbox = element_box(first)
    h = (pbox[3] - pbox[1]) if pbox else 0.0
    if h <= 1e-6:
        h = 0.02 * view.Scale

    # leader pattern copied from the picked tags
    x_offset = 0.8 * h                           # sideways step between the rows' leaders
    run = 1.5 * h                                # horizontal run: tag -> leader end (top row)
    ex_top = leader_end_x(info[0][2])
    if ex_top is not None and (info[0][1] - ex_top) > 0.3 * h:
        run = info[0][1] - ex_top
    if n_rows >= 2:
        ex_2 = leader_end_x(info[1][2])
        if ex_top is not None and ex_2 is not None and abs(ex_top - ex_2) > 0.2 * h:
            x_offset = abs(ex_top - ex_2)

    all_found = collect_candidates(INCLUDE_MODEL_ELEMENTS)

    # ---- what is already tagged with THIS tag type (other tag families do not count)
    tagged_el = set()
    tagged_codes = set()
    for tg in FilteredElementCollector(doc, view.Id).OfClass(IndependentTag):
        try:
            same_type = eid_val(tg.GetTypeId()) == eid_val(type_id)
        except Exception:
            same_type = False
        if same_type:
            for i in tagged_ids(tg):
                tagged_el.add(eid_val(i))
                e = doc.GetElement(i)
                if e is not None:
                    c = code_for(e)
                    if c:
                        tagged_codes.add(c)

    # ---- elements to tag: every 000-099 type code in the view, each element once
    x_floor = picked_x + 0.75 * h                # keep leaders clear of the picked tags
    top_y = rows[0]
    items = []
    skipped = []
    with_code = 0
    for el in all_found:
        code = code_for(el)
        if code is None:
            continue
        with_code += 1
        if code in EXCLUDE_CODES:
            continue
        if eid_val(el.Id) in tagged_el:
            continue                              # already tagged: never twice
        if ONE_TAG_PER_CODE and code in tagged_codes:
            continue                              # this code already has its tag

        segs = element_segments(el, True)
        if not segs:
            skipped.append("{} ({}): no geometry found in this view".format(code, type_name_of(el)))
            continue
        xs = [p[0] for s_ in segs for p in s_]
        ys = [p[1] for s_ in segs for p in s_]
        xl, xr = min(xs), max(xs)
        if max(ys) <= top_y:
            skipped.append("{} ({}): lies below the tag rows".format(code, type_name_of(el)))
            continue
        m = min(0.5 * x_offset, 0.3 * (xr - xl))    # keep the leader inside the element
        lo = xl + m
        hi = xr - m
        if hi < x_floor:
            skipped.append("{} ({}): lies left of the picked tags".format(code, type_name_of(el)))
            continue
        lo = max(lo, x_floor)
        if hi < lo:
            skipped.append("{} ({}): lies left of the picked tags".format(code, type_name_of(el)))
            continue
        items.append({"el": el, "code": code, "segs": segs, "lo": lo, "hi": hi,
                      "cx": 0.5 * (lo + hi), "xr": xr})

    def area_of(item):
        b_ = item["_box"]
        return (b_[2] - b_[0]) * (b_[3] - b_[1]) + (b_[2] - b_[0]) + (b_[3] - b_[1])

    items, dups = dedupe_items(items)             # exact duplicates: one element only
    alternates = {}
    if ONE_TAG_PER_CODE:
        by_code = {}
        for it in items:
            by_code.setdefault(it["code"], []).append(it)
        items = []
        for code, lst in by_code.items():
            lst.sort(key=area_of, reverse=True)   # most visible element first
            items.append(lst[0])
            alternates[code] = lst[1:]            # used if the first finds no place
    else:
        for it in dups:
            skipped.append("{} ({}): exact duplicate of another element, tagged once".format(
                it["code"], type_name_of(it["el"])))

    if not items:
        forms.alert(
            "Nothing to tag.\n\n"
            "Elements found in this view: {}\n"
            "...with a 000-099 number in the type name: {}\n"
            "...left after removing excluded codes, already tagged ones and ones left of "
            "the picked tags: {}".format(len(all_found), with_code, len(items)),
            title="Tag Layers (Section)")
        return

    # ---- plan the columns first (geometry only); if an element finds no place, another
    #      element with the same code takes over
    gap = TAG_SPACING * h
    if n_rows >= 2 and (rows[0] - rows[1]) > 0.5 * h:
        gap = rows[0] - rows[1]

    def row_y(k):
        if k < n_rows:
            return rows[k]
        return rows[-1] - (k - n_rows + 1) * gap

    plan_items = list(items)
    cols, dead, step_used, rows_used = find_plan(
        plan_items, picked_x, n_rows, run, x_offset, h, MAX_ROWS)
    for _round in range(8):
        if not dead:
            break
        swapped = False
        for d in dead:
            alts = alternates.get(d["code"])
            if alts:
                new = alts.pop(0)
                for idx in range(len(plan_items)):
                    if plan_items[idx] is d:
                        plan_items[idx] = new
                        swapped = True
                        break
        if not swapped:
            break
        cols, dead, step_used, rows_used = find_plan(
            plan_items, picked_x, n_rows, run, x_offset, h, MAX_ROWS)
    for d in dead:
        skipped.append("{} ({}): no free tag position, even with {} stacked tags".format(
            d["code"], type_name_of(d["el"]), MAX_ROWS))

    # ---- create the tags - tags that would show no text are removed again
    done, failed = 0, []
    t = Transaction(doc, "Tag Layers (Section)")
    t.Start()
    try:
        provisional = to_3d(picked_x, top_y)
        for col in cols:
            for it, k, lead_x in col["members"]:
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
                    ty = row_y(k)
                    ax, ay = arrow_point(it["segs"], lead_x, 0.3 * x_offset)
                    try:
                        tag.TagHeadPosition = to_3d(col["x"], ty)
                    except Exception:
                        pass
                    set_leader(tag, ref, to_3d(ax, ay), to_3d(ax, ty))
                    done += 1
                except Exception as ex:
                    failed.append("{} ({}): {}".format(
                        it["code"], type_name_of(it["el"]), ex))
        t.Commit()
    except Exception as ex:
        t.RollBack()
        forms.alert("Tagging failed:\n{}".format(ex), exitscript=True)

    failed.extend(skipped)
    if failed:
        forms.alert("Tagged {}, NOT tagged {}:\n\n{}".format(
            done, len(failed), "\n".join(failed[:15])), title="Tag Layers (Section)")
    elif SHOW_RESULT:
        forms.toast("Tagged {} element(s).".format(done), title="Tag Layers (Section)")


# =============================================================================
# START
# =============================================================================
def main():
    choice = forms.CommandSwitchWindow.show(
        ["Side Tag", "Under Tag"],
        message="Tag Layers - choose the tagging style:")
    if choice == "Side Tag":
        run_side_tag()
    elif choice == "Under Tag":
        run_under_tag()


if __name__ == "__main__":
    main()