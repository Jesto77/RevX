# -*- coding: utf-8 -*-
"""Align Edges - RevX.extension

Reimplements the workflow shown for FOREground's "Align Edges" tool:

    1. Pick a slab (Floor/Toposolid) to modify.
    2. Choose an Align Method - what kind of neighboring element to match:
       Slabs, Walls, Curbs, or Stairs.
    3. Pick one or more of those neighboring elements.
    4. Optional Points Offset (feet).

Any shape-edit point on the target slab that sits at the same XY location
as an edge of a picked neighbor ("coincident in the xy dimension") gets its
elevation snapped to follow that neighbor's height/slope.

This is an original implementation built directly against the public Revit
API - not a decompilation of FOREground's code (which I don't have access
to), and the dialog is branded as this extension's own tool. Some specific
choices (which face of a Wall/Curb/Stair counts as the reference) are my
best-effort design decisions, documented in README.md, since I don't have
FOREground's exact internal logic to copy.

Tested against the Revit API surface for 2023-2025. 2026/2027 are handled
defensively but not verified past this script's knowledge cutoff - test on
a throwaway file first. See get_shape_editor() / get_reference_curves().
"""

from pyrevit import revit, DB, forms, script
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException

try:
    from Autodesk.Revit.DB import Architecture as DB_Arch
except Exception:
    DB_Arch = None

try:
    from System.Collections.Generic import List
except Exception:
    List = None

doc = revit.doc
uidoc = revit.uidoc

# --- version-defensive class lookups ---------------------------------------
TOPOSOLID_CLASS = getattr(DB, "Toposolid", None)  # only exists from 2024 on
RAILING_CLASS = (getattr(DB_Arch, "Railing", None) if DB_Arch else None) or \
    getattr(DB, "Railing", None)
STAIRS_CLASSES = tuple(
    c for c in [
        getattr(DB, "Stairs", None),
        getattr(DB_Arch, "Stairs", None) if DB_Arch else None,
    ] if c is not None
)

EDGE_TOLERANCE = 0.05      # feet (~0.6") - how close counts as "coincident in XY"
MIN_MOVE = 1e-6
NORMAL_THRESHOLD = 0.7     # how vertical a face normal must be to count as top/bottom

ALIGN_METHODS = ["Slabs", "Walls", "Curbs", "Stairs"]


# ---------------------------------------------------------------- filters --
class SlabFilter(ISelectionFilter):
    """Used for step 1 - the target slab to modify."""
    def AllowElement(self, element):
        if isinstance(element, DB.Floor):
            return True
        if TOPOSOLID_CLASS is not None and isinstance(element, TOPOSOLID_CLASS):
            return True
        return False

    def AllowReference(self, reference, position):
        return True


class MethodFilter(ISelectionFilter):
    """Used for step 3 - adjacent elements, filtered by the chosen method."""
    def __init__(self, method):
        self.method = method

    def AllowElement(self, element):
        if self.method == "Slabs":
            return isinstance(element, DB.Floor) or (
                TOPOSOLID_CLASS is not None and isinstance(element, TOPOSOLID_CLASS)
            )
        if self.method == "Walls":
            return isinstance(element, DB.Wall)
        if self.method == "Curbs":
            return RAILING_CLASS is not None and isinstance(element, RAILING_CLASS)
        if self.method == "Stairs":
            return isinstance(element, STAIRS_CLASSES)
        return False

    def AllowReference(self, reference, position):
        return True


# ------------------------------------------------------------- geometry ---
def get_solids(element):
    opt = DB.Options()
    opt.ComputeReferences = True
    solids = []
    geom = element.get_Geometry(opt)
    if geom is None:
        return solids
    for obj in geom:
        if isinstance(obj, DB.Solid) and obj.Volume > 1e-9:
            solids.append(obj)
        elif isinstance(obj, DB.GeometryInstance):
            try:
                inst_geom = obj.GetInstanceGeometry()
            except Exception:
                continue
            for inst_obj in inst_geom:
                if isinstance(inst_obj, DB.Solid) and inst_obj.Volume > 1e-9:
                    solids.append(inst_obj)
    return solids


def get_face_edge_curves(element, direction):
    """direction: +1 = upward-facing faces (tops), -1 = downward-facing (bottoms)."""
    curves = []
    for solid in get_solids(element):
        for face in solid.Faces:
            try:
                normal = face.ComputeNormal(DB.UV(0.5, 0.5))
            except Exception:
                continue
            if direction > 0 and normal.Z < NORMAL_THRESHOLD:
                continue
            if direction < 0 and normal.Z > -NORMAL_THRESHOLD:
                continue
            for loop in face.EdgeLoops:
                for edge in loop:
                    try:
                        curves.append(edge.AsCurve())
                    except Exception:
                        continue
    return curves


def get_reference_curves(element, align_method):
    """The 3D curves on `element` that a target slab point could be
    coincident with. Design choices (documented in README):
      - Slabs / Stairs: top-facing edges (slab top boundary / tread nosings)
      - Curbs: the railing's actual TopRail path if available, else its
        top-facing edges as a fallback approximation
      - Walls: bottom-facing edges (wall base) - the assumption being a
        slab meets a wall at its base, not its top
    """
    if align_method == "Curbs" and RAILING_CLASS is not None and isinstance(element, RAILING_CLASS):
        try:
            top_rail_id = element.GetTopRail()
            top_rail_elem = doc.GetElement(top_rail_id)
            path = list(top_rail_elem.GetPath())
            if path:
                return path
        except Exception:
            pass  # fall through to face-edge approximation below

    if align_method == "Walls":
        return get_face_edge_curves(element, direction=-1)

    return get_face_edge_curves(element, direction=1)


def flatten_curve(curve):
    """Projects curve onto Z=0 so we can find the XY match point regardless
    of how much elevation varies along it."""
    if isinstance(curve, DB.Line):
        p0, p1 = curve.GetEndPoint(0), curve.GetEndPoint(1)
        return DB.Line.CreateBound(DB.XYZ(p0.X, p0.Y, 0.0), DB.XYZ(p1.X, p1.Y, 0.0))
    try:
        pts = curve.Tessellate()
        flat_pts = [DB.XYZ(p.X, p.Y, 0.0) for p in pts]
        if List is not None:
            flat_pts = List[DB.XYZ](flat_pts)
        return DB.HermiteSpline.Create(flat_pts, False)
    except Exception:
        return None


def elevation_at_xy(ref_curve, flat_ref_curve, x, y):
    """Projects (x,y) onto the flattened reference curve, then evaluates the
    ORIGINAL (unflattened) curve at the equivalent normalized parameter to
    recover the true elevation/slope at that spot."""
    result = flat_ref_curve.Project(DB.XYZ(x, y, 0.0))
    if result is None:
        return None
    t0, t1 = flat_ref_curve.GetEndParameter(0), flat_ref_curve.GetEndParameter(1)
    span = t1 - t0
    norm = (result.Parameter - t0) / span if span else 0.0
    norm = min(max(norm, 0.0), 1.0)  # clamp - don't extrapolate past the ends
    ot0, ot1 = ref_curve.GetEndParameter(0), ref_curve.GetEndParameter(1)
    orig_param = ot0 + norm * (ot1 - ot0)
    return ref_curve.Evaluate(orig_param, False).Z


def get_shape_editor(element):
    """Tries GetSlabShapeEditor() then the older SlabShapeEditor property -
    which one exists has shifted across Revit API versions."""
    get_method = getattr(element, "GetSlabShapeEditor", None)
    if callable(get_method):
        try:
            editor = get_method()
            if editor is not None:
                return editor
        except Exception:
            pass
    return getattr(element, "SlabShapeEditor", None)


def ensure_enabled(editor):
    if not getattr(editor, "IsEnabled", True):
        enable = getattr(editor, "Enable", None)
        if callable(enable):
            try:
                enable()
            except Exception:
                pass


# -------------------------------------------------------------- core op ---
def align_slab(target_elem, adjacent_elements, align_method, offset):
    """Returns the number of shape points moved."""
    ref_pairs = []
    for adj in adjacent_elements:
        for c in get_reference_curves(adj, align_method):
            flat = flatten_curve(c)
            if flat is not None:
                ref_pairs.append((c, flat))

    editor = get_shape_editor(target_elem)
    if editor is None:
        return 0
    ensure_enabled(editor)

    moved = 0
    for pt in editor.SlabShapeVertices:
        pos = pt.Position
        best_pair, best_dist = None, EDGE_TOLERANCE
        for orig_c, flat_c in ref_pairs:
            try:
                d = flat_c.Distance(DB.XYZ(pos.X, pos.Y, 0.0))
            except Exception:
                continue
            if d < best_dist:
                best_dist, best_pair = d, (orig_c, flat_c)
        if best_pair is None:
            continue
        new_z = elevation_at_xy(best_pair[0], best_pair[1], pos.X, pos.Y)
        if new_z is None:
            continue
        new_z += offset
        delta = new_z - pos.Z
        if abs(delta) > MIN_MOVE:
            editor.ModifyPoint(pt, delta)
            moved += 1
    return moved


# ------------------------------------------------------------------- UI ---
class AlignEdgesWindow(forms.WPFWindow):
    def __init__(self, xaml_file):
        forms.WPFWindow.__init__(self, xaml_file)
        self.target_elem = None
        self.adjacent_elems = []
        # Set the default selection here (post-load) rather than via
        # SelectedIndex in XAML - setting it in XAML fires SelectionChanged
        # mid-parse, before later-declared named elements (btn_pick_adjacent)
        # are bound onto self, which throws a MissingMemberException.
        self.cmb_method.SelectedIndex = 0
        self._refresh_state()

    @property
    def align_method(self):
        item = self.cmb_method.SelectedItem
        return item.Content if item else "Slabs"

    def _refresh_state(self):
        self.btn_pick_adjacent.IsEnabled = self.target_elem is not None
        self.btn_run.IsEnabled = (
            self.target_elem is not None and len(self.adjacent_elems) > 0
        )

    def pick_slab_click(self, sender, args):
        self.Hide()
        try:
            ref = uidoc.Selection.PickObject(
                ObjectType.Element, SlabFilter(), "Pick the slab to modify"
            )
            self.target_elem = doc.GetElement(ref)
            self.btn_pick_slab.Content = "Slab: {}".format(self.target_elem.Id)
            self.adjacent_elems = []
            self.btn_pick_adjacent.Content = "Pick Element(s)"
        except OperationCanceledException:
            pass
        except Exception as ex:
            forms.alert(
                "Pick Slab failed:\n\n{}".format(ex),
                title="Align Edges - Error"
            )
        finally:
            self._refresh_state()
            self.Show()

    def method_changed(self, sender, args):
        self.adjacent_elems = []
        self.btn_pick_adjacent.Content = "Pick Element(s)"
        self._refresh_state()

    def pick_adjacent_click(self, sender, args):
        self.Hide()
        try:
            refs = uidoc.Selection.PickObjects(
                ObjectType.Element, MethodFilter(self.align_method),
                "Pick adjacent {} elements, then click Finish".format(self.align_method)
            )
            self.adjacent_elems = [doc.GetElement(r) for r in refs]
            self.btn_pick_adjacent.Content = "{} picked".format(len(self.adjacent_elems))
        except OperationCanceledException:
            pass
        except Exception as ex:
            forms.alert(
                "Pick Element(s) failed:\n\n{}".format(ex),
                title="Align Edges - Error"
            )
        finally:
            self._refresh_state()
            self.Show()

    def help_click(self, sender, args):
        forms.alert(
            "1. Pick the slab (Floor/Toposolid) you want to re-grade.\n"
            "2. Choose what kind of neighboring element to align to.\n"
            "3. Pick one or more of those neighboring elements.\n"
            "4. Optionally add a constant Points Offset (feet).\n\n"
            "Any shape point on the target slab that sits at the same XY "
            "location as an edge of a picked neighbor will have its "
            "elevation snapped to match that neighbor's height/slope.",
            title="Align Edges - Help"
        )

    def run_click(self, sender, args):
        try:
            offset = float(self.txt_offset.Text) if self.txt_offset.Text else 0.0
        except ValueError:
            forms.alert("Points Offset must be a number.", title="Align Edges")
            return

        try:
            with revit.Transaction("Align Edges"):
                moved = align_slab(self.target_elem, self.adjacent_elems, self.align_method, offset)
        except Exception as ex:
            forms.alert("Run failed:\n\n{}".format(ex), title="Align Edges - Error")
            return

        forms.alert("Aligned {} point(s).".format(moved), title="Align Edges")

        if self.chk_keep_open.IsChecked:
            self.adjacent_elems = []
            self.btn_pick_adjacent.Content = "Pick Element(s)"
            self._refresh_state()
        else:
            self.Close()

    def close_click(self, sender, args):
        self.Close()


if __name__ == "__main__":
    xaml_path = script.get_bundle_file("ui.xaml")
    AlignEdgesWindow(xaml_path).ShowDialog()
