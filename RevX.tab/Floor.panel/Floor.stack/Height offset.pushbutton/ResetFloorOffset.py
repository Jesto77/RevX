# -*- coding: utf-8 -*-
# Height Offset (Keep Points Fixed)
# Author: Jesto Joy

import clr
try:
    clr.AddReference("RevitAPI")
    clr.AddReference("RevitAPIUI")
except Exception:
    pass

from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from pyrevit import revit, DB, forms, script

logger = script.get_logger()
doc = revit.doc
uidoc = revit.uidoc

TOL = 1e-5


def _toposolid_type():
    arch = getattr(DB, "Architecture", None)
    return getattr(arch, "Toposolid", None) if arch else None


def is_supported(elem):
    if elem is None:
        return False
    if isinstance(elem, DB.Floor):
        return True
    if isinstance(elem, DB.RoofBase):
        return True
    topo_cls = _toposolid_type()
    if topo_cls is not None and isinstance(elem, topo_cls):
        return True
    return False


class _SupportedFilter(ISelectionFilter):
    def AllowElement(self, e):
        return is_supported(e)

    def AllowReference(self, ref, point):
        return False


def get_slab_shape_editor(elem):
    sse = None
    if hasattr(elem, "SlabShapeEditor"):
        try:
            sse = elem.SlabShapeEditor
        except Exception:
            sse = None
    if sse is None and hasattr(elem, "GetSlabShapeEditor"):
        try:
            sse = elem.GetSlabShapeEditor()
        except Exception:
            sse = None
    return sse


def collect_points(sse):
    pts = []
    for v in sse.SlabShapeVertices:
        pos = v.Position
        pts.append((pos.X, pos.Y, pos.Z))
    return pts


def find_old_z(x, y, old_pts):
    for (ox, oy, oz) in old_pts:
        if abs(ox - x) < TOL and abs(oy - y) < TOL:
            return oz
    return None


def compensate_points(elem, old_points):
    sse = get_slab_shape_editor(elem)
    if sse is None:
        return

    verts = list(sse.SlabShapeVertices)
    if not verts:
        return

    probe_vertex = verts[0]
    probe_pos = probe_vertex.Position
    probe_value = 0.0
    sse.ModifySubElement(probe_vertex, probe_value)
    doc.Regenerate()

    sse2 = get_slab_shape_editor(elem)
    if sse2 is None:
        return
    verts2 = list(sse2.SlabShapeVertices)

    calib_after_z = None
    for v in verts2:
        p = v.Position
        if abs(p.X - probe_pos.X) < TOL and abs(p.Y - probe_pos.Y) < TOL:
            calib_after_z = p.Z
            break
    if calib_after_z is None:
        return

    constant_c = calib_after_z - probe_value

    for v in verts2:
        p = v.Position
        old_z = find_old_z(p.X, p.Y, old_points)
        if old_z is None:
            continue
        target_value = old_z - constant_c
        try:
            sse2.ModifySubElement(v, target_value)
        except Exception as ex:
            logger.debug("vertex adjust failed on {}: {}".format(elem.Id, ex))

    doc.Regenerate()


def get_height_offset_param(elem):
    if isinstance(elem, DB.Floor):
        bip_names = ["FLOOR_HEIGHTABOVELEVEL_PARAM"]
        ptid_names = ["FloorHeightabovelevelParam"]
        disp_names = ["Height Offset From Level"]
    elif isinstance(elem, DB.RoofBase):
        bip_names = ["ROOF_LEVEL_OFFSET_PARAM", "ROOF_CONSTRAINT_OFFSET_PARAM"]
        ptid_names = ["RoofLevelOffsetParam", "RoofConstraintOffsetParam"]
        disp_names = ["Base Offset From Level", "Height Offset From Level"]
    else:
        bip_names = ["TOPOSOLID_HEIGHTABOVELEVEL_PARAM"]
        ptid_names = ["ToposolidHeightabovelevelParam"]
        disp_names = ["Height Offset From Level"]

    for bip_name in bip_names:
        bip = getattr(DB.BuiltInParameter, bip_name, None)
        if bip is not None:
            try:
                p = elem.get_Parameter(bip)
                if p is not None:
                    return p
            except Exception:
                pass

    ptid_cls = getattr(DB, "ParameterTypeId", None)
    if ptid_cls is not None:
        for ptid_name in ptid_names:
            ftid = getattr(ptid_cls, ptid_name, None)
            if ftid is not None:
                try:
                    p = elem.get_Parameter(ftid)
                    if p is not None:
                        return p
                except Exception:
                    pass

    for name in disp_names:
        p = elem.LookupParameter(name)
        if p is not None:
            return p

    return None


selected_ids = uidoc.Selection.GetElementIds()
elements = [e for e in (doc.GetElement(eid) for eid in selected_ids) if is_supported(e)]

if not elements:
    try:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element,
            _SupportedFilter(),
            "Select a Floor, Roof or Toposolid"
        )
        picked = doc.GetElement(ref.ElementId)
        if is_supported(picked):
            elements = [picked]
    except Exception:
        script.exit()

if not elements:
    script.exit()

units = doc.GetUnits()
length_fo = units.GetFormatOptions(DB.SpecTypeId.Length)
unit_type_id = length_fo.GetUnitTypeId()
unit_label = DB.LabelUtils.GetLabelForUnit(unit_type_id)

first_param = get_height_offset_param(elements[0])
current_display = 0.0
if first_param is not None:
    current_display = DB.UnitUtils.ConvertFromInternalUnits(first_param.AsDouble(), unit_type_id)

user_input = forms.ask_for_string(
    default=str(round(current_display, 4)),
    prompt="New Height/Base Offset From Level value ({}):".format(unit_label),
    title="Height Offset (Keep Points Fixed)"
)

if not user_input:
    script.exit()

try:
    new_value_display = float(user_input.strip())
except ValueError:
    script.exit()

new_value_internal = DB.UnitUtils.ConvertToInternalUnits(new_value_display, unit_type_id)

tg = DB.TransactionGroup(doc, "Set Offset From Level - Keep Points Fixed")
tg.Start()

for elem in elements:
    try:
        with revit.Transaction("Set offset - id {}".format(elem.Id)):
            ho_param = get_height_offset_param(elem)
            if ho_param is None or ho_param.IsReadOnly:
                continue

            sse = get_slab_shape_editor(elem)
            editor_active = sse is not None and getattr(sse, "IsEnabled", True)

            old_points = []
            if editor_active:
                try:
                    old_points = collect_points(sse)
                except Exception as ex:
                    logger.debug("read points failed on {}: {}".format(elem.Id, ex))
                    editor_active = False

            ho_param.Set(new_value_internal)
            doc.Regenerate()

            if editor_active and old_points:
                compensate_points(elem, old_points)

    except Exception as ex:
        logger.debug("element {} failed: {}".format(elem.Id, ex))

tg.Assimilate()