# -*- coding: utf-8 -*-
"""Toposolid → Filled Region  |  Landscape-link converter.

Pick a linked landscape RVT visible on the current sheet / plan view,
then choose WHICH Toposolid types (families) to convert, and the script will:
  1. Collect the Toposolids of the chosen types in that linked file.
  2. Detect and SKIP parent/host Toposolids that have sub-divisions
     (only leaf Toposolids become filled regions).
  3. Extract the boundary (sketch profile → geometry fallback).
  4. Deduplicate overlapping loops.
  5. Match each Toposolid type name to an EXISTING Filled Region type
     whose name shares a keyword (anywhere in the name). No Filled Region
     type is ever created.
  6. Create one SEPARATE Filled Region per separate boundary (holes stay
     with the boundary that contains them).
"""

__title__  = "Planting"
__author__ = "Jesto Joy"
__doc__    = ("Select a linked landscape model and convert its "
              "Toposolids into filled regions with matching type names.")

# ─── stdlib ──────────────────────────────────────────────────────────
import re
import traceback

# ─── .NET / Revit ────────────────────────────────────────────────────
import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from System.Collections.Generic import List

from Autodesk.Revit.DB import (
    BoundingBoxXYZ,
    CurveLoop,
    ElementClassFilter,
    ElementId,
    FilteredElementCollector,
    FilledRegion,
    FilledRegionType,
    GeometryInstance,
    Line,
    Arc,
    Options,
    PlanarFace,
    RevitLinkInstance,
    Sketch,
    Solid,
    Transaction,
    ViewPlan,
    ViewSheet,
    XYZ,
)

# ─── pyRevit ─────────────────────────────────────────────────────────
from pyrevit import revit, forms, script

logger = script.get_logger()
output = script.get_output()

# The pyRevit output window pops up on the FIRST print, before the user has
# picked anything. Collect the report and print it once, at the very end.
_report = []


def log(md):
    _report.append(md)


def flush_report():
    for line in _report:
        output.print_md(line)

doc   = revit.doc
uidoc = revit.uidoc

# Try to import Toposolid — Revit 2024+ only
try:
    from Autodesk.Revit.DB import Toposolid
except ImportError:
    forms.alert(
        "Toposolid class is not available.\n\n"
        "This tool requires Revit 2024 or later.",
        exitscript=True,
    )


# =====================================================================
#  GENERAL HELPERS
# =====================================================================

def _eid(eid):
    """ElementId -> int. Revit 2024+ uses .Value, older uses .IntegerValue."""
    try:
        return int(eid.Value)
    except AttributeError:
        return eid.IntegerValue


def _element_name(el):
    """Return the Name property of an Element safely."""
    try:
        return el.Name
    except Exception:
        try:
            from Autodesk.Revit.DB import Element
            return Element.Name.GetValue(el)
        except Exception:
            return ""


def _iter_curve_loop(loop):
    """Yield every Curve inside a CurveLoop (IronPython-safe)."""
    it = loop.GetCurveLoopIterator()
    while it.MoveNext():
        yield it.Current


# =====================================================================
#  HOST / SUB-DIVISION DETECTION
#  A parent Toposolid that has sub-divisions should be SKIPPED —
#  only the leaf (child) Toposolids become filled regions.
# =====================================================================

def _identify_host_topos(toposolids, link_doc):
    """Return a set of Element-ID ints for parent/host Toposolids.

    Tries three strategies in order:
      1. Revit API  GetHostTopoId()  — each sub-division points to host
      2. GetDependentElements()      — host has children as dependents
      3. Bounding-box containment    — host bbox contains children bbox
    """
    host_ids = set()

    # ── Strategy 1: GetHostTopoId() ──────────────────────────────────
    try:
        for topo in toposolids:
            hid = topo.GetHostTopoId()
            if hid is not None and hid != ElementId.InvalidElementId:
                host_ids.add(_eid(hid))
        if host_ids:
            return host_ids
    except Exception:
        pass  # method may not exist in this Revit build

    # ── Strategy 2: GetDependentElements ─────────────────────────────
    try:
        topo_filter = ElementClassFilter(Toposolid)
        for topo in toposolids:
            dep_ids = topo.GetDependentElements(topo_filter)
            if dep_ids is None:
                continue
            for dep_id in dep_ids:
                dep = link_doc.GetElement(dep_id)
                if dep is not None and isinstance(dep, Toposolid):
                    # 'topo' has a Toposolid child → it is a host
                    host_ids.add(_eid(topo.Id))
                    break
        if host_ids:
            return host_ids
    except Exception:
        pass

    # ── Strategy 3: bounding-box containment ─────────────────────────
    bboxes = {}
    for topo in toposolids:
        try:
            bb = topo.get_BoundingBox(None)
            if bb:
                bboxes[_eid(topo.Id)] = bb
        except Exception:
            pass

    TOL = 0.3  # ≈ 100 mm slop
    for id_a, bb_a in bboxes.items():
        contains_count = 0
        for id_b, bb_b in bboxes.items():
            if id_a == id_b:
                continue
            if (bb_a.Min.X - TOL <= bb_b.Min.X and
                bb_a.Min.Y - TOL <= bb_b.Min.Y and
                bb_a.Max.X + TOL >= bb_b.Max.X and
                bb_a.Max.Y + TOL >= bb_b.Max.Y):
                contains_count += 1
        # If it fully contains 2 + other toposolids → it's a host
        if contains_count >= 2:
            host_ids.add(id_a)

    return host_ids


# =====================================================================
#  VIEW / LINK SELECTION
# =====================================================================

def get_target_view():
    view = doc.ActiveView
    if isinstance(view, ViewPlan):
        return view
    if isinstance(view, ViewSheet):
        vp_ids = view.GetAllViewports()
        if not vp_ids or vp_ids.Count == 0:
            forms.alert("No viewports on this sheet.", exitscript=True)
        plan_views = {}
        for vp_id in vp_ids:
            vp = doc.GetElement(vp_id)
            v  = doc.GetElement(vp.ViewId)
            if isinstance(v, ViewPlan):
                plan_views[v.Name] = v
        if not plan_views:
            forms.alert("No plan views found on this sheet.", exitscript=True)
        if len(plan_views) == 1:
            return list(plan_views.values())[0]
        sel = forms.SelectFromList.show(
            sorted(plan_views.keys()),
            title="Select Target Plan View",
            button_name="Select",
        )
        if not sel:
            script.exit()
        return plan_views[sel]
    forms.alert("Please open a Plan View or a Sheet first.", exitscript=True)


def get_linked_file():
    links = (
        FilteredElementCollector(doc)
        .OfClass(RevitLinkInstance)
        .ToElements()
    )
    if not links:
        forms.alert("No linked Revit files in the project.", exitscript=True)
    link_map = {}
    for lnk in links:
        ldoc = lnk.GetLinkDocument()
        if ldoc is None:
            continue
        name = ldoc.Title
        if name in link_map:
            name = "{} [ID {}]".format(name, _eid(lnk.Id))
        link_map[name] = (lnk, ldoc)
    if not link_map:
        forms.alert("All linked files are unloaded.", exitscript=True)
    sel = forms.SelectFromList.show(
        sorted(link_map.keys()),
        title="Select Linked Landscape Model",
        button_name="Select",
    )
    if not sel:
        script.exit()
    return link_map[sel]


def collect_toposolids(link_doc):
    try:
        return list(
            FilteredElementCollector(link_doc)
            .OfClass(Toposolid)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception as exc:
        forms.alert(
            "Cannot collect Toposolids:\n{}".format(exc),
            exitscript=True,
        )


def get_fr_type_dict():
    d = {}
    for frt in (
        FilteredElementCollector(doc)
        .OfClass(FilledRegionType)
        .ToElements()
    ):
        d[_element_name(frt)] = frt
    return d


# =====================================================================
#  NAME-MATCHING
# =====================================================================

def _tokenise(name):
    """Split a name into upper-cased tokens at two granularity levels.

    Level 1 — split by  _ / \\ - whitespace  (keeps dots together)
    Level 2 — also split on dots for sub-tokens

    Example:
      'Toposolid_Shrub_LAN_Planting_S/BOUG.GR'
      → {'TOPOSOLID','SHRUB','LAN','PLANTING','BOUG.GR','BOUG','GR'}
    """
    major = re.split(r"[_/\\\s\-]+", name)
    tokens = set()
    for t in major:
        up = t.upper().strip()
        if len(up) >= 2:
            tokens.add(up)
        for sub in t.split("."):
            sup = sub.upper().strip()
            if len(sup) >= 2:
                tokens.add(sup)
    return tokens


_NOISE = frozenset([
    "TOPOSOLID", "TOPO", "SOLID", "TYPE", "DEFAULT", "NEW",
    "LAN", "LA", "DTC", "THE", "RGN",
])


def match_fr_type(topo_type_name, fr_type_dict):
    """Find the EXISTING filled region type whose name shares a keyword
    with the Toposolid type name - keyword may sit ANYWHERE in the name
    (e.g. 'BOUG' inside 'LA_RGN_PLANTING_S/BOUG.GR').

    Returns (FilledRegionType, matched_name, score)."""
    topo_tokens = _tokenise(topo_type_name) - _NOISE
    topo_up = topo_type_name.upper()
    best_type  = None
    best_name  = None
    best_score = 0

    for fr_name, fr_type in fr_type_dict.items():
        fr_up = fr_name.upper()
        fr_tokens = _tokenise(fr_name)
        score = sum(len(t) for t in (topo_tokens & fr_tokens))
        # keyword found anywhere inside the filled region type name
        for t in topo_tokens:
            if len(t) >= 3 and t in fr_up and t not in fr_tokens:
                score += len(t)
        # ...or the filled region keyword sits inside the Toposolid name
        for t in (fr_tokens - _NOISE):
            if len(t) >= 3 and t in topo_up and t not in topo_tokens:
                score += len(t)
        if score > best_score:
            best_score = score
            best_type  = fr_type
            best_name  = fr_name

    if best_score >= 3:
        return best_type, best_name, best_score
    return None, None, 0


# =====================================================================
#  CURVE HELPERS
# =====================================================================

def _flat_pt(pt, z=0.0):
    return XYZ(pt.X, pt.Y, z)


def _flatten_curve(curve, z=0.0):
    try:
        if isinstance(curve, Line):
            p0 = _flat_pt(curve.GetEndPoint(0), z)
            p1 = _flat_pt(curve.GetEndPoint(1), z)
            if p0.DistanceTo(p1) < 1e-6:
                return None
            return Line.CreateBound(p0, p1)
        if isinstance(curve, Arc):
            p0  = _flat_pt(curve.GetEndPoint(0), z)
            p1  = _flat_pt(curve.GetEndPoint(1), z)
            mid = _flat_pt(curve.Evaluate(0.5, True), z)
            if p0.DistanceTo(p1) < 1e-6 or p0.DistanceTo(mid) < 1e-6:
                return None
            try:
                return Arc.Create(p0, p1, mid)
            except Exception:
                pass
        pts = [_flat_pt(p, z) for p in curve.Tessellate()]
        segs = []
        for i in range(len(pts) - 1):
            if pts[i].DistanceTo(pts[i + 1]) > 1e-6:
                segs.append(Line.CreateBound(pts[i], pts[i + 1]))
        return segs or None
    except Exception:
        return None


def _build_curve_loop(raw_curves, transform, z):
    flat = []
    for curve in raw_curves:
        xformed = curve.CreateTransformed(transform)
        result  = _flatten_curve(xformed, z)
        if result is None:
            continue
        if isinstance(result, list):
            flat.extend(result)
        else:
            flat.append(result)

    if len(flat) < 2:
        return None

    healed = [flat[0]]
    for i in range(1, len(flat)):
        prev_end   = healed[-1].GetEndPoint(1)
        curr_start = flat[i].GetEndPoint(0)
        curr_end   = flat[i].GetEndPoint(1)
        gap = prev_end.DistanceTo(curr_start)
        if gap < 1e-9:
            healed.append(flat[i])
        elif prev_end.DistanceTo(curr_end) < gap:
            healed.append(flat[i].CreateReversed())
        elif gap < 0.05:
            try:
                healed.append(Line.CreateBound(prev_end, curr_start))
            except Exception:
                pass
            healed.append(flat[i])
        else:
            healed.append(flat[i])

    if healed:
        last_end    = healed[-1].GetEndPoint(1)
        first_start = healed[0].GetEndPoint(0)
        gap = last_end.DistanceTo(first_start)
        if 1e-9 < gap < 0.05:
            try:
                healed.append(Line.CreateBound(last_end, first_start))
            except Exception:
                pass

    try:
        cl = CurveLoop()
        for c in healed:
            cl.Append(c)
        return cl
    except Exception as exc:
        logger.debug("CurveLoop build failed: {}".format(exc))
        return None


# =====================================================================
#  LOOP DEDUPLICATION
# =====================================================================

def _loop_signature(loop):
    pts = []
    for curve in _iter_curve_loop(loop):
        pts.append(curve.GetEndPoint(0))
    if not pts:
        return None
    xs = [p.X for p in pts]
    ys = [p.Y for p in pts]
    perimeter = 0.0
    for i in range(len(pts)):
        perimeter += pts[i].DistanceTo(pts[(i + 1) % len(pts)])
    return (min(xs), max(xs), min(ys), max(ys), perimeter)


def _deduplicate_loops(loops, tol=0.15):
    if len(loops) <= 1:
        return loops
    unique = []
    sigs   = []
    for loop in loops:
        sig = _loop_signature(loop)
        if sig is None:
            continue
        is_dup = False
        for esig in sigs:
            if all(abs(a - b) < tol for a, b in zip(sig, esig)):
                is_dup = True
                break
        if not is_dup:
            unique.append(loop)
            sigs.append(sig)
    return unique


# =====================================================================
#  BOUNDARY EXTRACTION
# =====================================================================

def _boundaries_from_sketch(topo, link_doc, transform, z):
    try:
        sketch_filter = ElementClassFilter(Sketch)
        dep_ids = topo.GetDependentElements(sketch_filter)
        if not dep_ids or dep_ids.Count == 0:
            return None
        all_loops = []
        for dep_id in dep_ids:
            sk = link_doc.GetElement(dep_id)
            if sk is None or not isinstance(sk, Sketch):
                continue
            for curve_arr in sk.Profile:
                raw = [curve_arr[i] for i in range(curve_arr.Size)]
                loop = _build_curve_loop(raw, transform, z)
                if loop is not None:
                    all_loops.append(loop)
            if all_loops:
                break
        if all_loops:
            return _deduplicate_loops(all_loops)
        return None
    except Exception as exc:
        logger.debug("Sketch strategy failed: {}".format(exc))
        return None


def _boundaries_from_geometry(topo, transform, z):
    try:
        opt = Options()
        opt.ComputeReferences = False
        opt.IncludeNonVisibleObjects = False
        geom = topo.get_Geometry(opt)
        if not geom:
            return None
        solids = []
        for obj in geom:
            if isinstance(obj, Solid) and obj.Volume > 1e-6:
                solids.append(obj)
            elif isinstance(obj, GeometryInstance):
                for g in obj.GetInstanceGeometry():
                    if isinstance(g, Solid) and g.Volume > 1e-6:
                        solids.append(g)
        if not solids:
            return None

        # Single largest horizontal planar face across ALL solids
        best_face = None
        best_area = 0.0
        for solid in solids:
            for face in solid.Faces:
                if not isinstance(face, PlanarFace):
                    continue
                if abs(face.FaceNormal.Z) < 0.7:
                    continue
                if face.Area > best_area:
                    best_area = face.Area
                    best_face = face
        if best_face is None:
            return None

        all_loops = []
        for edge_array in best_face.EdgeLoops:
            raw = [edge_array[i].AsCurve()
                   for i in range(edge_array.Size)]
            loop = _build_curve_loop(raw, transform, z)
            if loop is not None:
                all_loops.append(loop)
        if all_loops:
            return _deduplicate_loops(all_loops)
        return None
    except Exception as exc:
        logger.debug("Geometry strategy failed: {}".format(exc))
        return None


def get_topo_boundaries(topo, link_doc, transform, z=0.0):
    loops = _boundaries_from_sketch(topo, link_doc, transform, z)
    if loops:
        return loops
    loops = _boundaries_from_geometry(topo, transform, z)
    if loops:
        return loops
    return None


def _loop_poly(loop):
    pts = []
    for c in _iter_curve_loop(loop):
        try:
            tess = list(c.Tessellate())
        except Exception:
            continue
        for p in tess[:-1]:
            pts.append((p.X, p.Y))
    return pts


def _poly_area(poly):
    a = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) * 0.5


def _pt_in_poly(x, y, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        dy = (yj - yi) if abs(yj - yi) > 1e-15 else 1e-15
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / dy + xi):
            inside = not inside
        j = i
    return inside


def _poly_inside(inner, outer):
    """True when most vertices of 'inner' lie inside 'outer'."""
    if not inner or not outer:
        return False
    step = max(1, len(inner) // 12)
    pts = inner[::step]
    hits = sum(1 for (x, y) in pts if _pt_in_poly(x, y, outer))
    return hits >= max(1, int(len(pts) * 0.7 + 0.5))


def split_into_regions(loops):
    """Separate boundaries -> separate filled regions.

    Returns a list of loop groups: [outer, hole, hole ...]. Every outer
    boundary becomes its OWN filled region; only genuine holes (loops
    inside an outer) travel with it."""
    info = []
    for lp in loops:
        poly = _loop_poly(lp)
        if len(poly) >= 3:
            info.append({"loop": lp, "poly": poly, "area": _poly_area(poly)})
    info.sort(key=lambda d: -d["area"])

    for d in info:
        d["containers"] = [o for o in info
                           if o is not d and o["area"] > d["area"]
                           and _poly_inside(d["poly"], o["poly"])]
        d["depth"] = len(d["containers"])

    groups = {}
    order = []
    for d in info:
        if d["depth"] % 2 == 0:                 # outer boundary
            groups[id(d)] = [d["loop"]]
            order.append(id(d))
    for d in info:
        if d["depth"] % 2 == 1:                 # hole of its tightest outer
            parent = min(d["containers"], key=lambda o: o["area"])
            if id(parent) in groups:
                groups[id(parent)].append(d["loop"])
    return [groups[k] for k in order]


# =====================================================================
#  CROSS-ELEMENT DEDUP TRACKER
# =====================================================================

class GlobalDedup(object):
    def __init__(self, tol=0.15):
        self._sigs = []
        self._tol  = tol

    def is_duplicate(self, loops):
        if not loops:
            return True
        new_sigs = [_loop_signature(l) for l in loops]
        new_sigs = [s for s in new_sigs if s is not None]
        if not new_sigs:
            return True
        all_known = True
        for nsig in new_sigs:
            found = False
            for esig in self._sigs:
                if all(abs(a - b) < self._tol
                       for a, b in zip(nsig, esig)):
                    found = True
                    break
            if not found:
                all_known = False
                break
        return all_known

    def register(self, loops):
        for loop in loops:
            sig = _loop_signature(loop)
            if sig is not None:
                self._sigs.append(sig)


# =====================================================================
#  TOPOSOLID TYPE PICKER
# =====================================================================

def _topo_type_name(topo, link_doc):
    try:
        t = link_doc.GetElement(topo.GetTypeId())
        return _element_name(t) if t else "Unknown"
    except Exception:
        return "Unknown"


def pick_topo_types(toposolids, link_doc):
    """Ask which Toposolid types to convert; return the chosen elements.
    Every type in the link is listed - YOU decide what gets converted."""
    groups = {}
    for topo in toposolids:
        name = _topo_type_name(topo, link_doc)
        groups.setdefault(name, []).append(topo)

    labels = {}
    for name in sorted(groups.keys()):
        n = len(groups[name])
        label = "{}   ({} element{})".format(
            name, n, "" if n == 1 else "s")
        labels[label] = name

    chosen_labels = forms.SelectFromList.show(
        sorted(labels.keys()),
        title="Select Toposolid type(s) to convert",
        button_name="Convert selected",
        multiselect=True,
    )
    if not chosen_labels:
        script.exit()

    chosen_names = sorted(set(labels[l] for l in chosen_labels))
    chosen = []
    for name in chosen_names:
        chosen.extend(groups[name])
    return chosen, chosen_names


_masking_done = set()


def unmask_fr_type(fr_type):
    """Untick 'Masking' on a filled region type (once per type)."""
    key = _eid(fr_type.Id)
    if key in _masking_done:
        return
    _masking_done.add(key)
    try:
        fr_type.IsMasking = False
    except Exception:
        try:
            p = fr_type.LookupParameter("Masking")
            if p is not None and not p.IsReadOnly:
                p.Set(0)
        except Exception as exc:
            logger.debug("Masking not changed: {}".format(exc))


# =====================================================================
#  MAIN
# =====================================================================

log("# 🌿 Toposolid → Filled Region Converter")
log("---")

# 1 ── target view
target_view = get_target_view()
log("**Target view:** {}".format(target_view.Name))

flatten_z = 0.0
try:
    if target_view.GenLevel:
        flatten_z = target_view.GenLevel.Elevation
except Exception:
    pass

# 2 ── linked file
link_instance, link_doc = get_linked_file()
link_transform = link_instance.GetTotalTransform()
log("**Linked file:** {}".format(link_doc.Title))

# 3 ── collect Toposolids
toposolids = collect_toposolids(link_doc)
if not toposolids:
    forms.alert(
        "No Toposolids found in '{}'.".format(link_doc.Title),
        exitscript=True,
    )
log("**Toposolids found:** {}".format(len(toposolids)))

host_ids = set()      # automatic host skipping removed - you choose the types

# 4 ── choose which Toposolid types to convert
toposolids, chosen_type_names = pick_topo_types(toposolids, link_doc)
log("**Converting types:** {}".format(
    ", ".join(chosen_type_names)))
log("**Toposolids in selected types:** {}".format(
    len(toposolids)))

# 5 ── existing filled-region types (nothing new is ever created)
fr_type_dict = get_fr_type_dict()
if not fr_type_dict:
    forms.alert("No Filled Region types in the project.", exitscript=True)

sorted_names = sorted(fr_type_dict.keys())
unmatched = [n for n in chosen_type_names
             if match_fr_type(n, fr_type_dict)[0] is None]

default_fr_type = None
default_name = None
if unmatched:
    SKIP = "<Skip types with no matching name>"
    preview = ", ".join(unmatched[:4]) + (" ..." if len(unmatched) > 4 else "")
    pick = forms.SelectFromList.show(
        [SKIP] + sorted_names,
        title="No Filled Region type matches: {}  -  use which one?"
              .format(preview),
        button_name="OK",
    )
    if pick and pick != SKIP:
        default_name = pick
        default_fr_type = fr_type_dict[pick]

# 6 ── process each Toposolid
created  = 0
failed   = 0
skipped  = 0
host_skipped = 0
no_type  = 0
dedup    = GlobalDedup(tol=0.15)

log("---")
log("## Processing …")
log("")

with revit.Transaction("Topo → Filled Region"):
    for idx, topo in enumerate(toposolids):
        topo_type_name = "?"
        try:
            # ── type name
            topo_type = link_doc.GetElement(topo.GetTypeId())
            topo_type_name = (
                _element_name(topo_type) if topo_type else "Unknown"
            )

            # ── skip host/parent Toposolids ──────────────────────────
            if _eid(topo.Id) in host_ids:
                log(
                    "- 🚫 **{}** — host/parent, skipped (ID {})"
                    .format(topo_type_name, _eid(topo.Id))
                )
                host_skipped += 1
                continue

            # ── match filled-region type
            matched_type, matched_name, score = match_fr_type(
                topo_type_name, fr_type_dict
            )
            if matched_type:
                use_type = matched_type
                use_name = matched_name
                status   = "✅ Matched (score {})".format(score)
            elif default_fr_type is not None:
                use_type = default_fr_type
                use_name = default_name
                status   = "⚠️ No match → chosen type"
            else:
                log("- ⏭️ **{}** — no matching filled region type, skipped"
                    .format(topo_type_name))
                no_type += 1
                continue

            unmask_fr_type(use_type)

            # ── boundary curves
            loops = get_topo_boundaries(
                topo, link_doc, link_transform, z=flatten_z
            )
            if not loops:
                log(
                    "- ❌ **{}** — boundary extraction failed"
                    .format(topo_type_name)
                )
                failed += 1
                continue

            # ── one filled region PER separate boundary
            made = 0
            for group in split_into_regions(loops):
                outer = group[0]
                if dedup.is_duplicate([outer]):
                    skipped += 1
                    continue
                FilledRegion.Create(
                    doc, use_type.Id, target_view.Id, List[CurveLoop](group))
                dedup.register([outer])
                made += 1
                created += 1
            if made:
                log("- {} **{}** → **{}**  ({} separate region{})"
                    .format(status, topo_type_name, use_name, made,
                            "" if made == 1 else "s"))
            else:
                log("- 🔄 **{}** — duplicate boundary, skipped"
                    .format(topo_type_name))

        except Exception as exc:
            failed += 1
            log(
                "- ❌ **{}** — {}".format(topo_type_name, str(exc))
            )
            logger.debug(traceback.format_exc())

# 7 ── summary
log("---")
log("## Summary")
log("- ✅ Created: **{}** filled region(s)".format(created))
if host_skipped:
    log(
        "- 🚫 Host/parent skipped: **{}**".format(host_skipped)
    )
if skipped:
    log("- 🔄 Duplicate skipped: **{}**".format(skipped))
if no_type:
    log("- ⏭️ No matching type, skipped: **{}**".format(no_type))
if failed:
    log("- ❌ Failed: **{}**".format(failed))
log("- View: **{}**".format(target_view.Name))
if default_name:
    log("- Fallback type: **{}**".format(default_name))

flush_report()