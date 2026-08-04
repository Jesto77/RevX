# -*- coding: utf-8 -*-

from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.DB import *
from pyrevit import revit, script

uidoc = revit.uidoc
doc = revit.doc


# ----------------------------------------------------------
# FIND OR CREATE 3D VIEW
# ----------------------------------------------------------

def get_3d_view():

    views = FilteredElementCollector(doc)\
        .OfClass(View3D)\
        .ToElements()

    # Use existing non-template 3D view
    for v in views:
        if not v.IsTemplate:
            return v

    # Create new isometric view
    vft = FilteredElementCollector(doc)\
        .OfClass(ViewFamilyType)

    for x in vft:
        if x.ViewFamily == ViewFamily.ThreeDimensional:

            t = Transaction(doc, "Create 3D View")
            t.Start()

            view3d = View3D.CreateIsometric(doc, x.Id)
            view3d.Name = "PyRevit_Linked_Element_View"

            t.Commit()

            return view3d

    return None


# ----------------------------------------------------------
# PICK LINKED ELEMENT
# ----------------------------------------------------------

try:
    ref = uidoc.Selection.PickObject(
        ObjectType.LinkedElement,
        "Pick linked element"
    )
except:
    script.exit()


# ----------------------------------------------------------
# GET LINK + ELEMENT
# ----------------------------------------------------------

link_instance = doc.GetElement(ref.ElementId)

if not isinstance(link_instance, RevitLinkInstance):
    print("Invalid linked element.")
    script.exit()

link_doc = link_instance.GetLinkDocument()

if not link_doc:
    print("Could not access linked document.")
    script.exit()

linked_element = link_doc.GetElement(ref.LinkedElementId)

if not linked_element:
    print("Linked element not found.")
    script.exit()


# ----------------------------------------------------------
# GET BOUNDING BOX
# ----------------------------------------------------------

bbox = linked_element.get_BoundingBox(None)

if not bbox:
    print("No bounding box found.")
    script.exit()

transform = link_instance.GetTransform()

min_pt = transform.OfPoint(bbox.Min)
max_pt = transform.OfPoint(bbox.Max)


# ----------------------------------------------------------
# CREATE SECTION BOX
# ----------------------------------------------------------

offset = 2.0  # feet

section_box = BoundingBoxXYZ()

section_box.Min = XYZ(
    min(min_pt.X, max_pt.X) - offset,
    min(min_pt.Y, max_pt.Y) - offset,
    min(min_pt.Z, max_pt.Z) - offset
)

section_box.Max = XYZ(
    max(min_pt.X, max_pt.X) + offset,
    max(min_pt.Y, max_pt.Y) + offset,
    max(min_pt.Z, max_pt.Z) + offset
)


# ----------------------------------------------------------
# GET 3D VIEW
# ----------------------------------------------------------

view3d = get_3d_view()

if not view3d:
    print("Could not create/find 3D view.")
    script.exit()


# ----------------------------------------------------------
# APPLY SECTION BOX
# ----------------------------------------------------------

t = Transaction(doc, "Section Box Linked Element")
t.Start()

view3d.IsSectionBoxActive = True
view3d.SetSectionBox(section_box)

t.Commit()


# ----------------------------------------------------------
# OPEN 3D VIEW
# ----------------------------------------------------------

uidoc.ActiveView = view3d
uidoc.RefreshActiveView()

print("Done.")