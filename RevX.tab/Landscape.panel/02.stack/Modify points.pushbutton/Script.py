# -*- coding: utf-8 -*-
"""
Remove Interior Shape Editing Points (Blue Points)
--------------------------------------------------
Deletes all blue interior points from Toposolids / Floors
and keeps only the green boundary points.
"""

__title__ = "Remove Modify Points"
__author__ = "Jesto Joy"
__doc__ = "Deletes all blue interior points from selected Toposolid/Floor and preserves green boundary points."

import sys
import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

import Autodesk.Revit.DB as DB
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import forms, revit

doc = revit.doc
uidoc = revit.uidoc


def get_shape_editor(elem):
    """Retrieve SlabShapeEditor from element if available."""
    try:
        if hasattr(elem, "GetSlabShapeEditor"):
            return elem.GetSlabShapeEditor()
        elif hasattr(elem, "SlabShapeEditor"):
            return elem.SlabShapeEditor
    except Exception:
        pass
    return None


class ShapeEditableFilter(ISelectionFilter):
    """Selection filter for elements with SlabShapeEditor enabled."""
    def AllowElement(self, elem):
        editor = get_shape_editor(elem)
        return editor is not None

    def AllowReference(self, ref, point):
        return False


def main():
    # 1. Get Current Selection or Prompt User
    selection_ids = uidoc.Selection.GetElementIds()
    selected_elems = [doc.GetElement(eid) for eid in selection_ids]
    
    target_elems = [e for e in selected_elems if get_shape_editor(e) is not None]

    if not target_elems:
        try:
            refs = uidoc.Selection.PickObjects(
                ObjectType.Element,
                ShapeEditableFilter(),
                "Select Toposolid(s) or Floor(s) to clean, then press Finish."
            )
            target_elems = [doc.GetElement(r.ElementId) for r in refs]
        except OperationCanceledException:
            sys.exit()

    if not target_elems:
        forms.alert("No valid Toposolid or Floor selected.", exitscript=True)

    # 2. Process Elements
    total_interior_removed = 0
    total_boundary_kept = 0
    processed_count = 0

    with DB.Transaction(doc, "Remove Interior Blue Points") as tx:
        tx.Start()
        
        for elem in target_elems:
            editor = get_shape_editor(elem)
            if not editor:
                continue

            # Enable shape editing if disabled
            if hasattr(editor, "IsEnabled") and not editor.IsEnabled:
                try:
                    editor.IsEnabled = True
                except Exception:
                    pass

            vertices = list(editor.SlabShapeVertices)
            if not vertices:
                continue

            # Separate boundary points (green) and interior points (blue)
            boundary_pts = []
            interior_count = 0

            for v in vertices:
                if v.VertexType == DB.SlabShapeVertexType.Interior:
                    interior_count += 1
                else:
                    # Corner / Edge boundary points
                    boundary_pts.append(v.Position)

            if interior_count == 0:
                continue

            # Reset shape editing (clears all interior points & split lines)
            editor.ResetSlabShape()

            # Re-draw boundary points with original 3D elevations
            for pt in boundary_pts:
                try:
                    editor.DrawPoint(pt)
                except Exception:
                    pass

            total_interior_removed += interior_count
            total_boundary_kept += len(boundary_pts)
            processed_count += 1

        tx.Commit()

    # 3. Summary Report
    if processed_count > 0:
        forms.alert(
            "Cleaned {} element(s) successfully!\n\n"
            "• Blue Interior Points Deleted: {}\n"
            "• Green Boundary Points Kept: {}".format(
                processed_count, total_interior_removed, total_boundary_kept
            ),
            title="Success",
            warn_icon=False
        )
    else:
        forms.alert(
            "No blue interior points found on the selected element(s).",
            title="Info"
        )


if __name__ == "__main__":
    main()