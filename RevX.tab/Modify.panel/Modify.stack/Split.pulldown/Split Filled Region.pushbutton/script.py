# -*- coding: utf-8 -*-
"""Split Filled Region into Individual Regions
Splits a single Filled Region with multiple separate boundaries 
into individual Filled Region elements, strictly preserving all boundary line styles.
Compatible with Revit 2023-2027.
"""

__title__ = "Split\nFilled Region"
__author__ = "Jesto Joy"
__doc__ = "Split a Filled Region with multiple boundaries into separate Filled Regions preserving line styles."

import clr
import sys

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


class FilledRegionSelectionFilter(ISelectionFilter):
    """Selection filter that only allows Filled Regions."""
    def AllowElement(self, element):
        return isinstance(element, DB.FilledRegion)
    
    def AllowReference(self, reference, position):
        return False


def get_sketch_curves(filled_region):
    """
    Extracts all CurveElement (SketchCurve) objects belonging to a Filled Region's sketch.
    Works across Revit 2023-2027.
    """
    curves = []
    try:
        sketch_id = filled_region.SketchId
        if sketch_id and sketch_id != DB.ElementId.InvalidElementId:
            sketch = doc.GetElement(sketch_id)
            if sketch:
                if hasattr(sketch, "GetAllElements"):
                    for eid in sketch.GetAllElements():
                        elem = doc.GetElement(eid)
                        if isinstance(elem, DB.CurveElement):
                            curves.append(elem)
                            
                if not curves:
                    for eid in sketch.GetDependentElements(None):
                        elem = doc.GetElement(eid)
                        if isinstance(elem, DB.CurveElement):
                            curves.append(elem)
    except Exception as ex:
        logger.debug("Sketch curve extraction failed: {}".format(str(ex)))

    if not curves:
        try:
            for eid in filled_region.GetDependentElements(None):
                elem = doc.GetElement(eid)
                if isinstance(elem, DB.CurveElement):
                    curves.append(elem)
                elif isinstance(elem, DB.Sketch):
                    if hasattr(elem, "GetAllElements"):
                        for ceid in elem.GetAllElements():
                            celem = doc.GetElement(ceid)
                            if isinstance(celem, DB.CurveElement):
                                curves.append(celem)
        except Exception as ex:
            logger.debug("Dependent curve extraction failed: {}".format(str(ex)))
            
    return curves


def get_curve_line_style_id(curve_elem):
    """Gets the LineStyle GraphicsStyle ElementId from a SketchCurve element."""
    try:
        if curve_elem.LineStyle:
            return curve_elem.LineStyle.Id
    except:
        pass
        
    for bip in [DB.BuiltInParameter.BUILDING_CURVE_GSTYLE, 
                DB.BuiltInParameter.CURVE_ELEM_SUBCATEGORY]:
        try:
            p = curve_elem.get_Parameter(bip)
            if p and p.HasValue:
                return p.AsElementId()
        except:
            pass
    return None


def set_curve_line_style(curve_elem, style_id):
    """Applies a LineStyle GraphicsStyle ElementId to a SketchCurve element."""
    if not style_id or style_id == DB.ElementId.InvalidElementId:
        return
        
    try:
        style_elem = doc.GetElement(style_id)
        if style_elem:
            curve_elem.LineStyle = style_elem
    except:
        pass
        
    for bip in [DB.BuiltInParameter.BUILDING_CURVE_GSTYLE, 
                DB.BuiltInParameter.CURVE_ELEM_SUBCATEGORY]:
        try:
            p = curve_elem.get_Parameter(bip)
            if p and not p.IsReadOnly:
                p.Set(style_id)
        except:
            pass


def curves_match(c1, c2, tol=0.02):
    """Checks if two 3D curves match in position and shape."""
    p1_s, p1_e = c1.GetEndPoint(0), c1.GetEndPoint(1)
    p2_s, p2_e = c2.GetEndPoint(0), c2.GetEndPoint(1)

    direct = (p1_s.DistanceTo(p2_s) < tol and p1_e.DistanceTo(p2_e) < tol)
    reverse = (p1_s.DistanceTo(p2_e) < tol and p1_e.DistanceTo(p2_s) < tol)

    if direct or reverse:
        if isinstance(c1, DB.Arc) or isinstance(c2, DB.Arc):
            m1 = c1.Evaluate(0.5, True)
            m2 = c2.Evaluate(0.5, True)
            return m1.DistanceTo(m2) < tol
        return True
    return False


def project_pt_to_view_uv(pt, view):
    """Projects a 3D point onto the view plane (U, V axes)."""
    right = view.RightDirection
    up = view.UpDirection
    origin = view.Origin
    vec = pt - origin
    u = vec.DotProduct(right)
    v = vec.DotProduct(up)
    return (u, v)


def is_point_inside_loop_in_view(point_3d, curve_loop, view):
    """2D ray casting containment in the owner view plane."""
    try:
        segments_uv = []
        for curve in curve_loop:
            p0 = curve.GetEndPoint(0)
            p1 = curve.GetEndPoint(1)
            u0, v0 = project_pt_to_view_uv(p0, view)
            u1, v1 = project_pt_to_view_uv(p1, view)
            segments_uv.append(((u0, v0), (u1, v1)))
        
        pu, pv = project_pt_to_view_uv(point_3d, view)
        inside = False
        
        for ((u0, v0), (u1, v1)) in segments_uv:
            if ((v0 > pv) != (v1 > pv)):
                u_intersect = u0 + (pv - v0) * (u1 - u0) / (v1 - v0)
                if pu < u_intersect:
                    inside = not inside
        
        return inside
    except:
        return False


def classify_loops(boundary_loops, view):
    """Classifies loops into outer islands and inner holes using the view plane."""
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
            if is_point_inside_loop_in_view(ld1['centroid'], ld2['loop'], view):
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


def copy_parameters(source, target):
    """Copies instance parameters and comments."""
    try:
        params_to_copy = [
            DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS,
            DB.BuiltInParameter.ALL_MODEL_MARK,
        ]
        for bip in params_to_copy:
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


def pick_filled_region():
    """Prompts user to select a Filled Region."""
    try:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element,
            FilledRegionSelectionFilter(),
            "Select a Filled Region to split"
        )
        if ref:
            return doc.GetElement(ref.ElementId)
    except OperationCanceledException:
        return None
    except:
        return None
    return None


def main():
    # Step 1: Select element
    filled_region = pick_filled_region()
    if not filled_region:
        return
    
    # Step 2: Confirmation dialog for deleting original
    result = forms.alert(
        "Do you want to DELETE the original Filled Region after splitting?\n\n"
        "• YES → Delete the original\n"
        "• NO → Keep the original Filled Region",
        title="Split Filled Region",
        yes=True,
        no=True
    )
    delete_original = bool(result)
    
    view = doc.GetElement(filled_region.OwnerViewId)
    
    # Map original line styles before editing
    original_sketch_curves = get_sketch_curves(filled_region)
    original_styles_map = []
    for sc in original_sketch_curves:
        try:
            g_curve = sc.GeometryCurve
            s_id = get_curve_line_style_id(sc)
            if s_id:
                original_styles_map.append({
                    'curve': g_curve,
                    'style_id': s_id
                })
        except:
            pass
            
    boundaries = list(filled_region.GetBoundaries())
    if not boundaries:
        return
    
    classified = classify_loops(boundaries, view)
    if len(classified) <= 1:
        return
    
    type_id = filled_region.GetTypeId()
    view_id = filled_region.OwnerViewId
    new_regions = []

    # Wrap both operations in a TransactionGroup for undo support & proper refresh
    tg = DB.TransactionGroup(doc, "Split Filled Region")
    tg.Start()

    try:
        # TRANSACTION 1: Create new elements and copy properties
        t1 = DB.Transaction(doc, "Create Split Regions")
        t1.Start()
        for outer_loop, hole_loops in classified:
            try:
                loop_list = List[DB.CurveLoop]()
                loop_list.Add(outer_loop)
                for hole in hole_loops:
                    loop_list.Add(hole)
                
                new_fr = DB.FilledRegion.Create(doc, type_id, view_id, loop_list)
                if new_fr:
                    copy_parameters(filled_region, new_fr)
                    new_regions.append(new_fr)
            except Exception as ex:
                logger.debug("Failed creating region segment: {}".format(str(ex)))
        t1.Commit()

        # TRANSACTION 2: Apply line styles & clean up (Regenerate inside the active transaction)
        t2 = DB.Transaction(doc, "Apply Line Styles & Cleanup")
        t2.Start()
        doc.Regenerate()  # Safe inside transaction
        
        if original_styles_map:
            for new_fr in new_regions:
                new_sketch_curves = get_sketch_curves(new_fr)
                for nsc in new_sketch_curves:
                    try:
                        n_curve = nsc.GeometryCurve
                        matched_style_id = None
                        for orig in original_styles_map:
                            if curves_match(n_curve, orig['curve']):
                                matched_style_id = orig['style_id']
                                break
                        
                        if matched_style_id:
                            set_curve_line_style(nsc, matched_style_id)
                    except Exception as ex:
                        logger.debug("Failed applying line style: {}".format(str(ex)))
        
        # Safely delete original if requested
        if delete_original and len(new_regions) == len(classified):
            try:
                doc.Delete(filled_region.Id)
            except Exception as ex:
                logger.debug("Failed deleting original filled region: {}".format(str(ex)))
                
        doc.Regenerate()
        t2.Commit()

        tg.Assimilate()

        # Force Revit active view to repaint so line styles display immediately
        try:
            uidoc.RefreshActiveView()
        except:
            pass

    except Exception as ex:
        tg.RollBack()
        logger.error("Split Filled Region failed: {}".format(str(ex)))


if __name__ == "__main__":
    main()