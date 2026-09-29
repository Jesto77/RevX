# -*- coding: utf-8 -*-
"""
Section Box Linked Element - PyRevit Tool
Picks an element inside a linked model and creates a tight 3D Section Box
around it in host coordinates, then focuses and zooms the camera on it.
"""

from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    View3D,
    ViewFamilyType,
    ViewFamily,
    BoundingBoxXYZ,
    XYZ,
    Transform,
    Transaction,
    RevitLinkInstance,
    BuiltInCategory,
    ElementId
)
from pyrevit import revit, script

uidoc = revit.uidoc
doc = revit.doc

# ----------------------------------------------------------
# 1. PICK LINKED ELEMENT
# ----------------------------------------------------------
try:
    ref = uidoc.Selection.PickObject(
        ObjectType.LinkedElement,
        "Pick a linked element to create Section Box"
    )
except Exception:
    script.exit()

if not ref:
    script.exit()

# ----------------------------------------------------------
# 2. GET LINK INSTANCE & ELEMENT
# ----------------------------------------------------------
link_instance = doc.GetElement(ref.ElementId)
if not isinstance(link_instance, RevitLinkInstance):
    print("Selected item is not from a linked Revit model.")
    script.exit()

link_doc = link_instance.GetLinkDocument()
if not link_doc:
    print("Could not access the linked document.")
    script.exit()

linked_element = link_doc.GetElement(ref.LinkedElementId)
if not linked_element:
    print("Linked element could not be found.")
    script.exit()

# ----------------------------------------------------------
# 3. ACCURATE 8-CORNER BOUNDING BOX TRANSFORMATION
# ----------------------------------------------------------
bbox = linked_element.get_BoundingBox(None)
if not bbox:
    opt = link_doc.Application.Create.NewGeometryOptions()
    geom = linked_element.get_Geometry(opt)
    if geom:
        bbox = geom.GetBoundingBox()

if not bbox:
    print("Could not determine a bounding box for the selected linked element.")
    script.exit()

# Get all 8 corners in linked model coordinates
corners = [
    XYZ(bbox.Min.X, bbox.Min.Y, bbox.Min.Z),
    XYZ(bbox.Max.X, bbox.Min.Y, bbox.Min.Z),
    XYZ(bbox.Min.X, bbox.Max.Y, bbox.Min.Z),
    XYZ(bbox.Max.X, bbox.Max.Y, bbox.Min.Z),
    XYZ(bbox.Min.X, bbox.Min.Y, bbox.Max.Z),
    XYZ(bbox.Max.X, bbox.Min.Y, bbox.Max.Z),
    XYZ(bbox.Min.X, bbox.Max.Y, bbox.Max.Z),
    XYZ(bbox.Max.X, bbox.Max.Y, bbox.Max.Z),
]

# Transform all 8 corners into host project coordinates
total_transform = link_instance.GetTotalTransform()
transformed_corners = [total_transform.OfPoint(pt) for pt in corners]

min_x = min(pt.X for pt in transformed_corners)
min_y = min(pt.Y for pt in transformed_corners)
min_z = min(pt.Z for pt in transformed_corners)

max_x = max(pt.X for pt in transformed_corners)
max_y = max(pt.Y for pt in transformed_corners)
max_z = max(pt.Z for pt in transformed_corners)

# Offset clearance buffer around element (1.5 feet ≈ 450 mm)
offset = 1.5

section_box = BoundingBoxXYZ()
section_box.Transform = Transform.Identity
section_box.Min = XYZ(min_x - offset, min_y - offset, min_z - offset)
section_box.Max = XYZ(max_x + offset, max_y + offset, max_z + offset)

# ----------------------------------------------------------
# 4. FIND OR CREATE USABLE 3D VIEW
# ----------------------------------------------------------
def get_or_create_3d_view():
    active_v = doc.ActiveView
    if isinstance(active_v, View3D) and not active_v.IsTemplate and not active_v.IsPerspective:
        return active_v

    views = FilteredElementCollector(doc).OfClass(View3D).ToElements()

    # Prefer default {3D} view or existing tool view
    for v in views:
        if not v.IsTemplate and not v.IsPerspective:
            if v.Name.startswith("{3D") or "SectionBox" in v.Name:
                return v

    # Any non-template isometric 3D view
    for v in views:
        if not v.IsTemplate and not v.IsPerspective:
            return v

    # Create new 3D view if none exist
    vft = FilteredElementCollector(doc).OfClass(ViewFamilyType)
    for x in vft:
        if x.ViewFamily == ViewFamily.ThreeDimensional:
            t_create = Transaction(doc, "Create Section Box 3D View")
            t_create.Start()
            new_view = View3D.CreateIsometric(doc, x.Id)
            new_view.Name = "Linked_Element_SectionBox"
            t_create.Commit()
            return new_view

    return None

view3d = get_or_create_3d_view()
if not view3d:
    print("Could not find or create a valid 3D view.")
    script.exit()

# ----------------------------------------------------------
# 5. APPLY SECTION BOX & UNHIDE CATEGORY
# ----------------------------------------------------------
t = Transaction(doc, "Section Box Linked Element")
t.Start()

try:
    # Unlock view if locked
    if view3d.IsLocked:
        view3d.Unlock()

    # Clear view template if assigned so Section Box can be set
    if view3d.ViewTemplateId and view3d.ViewTemplateId != ElementId.InvalidElementId:
        view3d.ViewTemplateId = ElementId.InvalidElementId

    # Enable and assign section box
    view3d.IsSectionBoxActive = True
    view3d.SetSectionBox(section_box)

    # Ensure Section Box annotation is visible
    try:
        cat_secbox = doc.Settings.Categories.get_Item(BuiltInCategory.OST_SectionBox)
        if cat_secbox:
            view3d.SetCategoryHidden(cat_secbox.Id, False)
    except:
        pass

    t.Commit()
except Exception as ex:
    if t.HasStarted() and not t.HasEnded():
        t.RollBack()
    print("Error applying Section Box: {}".format(ex))
    script.exit()

# ----------------------------------------------------------
# 6. SWITCH VIEW & ZOOM TO FIT SECTION BOX
# ----------------------------------------------------------
if uidoc.ActiveView.Id != view3d.Id:
    uidoc.ActiveView = view3d

for uiview in uidoc.GetOpenUIViews():
    if uiview.ViewId == view3d.Id:
        uiview.ZoomAndCenterRectangle(section_box.Min, section_box.Max)
        break

uidoc.RefreshActiveView()