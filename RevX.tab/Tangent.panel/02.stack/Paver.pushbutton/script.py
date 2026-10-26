# -*- coding: utf-8 -*-
"""Floors + Toposolids (linked model) -> Filled Regions  |  pattern-matched.

Workflow
  1. Pick the linked model.
  2. Tick the Floor / Toposolid TYPES you want (everything in the link is
     listed - you decide).
  3. For every element of the ticked types a Filled Region is created in the
     active plan view, using a Filled Region TYPE that has the SAME NAME as
     the element type. If that type does not exist it is created, with the
     Surface Foreground / Background pattern + colour of the element type's
     top material (Model patterns stay Model, Drafting stay Drafting).
     An existing Filled Region type with that name is re-patterned to match.
"""

__title__  = "Link Floors/Topo\nto Filled Region"
__author__ = "Jesto Joy"
__doc__    = ("Select a linked model, choose Floor / Toposolid types and "
              "create filled regions named and patterned like them.")

import re
import traceback

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from System.Collections.Generic import List

from Autodesk.Revit.DB import (
    Arc,
    BuiltInCategory,
    Color,
    CurveLoop,
    ElementClassFilter,
    ElementId,
    FilledRegion,
    FilledRegionType,
    FillPatternElement,
    FillPatternTarget,
    FilteredElementCollector,
    Floor,
    GeometryInstance,
    Line,
    Options,
    PlanarFace,
    RevitLinkInstance,
    Sketch,
    Solid,
    ViewPlan,
    ViewSheet,
    XYZ,
)

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

try:
    from Autodesk.Revit.DB import Toposolid
    HAS_TOPOSOLID = True
except ImportError:
    Toposolid = None
    HAS_TOPOSOLID = False


# =====================================================================
#  GENERAL HELPERS
# =====================================================================

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
    it = loop.GetCurveLoopIterator()
    while it.MoveNext():
        yield it.Current


# =====================================================================
#  VIEW / LINK SELECTION
# =====================================================================

def _eid(eid):
    """ElementId -> int. Revit 2024+ uses .Value, older uses .IntegerValue."""
    try:
        return int(eid.Value)
    except AttributeError:
        return eid.IntegerValue


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


def collect_elements(link_doc):
    """All Floors and Toposolids in the link: list of (category, element)."""
    items = []
    try:
        for fl in (FilteredElementCollector(link_doc)
                   .OfClass(Floor).WhereElementIsNotElementType()
                   .ToElements()):
            items.append(("Floor", fl))
    except Exception as exc:
        logger.debug("Floor collect failed: {}".format(exc))
    if HAS_TOPOSOLID:
        try:
            for tp in (FilteredElementCollector(link_doc)
                       .OfClass(Toposolid).WhereElementIsNotElementType()
                       .ToElements()):
                items.append(("Toposolid", tp))
        except Exception as exc:
            logger.debug("Toposolid collect failed: {}".format(exc))
    return items


def _type_name(el, link_doc):
    try:
        t = link_doc.GetElement(el.GetTypeId())
        return _element_name(t) if t else "Unknown"
    except Exception:
        return "Unknown"


def pick_types(items, link_doc):
    """Return {(category, type name): [elements]} for the ticked types."""
    groups = {}
    for cat, el in items:
        groups.setdefault((cat, _type_name(el, link_doc)), []).append(el)

    labels = {}
    for key in sorted(groups.keys()):
        n = len(groups[key])
        label = "{} | {}   ({} element{})".format(
            key[0], key[1], n, "" if n == 1 else "s")
        labels[label] = key

    chosen = forms.SelectFromList.show(
        sorted(labels.keys()),
        title="Select Floor / Toposolid type(s) to convert",
        button_name="Create filled regions",
        multiselect=True,
    )
    if not chosen:
        script.exit()
    return dict((labels[l], groups[labels[l]]) for l in chosen)


# =====================================================================
#  FILLED REGION TYPE  (name + surface pattern from the element type)
# =====================================================================

_BAD_NAME_CHARS = re.compile(r'[\\{}\[\]|;<>?`~:]')


def _safe_name(name):
    n = _BAD_NAME_CHARS.sub("_", name or "Unnamed").strip()
    return n or "Unnamed"


def _valid_id(eid):
    return eid is not None and eid != ElementId.InvalidElementId


def _surface_ids(mat):
    """(fg id, fg colour, bg id, bg colour) - SURFACE patterns."""
    return (mat.SurfaceForegroundPatternId, mat.SurfaceForegroundPatternColor,
            mat.SurfaceBackgroundPatternId, mat.SurfaceBackgroundPatternColor)


def _cut_ids(mat):
    return (mat.CutForegroundPatternId, mat.CutForegroundPatternColor,
            mat.CutBackgroundPatternId, mat.CutBackgroundPatternColor)


def _has_pattern(ids):
    return _valid_id(ids[0]) or _valid_id(ids[2])


def _pick_material(elem_type, link_doc):
    """(material, layer no., 'surface'|'cut'|'none').

    Walks the layers from the top and takes the first material that really
    has a SURFACE pattern (so a plain top layer without pattern does not
    hide the patterned one). Falls back to a Cut pattern, then to the first
    material found."""
    mats = []
    try:
        cs = elem_type.GetCompoundStructure()
        if cs is not None:
            for i, layer in enumerate(cs.GetLayers()):
                if _valid_id(layer.MaterialId):
                    m = link_doc.GetElement(layer.MaterialId)
                    if m is not None:
                        mats.append((m, i + 1))
    except Exception:
        pass
    if not mats:
        try:
            from Autodesk.Revit.DB import BuiltInParameter
            p = elem_type.get_Parameter(BuiltInParameter.MATERIAL_ID_PARAM)
            if p is not None and _valid_id(p.AsElementId()):
                m = link_doc.GetElement(p.AsElementId())
                if m is not None:
                    mats.append((m, 0))
        except Exception:
            pass
    for m, n in mats:
        if _has_pattern(_surface_ids(m)):
            return m, n, "surface"
    for m, n in mats:
        if _has_pattern(_cut_ids(m)):
            return m, n, "cut"
    if mats:
        return mats[0][0], mats[0][1], "none"
    return None, 0, "none"


_host_pattern_cache = {}
_pattern_problems = []


def _host_pattern_id(link_doc, link_pattern_id):
    """Fill pattern of the LINK (Drafting OR Model) -> same pattern in HOST.

    Order: same name + SAME target (Model stays Model) -> create a
    copy in the host (renamed if the name is taken). Solid fill is matched
    by IsSolidFill, never by name."""
    if not _valid_id(link_pattern_id):
        return None
    key = _eid(link_pattern_id)
    if key in _host_pattern_cache:
        return _host_pattern_cache[key]
    result = None
    try:
        lp_el = link_doc.GetElement(link_pattern_id)
        if lp_el is None:
            raise Exception("pattern element not found in link")
        lp = lp_el.GetFillPattern()
        host = []
        for fe in FilteredElementCollector(doc).OfClass(FillPatternElement):
            try:
                host.append((fe, fe.GetFillPattern()))
            except Exception:
                pass

        if lp.IsSolidFill:
            for fe, hp in host:
                if hp.IsSolidFill and hp.Target == lp.Target:
                    result = fe.Id
                    break
        else:
            lname = (lp.Name or "").strip().lower()
            for fe, hp in host:
                if (not hp.IsSolidFill) and hp.Target == lp.Target \
                        and (hp.Name or "").strip().lower() == lname:
                    result = fe.Id
                    break

        if result is None:
            try:
                result = FillPatternElement.Create(doc, lp).Id
            except Exception:
                try:
                    lp.Name = "{} (link)".format(lp.Name)
                    result = FillPatternElement.Create(doc, lp).Id
                except Exception as exc2:
                    raise Exception("could not copy pattern '{}': {}"
                                    .format(lp.Name, exc2))
    except Exception as exc:
        _pattern_problems.append(str(exc))
        logger.debug("Pattern map failed: {}".format(exc))
    _host_pattern_cache[key] = result
    return result


_fr_types = {}


def _load_fr_types():
    for frt in FilteredElementCollector(doc).OfClass(FilledRegionType):
        _fr_types[_element_name(frt)] = frt


def _pat_label(pid):
    try:
        fp = doc.GetElement(pid).GetFillPattern()
        return "{} [{}]".format(fp.Name, fp.Target)
    except Exception:
        return "?"


def _safe_color(col):
    """Copy of a valid Color, else None."""
    try:
        if col is not None and col.IsValid:
            return Color(col.Red, col.Green, col.Blue)
    except Exception:
        pass
    return None


def _rgb(col):
    try:
        if col is not None and col.IsValid:
            return "{},{},{}".format(col.Red, col.Green, col.Blue)
    except Exception:
        pass
    return "invalid"


def get_or_create_fr_type(name, elem_type, link_doc, notes):
    """FilledRegionType named like the element type, patterned from it:
    foreground + background, each Model or Drafting exactly as the
    material has it.

    Pattern AND colour are ALWAYS written explicitly - nothing is left
    inherited from the type that was duplicated (that inherited colour is
    what showed up as magenta)."""
    name = _safe_name(name)
    existing = name in _fr_types
    if existing:
        new_t = _fr_types[name]           # re-patterned below
    else:
        if not _fr_types:
            raise Exception("No Filled Region type in the project to copy from.")
        base = None
        for t in _fr_types.values():
            try:
                if not t.IsMasking:
                    base = t
                    break
            except Exception:
                pass
        if base is None:
            base = list(_fr_types.values())[0]
        new_t = base.Duplicate(name)
        new_t = doc.GetElement(new_t) if isinstance(new_t, ElementId) else new_t

    # un-mask FIRST: a masking type does not accept the patterns below
    try:
        new_t.IsMasking = False
    except Exception:
        try:
            p = new_t.LookupParameter("Masking")
            if p is not None and not p.IsReadOnly:
                p.Set(0)
        except Exception as exc:
            notes.append("masking not changed: {}".format(exc))

    black = Color(0, 0, 0)
    invalid = ElementId.InvalidElementId
    del _pattern_problems[:]

    mat, layer_no, source = _pick_material(elem_type, link_doc)
    if mat is None:
        notes.append("no material found - pattern/colour reset to none/black")
        fg_id = bg_id = None
        fg_col = bg_col = None
    else:
        ids = _surface_ids(mat) if source != "cut" else _cut_ids(mat)
        fg_link, fg_col, bg_link, bg_col = ids
        fg_id = _host_pattern_id(link_doc, fg_link)
        bg_id = _host_pattern_id(link_doc, bg_link)
        if _valid_id(fg_link) and fg_id is None:
            notes.append("FG pattern could not be mapped")
        if _valid_id(bg_link) and bg_id is None:
            notes.append("BG pattern could not be mapped")
        if source == "none":
            notes.append("material has no surface/cut pattern")

    fg_c = _safe_color(fg_col)
    bg_c = _safe_color(bg_col)
    if fg_id is not None and fg_c is None:
        notes.append("FG colour invalid in link - black used")
    if bg_id is not None and bg_c is None:
        notes.append("BG colour invalid in link - black used")

    # pattern first, colour afterwards (colour is re-asserted at the end)
    try:
        new_t.ForegroundPatternId = fg_id if fg_id is not None else invalid
    except Exception as exc:
        notes.append("foreground pattern failed: {}".format(exc))
    try:
        new_t.BackgroundPatternId = bg_id if bg_id is not None else invalid
    except Exception as exc:
        notes.append("background pattern failed: {}".format(exc))
    for _ in range(2):
        try:
            new_t.ForegroundPatternColor = fg_c if fg_c is not None else black
        except Exception as exc:
            notes.append("foreground colour failed: {}".format(exc))
        try:
            new_t.BackgroundPatternColor = bg_c if bg_c is not None else black
        except Exception as exc:
            notes.append("background colour failed: {}".format(exc))

    # untick "Masking" on the type
    try:
        new_t.IsMasking = False
    except Exception:
        try:
            p = new_t.LookupParameter("Masking")
            if p is not None and not p.IsReadOnly:
                p.Set(0)
        except Exception as exc:
            notes.append("masking not changed: {}".format(exc))

    # read back what the type really has now
    try:
        notes.append("{} of '{}' (layer {}): FG = {} ({}), BG = {} ({})".format(
            source, _element_name(mat) if mat is not None else "-", layer_no,
            _pat_label(new_t.ForegroundPatternId)
            if _valid_id(new_t.ForegroundPatternId) else "none",
            _rgb(new_t.ForegroundPatternColor),
            _pat_label(new_t.BackgroundPatternId)
            if _valid_id(new_t.BackgroundPatternId) else "none",
            _rgb(new_t.BackgroundPatternColor)))
    except Exception:
        pass
    for pr in set(_pattern_problems):
        notes.append("pattern issue: {}".format(pr))

    _fr_types[name] = new_t
    notes.insert(0, "existing type UPDATED" if existing else "NEW type")
    return new_t


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
#  MAIN
# =====================================================================

log("# Link Floors / Toposolids -> Filled Regions")
log("---")

target_view = get_target_view()
log("**Target view:** {}".format(target_view.Name))

flatten_z = 0.0
try:
    if target_view.GenLevel:
        flatten_z = target_view.GenLevel.Elevation
except Exception:
    pass

link_instance, link_doc = get_linked_file()
link_transform = link_instance.GetTotalTransform()
log("**Linked file:** {}".format(link_doc.Title))

items = collect_elements(link_doc)
if not items:
    forms.alert("No Floors or Toposolids found in '{}'."
                .format(link_doc.Title), exitscript=True)

selected = pick_types(items, link_doc)
log("**Types chosen:** {}".format(len(selected)))
log("---")

created = 0
failed = 0
dups = 0
dedups = {}

with revit.Transaction("Link Floors/Topo -> Filled Regions"):
    _load_fr_types()
    for (cat, tname), elements in sorted(selected.items()):
        notes = []
        try:
            elem_type = link_doc.GetElement(elements[0].GetTypeId())
            fr_type = get_or_create_fr_type(tname, elem_type, link_doc, notes)
        except Exception as exc:
            failed += len(elements)
            log("- ❌ **{} | {}** - filled region type: {}"
                            .format(cat, tname, exc))
            continue

        dd = dedups.setdefault(tname, GlobalDedup(tol=0.15))
        made = 0
        for el in elements:
            try:
                loops = get_topo_boundaries(
                    el, link_doc, link_transform, z=flatten_z)
                if not loops:
                    failed += 1
                    log("  - ❌ boundary extraction failed "
                                    "(ID {})".format(_eid(el.Id)))
                    continue
                if dd.is_duplicate(loops):
                    dups += 1
                    continue
                FilledRegion.Create(
                    doc, fr_type.Id, target_view.Id, List[CurveLoop](loops))
                dd.register(loops)
                made += 1
                created += 1
            except Exception as exc:
                failed += 1
                log("  - ❌ ID {} - {}".format(_eid(el.Id), exc))
                logger.debug(traceback.format_exc())
        log("- **{} | {}** -> type **{}**: {} region(s)  _({})_"
                        .format(cat, tname, _safe_name(tname), made,
                                "; ".join(notes)))

log("---")
log("## Summary")
log("- ✅ Created: **{}** filled region(s)".format(created))
if dups:
    log("- 🔄 Duplicate boundaries skipped: **{}**".format(dups))
if failed:
    log("- ❌ Failed: **{}**".format(failed))
log("- View: **{}**".format(target_view.Name))

flush_report()