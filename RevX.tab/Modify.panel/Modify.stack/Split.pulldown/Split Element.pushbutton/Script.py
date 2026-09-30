# -*- coding: utf-8 -*-
"""Split Element into Individual Elements
Splits Floors, Toposolids, Roofs, or Ceilings with multiple boundary loops 
into separate individual elements, preserving sub-element slopes/shapes.
Compatible with Revit 2023-2027.
"""

__title__ = "Split\nElement"
__author__ = "Jesto Joy"
__doc__ = "Split Floor/Toposolid/Roof/Ceiling into individual elements. Keeps sub-element points."

import clr
import sys
import traceback

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

import Autodesk.Revit.DB as DB
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException
from System.Collections.Generic import List

# pyrevit imports
from pyrevit import revit, script, forms

logger = script.get_logger()

doc = revit.doc
uidoc = revit.uidoc
app = doc.Application
version = int(app.VersionNumber)

SUPPORTED_CATEGORIES = [
    int(DB.BuiltInCategory.OST_Floors),
    int(DB.BuiltInCategory.OST_Roofs),
    int(DB.BuiltInCategory.OST_Ceilings),
]

# Add Toposolid (Revit 2024+)
try:
    SUPPORTED_CATEGORIES.append(int(DB.BuiltInCategory.OST_Toposolid))
except:
    pass


class ElementSelectionFilter(ISelectionFilter):
    """Selection filter for supported element categories."""
    def AllowElement(self, element):
        if element and element.Category:
            return element.Category.Id.IntegerValue in SUPPORTED_CATEGORIES
        return False
    
    def AllowReference(self, reference, position):
        return False


def get_element_type_key(element):
    """Return string key that identifies element type: floor/roof/ceiling/toposolid."""
    if not element or not element.Category:
        return None
    cat_id = element.Category.Id.IntegerValue
    if cat_id == int(DB.BuiltInCategory.OST_Floors):
        return "floor"
    if cat_id == int(DB.BuiltInCategory.OST_Roofs):
        return "roof"
    if cat_id == int(DB.BuiltInCategory.OST_Ceilings):
        return "ceiling"
    try:
        if cat_id == int(DB.BuiltInCategory.OST_Toposolid):
            return "toposolid"
    except:
        pass
    return None


def flatten_curve_2d(curve, target_z):
    """Flattens 3D curve vertically to target Z."""
    p0 = curve.GetEndPoint(0)
    p1 = curve.GetEndPoint(1)
    flat_p0 = DB.XYZ(p0.X, p0.Y, target_z)
    flat_p1 = DB.XYZ(p1.X, p1.Y, target_z)

    if isinstance(curve, DB.Line):
        if flat_p0.DistanceTo(flat_p1) < 0.001:
            return None
        return DB.Line.CreateBound(flat_p0, flat_p1)
    elif isinstance(curve, DB.Arc):
        pm = curve.Evaluate(0.5, True)
        flat_pm = DB.XYZ(pm.X, pm.Y, target_z)
        try:
            return DB.Arc.Create(flat_p0, flat_p1, flat_pm)
        except:
            return DB.Line.CreateBound(flat_p0, flat_p1)
    else:
        try:
            translation = DB.XYZ(0, 0, target_z - p0.Z)
            transform = DB.Transform.CreateTranslation(translation)
            return curve.CreateTransformed(transform)
        except:
            return DB.Line.CreateBound(flat_p0, flat_p1)


def are_curves_coincident_2d(c1, c2, tol=0.005):
    """Check if two 2D curves are the same segment."""
    p1_s, p1_e = c1.GetEndPoint(0), c1.GetEndPoint(1)
    p2_s, p2_e = c2.GetEndPoint(0), c2.GetEndPoint(1)

    direct = (p1_s.DistanceTo(p2_s) < tol and p1_e.DistanceTo(p2_e) < tol)
    reverse = (p1_s.DistanceTo(p2_e) < tol and p1_e.DistanceTo(p2_s) < tol)
    
    if direct or reverse:
        if isinstance(c1, DB.Arc) and isinstance(c2, DB.Arc):
            m1 = c1.Evaluate(0.5, True)
            m2 = c2.Evaluate(0.5, True)
            return m1.DistanceTo(m2) < tol
        return True
    return False


def stitch_curves_to_loops(curves, tol=0.01):
    """Stitch raw perimeter curves into closed CurveLoops."""
    loops = []
    unused = list(curves)
    
    while unused:
        current_curves = []
        curr = unused.pop(0)
        current_curves.append(curr)
        
        start_pt = curr.GetEndPoint(0)
        end_pt = curr.GetEndPoint(1)
        
        closed = False
        max_iter = len(unused) * 2 + 10
        it = 0
        
        while not closed and unused and it < max_iter:
            it += 1
            matched = False
            for i, other in enumerate(unused):
                op0 = other.GetEndPoint(0)
                op1 = other.GetEndPoint(1)
                
                if end_pt.DistanceTo(op0) <= tol:
                    current_curves.append(other)
                    end_pt = op1
                    unused.pop(i)
                    matched = True
                    break
                elif end_pt.DistanceTo(op1) <= tol:
                    rev_curve = other.CreateReversed()
                    current_curves.append(rev_curve)
                    end_pt = op0
                    unused.pop(i)
                    matched = True
                    break
            
            if end_pt.DistanceTo(start_pt) <= tol:
                closed = True
                break
                
            if not matched:
                break
        
        if len(current_curves) >= 3:
            try:
                if current_curves[0].GetEndPoint(0).DistanceTo(current_curves[-1].GetEndPoint(1)) <= tol:
                    cl = DB.CurveLoop()
                    for c in current_curves:
                        cl.Append(c)
                    loops.append(cl)
            except:
                pass
    return loops


def get_element_reference_level_z(element):
    """Get the base Z elevation for the element."""
    level_id = None
    try:
        level_id = element.LevelId
    except:
        pass
    if level_id is None or level_id == DB.ElementId.InvalidElementId:
        try:
            lp = element.get_Parameter(DB.BuiltInParameter.LEVEL_PARAM)
            if lp:
                level_id = lp.AsElementId()
        except:
            pass
    if level_id and level_id != DB.ElementId.InvalidElementId:
        base_level = doc.GetElement(level_id)
        if base_level:
            return base_level.Elevation, level_id
    return 0.0, None


def get_element_boundary_loops(element):
    """
    Topological perimeter extractor.
    Extracts true outer/hole boundaries from top face geometry, 
    ignoring internal split/crease lines. Works for all supported element types.
    """
    target_z, _ = get_element_reference_level_z(element)
    
    opt = DB.Options()
    opt.ComputeReferences = False
    opt.DetailLevel = DB.ViewDetailLevel.Fine
    
    geom = element.get_Geometry(opt)
    if not geom:
        return []

    top_faces = []
    for geom_obj in geom:
        if isinstance(geom_obj, DB.GeometryInstance):
            geom_obj = geom_obj.GetInstanceGeometry()
        if isinstance(geom_obj, DB.Solid) and geom_obj.Volume > 0.001:
            for face in geom_obj.Faces:
                bbox = face.GetBoundingBox()
                center_uv = bbox.Min + (bbox.Max - bbox.Min) * 0.5
                normal = face.ComputeNormal(center_uv)
                if normal.Z > 0.2:
                    top_faces.append(face)

    if not top_faces:
        return []

    all_raw_curves = []
    for face in top_faces:
        for loop in face.GetEdgesAsCurveLoops():
            for curve in loop:
                flat_c = flatten_curve_2d(curve, target_z)
                if flat_c:
                    all_raw_curves.append(flat_c)

    perimeter_curves = []
    counts = [0] * len(all_raw_curves)

    for i in range(len(all_raw_curves)):
        for j in range(len(all_raw_curves)):
            if i == j:
                continue
            if are_curves_coincident_2d(all_raw_curves[i], all_raw_curves[j]):
                counts[i] += 1

    for i, count in enumerate(counts):
        if count == 0:
            perimeter_curves.append(all_raw_curves[i])

    boundary_loops = stitch_curves_to_loops(perimeter_curves)
    return boundary_loops


def is_point_inside_loop(point, curve_loop):
    """2D ray-casting containment check."""
    try:
        segments = []
        for curve in curve_loop:
            p0 = curve.GetEndPoint(0)
            p1 = curve.GetEndPoint(1)
            segments.append((p0, p1))
        
        px, py = point.X, point.Y
        inside = False
        
        for (p0, p1) in segments:
            x0, y0 = p0.X, p0.Y
            x1, y1 = p1.X, p1.Y
            
            if ((y0 > py) != (y1 > py)):
                x_intersect = x0 + (py - y0) * (x1 - x0) / (y1 - y0)
                if px < x_intersect:
                    inside = not inside
        
        return inside
    except:
        return False


def classify_loops(boundary_loops):
    """Classify loops into outer islands and holes."""
    if not boundary_loops:
        return []
    
    if len(boundary_loops) == 1:
        return [(boundary_loops[0], [])]
    
    loop_data = []
    for i, loop in enumerate(boundary_loops):
        pts = [c.GetEndPoint(0) for c in loop]
        cx = sum(p.X for p in pts) / len(pts)
        cy = sum(p.Y for p in pts) / len(pts)
        cz = sum(p.Z for p in pts) / len(pts)
        centroid = DB.XYZ(cx, cy, cz)
        
        loop_data.append({
            'index': i,
            'loop': loop,
            'centroid': centroid,
            'containers': []
        })
        
    for i, ld1 in enumerate(loop_data):
        for j, ld2 in enumerate(loop_data):
            if i == j:
                continue
            if is_point_inside_loop(ld1['centroid'], ld2['loop']):
                ld1['containers'].append(j)
                
    outer_loops = []
    holes = []
    
    for i, ld in enumerate(loop_data):
        if len(ld['containers']) == 0:
            outer_loops.append((i, ld['loop']))
        else:
            holes.append((ld['loop'], ld['containers']))
            
    result = []
    for outer_idx, outer_loop in outer_loops:
        associated_holes = []
        for hole_loop, containers in holes:
            if outer_idx in containers:
                associated_holes.append(hole_loop)
        result.append((outer_loop, associated_holes))
        
    if not result and boundary_loops:
        return [(loop, []) for loop in boundary_loops]
        
    return result


def create_element_from_loop(element, type_key, outer_loop, hole_loops=None):
    """Creates a new element (Floor/Roof/Ceiling/Toposolid) using proper API."""
    type_id = element.GetTypeId()
    level_id = None
    try:
        level_id = element.LevelId
    except:
        pass
    if level_id is None or level_id == DB.ElementId.InvalidElementId:
        try:
            lp = element.get_Parameter(DB.BuiltInParameter.LEVEL_PARAM)
            if lp:
                level_id = lp.AsElementId()
        except:
            pass
    
    loop_list = List[DB.CurveLoop]()
    loop_list.Add(outer_loop)
    if hole_loops:
        for hole in hole_loops:
            loop_list.Add(hole)
    
    if type_key == "floor":
        return DB.Floor.Create(doc, loop_list, type_id, level_id)
    
    elif type_key == "toposolid":
        try:
            return DB.Toposolid.Create(doc, loop_list, type_id, level_id)
        except:
            return None
    
    elif type_key == "ceiling":
        try:
            return DB.Ceiling.Create(doc, loop_list, type_id, level_id)
        except:
            return None
    
    elif type_key == "roof":
        try:
            # FootPrintRoof uses CurveArray
            curve_array = DB.CurveArray()
            for c in outer_loop:
                curve_array.Append(c)
            
            level = doc.GetElement(level_id)
            
            # Get roof type
            roof_type = doc.GetElement(type_id)
            
            # Model curve mapping placeholder
            model_curve_array = DB.ModelCurveArray()
            
            new_roof = doc.Create.NewFootPrintRoof(curve_array, level, roof_type, model_curve_array)
            return new_roof
        except Exception as ex:
            logger.debug("Roof creation failed: {}".format(str(ex)))
            return None
    
    return None


def get_element_top_faces(element):
    """Extract 3D top faces for Z sampling."""
    top_faces = []
    opt = DB.Options()
    opt.ComputeReferences = False
    opt.DetailLevel = DB.ViewDetailLevel.Fine
    geom_elem = element.get_Geometry(opt)
    if geom_elem:
        for geom_obj in geom_elem:
            if isinstance(geom_obj, DB.GeometryInstance):
                geom_obj = geom_obj.GetInstanceGeometry()
            if isinstance(geom_obj, DB.Solid) and geom_obj.Volume > 0.001:
                for face in geom_obj.Faces:
                    bbox = face.GetBoundingBox()
                    center_uv = bbox.Min + (bbox.Max - bbox.Min) * 0.5
                    normal = face.ComputeNormal(center_uv)
                    if normal.Z > 0.2:
                        top_faces.append(face)
    return top_faces


def get_exact_z_from_original(point, orig_vertices, orig_creases, top_faces, default_z):
    """Sample precise elevation from original geometry."""
    px, py = point.X, point.Y
    
    for v in orig_vertices:
        v_pos = v.Position
        if (v_pos.X - px)**2 + (v_pos.Y - py)**2 < 0.0001:
            return v_pos.Z
            
    for crease in orig_creases:
        try:
            v1, v2 = crease.CreaseVertices[0], crease.CreaseVertices[1]
            p1, p2 = v1.Position, v2.Position
            dx, dy = p2.X - p1.X, p2.Y - p1.Y
            line_len_sq = dx*dx + dy*dy
            if line_len_sq > 0.000001:
                t = ((px - p1.X) * dx + (py - p1.Y) * dy) / line_len_sq
                if 0.0 <= t <= 1.0:
                    proj_x = p1.X + t * dx
                    proj_y = p1.Y + t * dy
                    if (px - proj_x)**2 + (py - proj_y)**2 < 0.0001:
                        return p1.Z + t * (p2.Z - p1.Z)
        except:
            pass
            
    for face in top_faces:
        try:
            test_pt = DB.XYZ(px, py, default_z)
            proj = face.Project(test_pt)
            if proj:
                proj_pt = proj.XYZPoint
                if (proj_pt.X - px)**2 + (proj_pt.Y - py)**2 < 0.0001:
                    return proj_pt.Z
        except:
            pass
            
    return default_z


def get_slab_shape_editor(element):
    """Universal SlabShapeEditor accessor for Floors/Roofs/Ceilings/Toposolids."""
    try:
        editor = element.SlabShapeEditor
        return editor
    except:
        return None


def copy_element_parameters(source, target):
    """Copies base parameters from source to target."""
    try:
        # Universal height offset
        for bip in [DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM,
                    DB.BuiltInParameter.CEILING_HEIGHTABOVELEVEL_PARAM,
                    DB.BuiltInParameter.ROOF_LEVEL_OFFSET_PARAM]:
            try:
                param = source.get_Parameter(bip)
                if param and not param.IsReadOnly:
                    tparam = target.get_Parameter(bip)
                    if tparam and not tparam.IsReadOnly:
                        tparam.Set(param.AsDouble())
            except:
                pass
        
        # Structural (floor)
        try:
            param_structural = source.get_Parameter(DB.BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL)
            if param_structural:
                target_param = target.get_Parameter(DB.BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL)
                if target_param and not target_param.IsReadOnly:
                    target_param.Set(param_structural.AsInteger())
        except:
            pass
        
        # Identity
        params_to_copy = [
            DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS,
            DB.BuiltInParameter.ALL_MODEL_MARK,
        ]
        for bip in params_to_copy:
            try:
                src_param = source.get_Parameter(bip)
                if src_param and src_param.HasValue:
                    tgt_param = target.get_Parameter(bip)
                    if tgt_param and not tgt_param.IsReadOnly:
                        if src_param.StorageType == DB.StorageType.String:
                            tgt_param.Set(src_param.AsString() or "")
                        elif src_param.StorageType == DB.StorageType.Double:
                            tgt_param.Set(src_param.AsDouble())
                        elif src_param.StorageType == DB.StorageType.Integer:
                            tgt_param.Set(src_param.AsInteger())
                        elif src_param.StorageType == DB.StorageType.ElementId:
                            tgt_param.Set(src_param.AsElementId())
            except:
                pass
    except:
        pass


def pick_element():
    """Prompts the user to pick a supported element."""
    try:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element,
            ElementSelectionFilter(),
            "Select a Floor / Toposolid / Roof / Ceiling to split"
        )
        if ref:
            return doc.GetElement(ref.ElementId)
    except OperationCanceledException:
        return None
    except:
        return None
    return None


def main():
    # Step 1: Pick element
    element = pick_element()
    if not element:
        return
    
    type_key = get_element_type_key(element)
    if not type_key:
        return
    
    # Step 2: Ask whether to keep or delete the original
    result = forms.alert(
        "Do you want to DELETE the original element after splitting?\n\n"
        "• YES → Delete the original\n"
        "• NO → Keep the original element",
        title="Split Element",
        yes=True,
        no=True
    )
    delete_original = bool(result)
    
    # Step 3: Perform the split
    with revit.Transaction("Split Element"):
        boundary_loops = get_element_boundary_loops(element)
        if not boundary_loops:
            return
        
        classified = classify_loops(boundary_loops)
        if len(classified) <= 1:
            return
        
        # Extract slab shape data
        orig_editor = get_slab_shape_editor(element)
        has_shapes = orig_editor is not None and orig_editor.IsEnabled
        orig_vertices = list(orig_editor.SlabShapeVertices) if has_shapes else []
        orig_creases = list(orig_editor.SlabShapeCreases) if has_shapes else []
        top_faces = get_element_top_faces(element) if has_shapes else []
        
        new_elems_data = []
        
        # Create split elements
        for i, (outer_loop, hole_loops) in enumerate(classified):
            try:
                new_elem = create_element_from_loop(element, type_key, outer_loop, hole_loops)
                if new_elem:
                    copy_element_parameters(element, new_elem)
                    new_elems_data.append((new_elem, outer_loop))
            except Exception as ex:
                logger.debug("Create element failed: {}".format(str(ex)))
        
        doc.Regenerate()
        
        # Reapply slab shape sub-elements
        if has_shapes:
            for new_elem, _ in new_elems_data:
                try:
                    editor = get_slab_shape_editor(new_elem)
                    if editor:
                        editor.Enable()
                except:
                    pass
            
            doc.Regenerate()
            
            # Interior drainage points
            for new_elem, outer_loop in new_elems_data:
                editor = get_slab_shape_editor(new_elem)
                if not editor:
                    continue
                for orig_v in orig_vertices:
                    pos = orig_v.Position
                    if is_point_inside_loop(pos, outer_loop):
                        try:
                            editor.DrawPoint(pos)
                        except:
                            pass
            
            doc.Regenerate()
            
            # Apply exact vertex elevations
            for new_elem, _ in new_elems_data:
                editor = get_slab_shape_editor(new_elem)
                if not editor:
                    continue
                
                flat_z = None
                if editor.SlabShapeVertices.Size > 0:
                    flat_z = list(editor.SlabShapeVertices)[0].Position.Z
                    
                if flat_z is None:
                    lvl_z, _ = get_element_reference_level_z(new_elem)
                    flat_z = lvl_z
                
                for vertex in editor.SlabShapeVertices:
                    pos = vertex.Position
                    target_z = get_exact_z_from_original(pos, orig_vertices, orig_creases, top_faces, pos.Z)
                    offset_value = target_z - flat_z
                    try:
                        editor.ModifySubElement(vertex, offset_value)
                    except:
                        pass
        
        # Delete original if selected
        if delete_original and len(new_elems_data) == len(classified):
            try:
                doc.Delete(element.Id)
            except:
                pass


if __name__ == "__main__":
    main()