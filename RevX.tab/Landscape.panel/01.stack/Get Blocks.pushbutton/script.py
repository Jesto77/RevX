# -*- coding: utf-8 -*-
 
from pyrevit import revit, forms, script

from Autodesk.Revit.DB import *

from Autodesk.Revit.UI.Selection import ObjectType

from Autodesk.Revit.DB.Structure import StructuralType

from System.Collections.Generic import List
 
doc = revit.doc

uidoc = revit.uidoc

view = doc.ActiveView
 
# =====================================================

# CHECK VIEW

# =====================================================
 
if not hasattr(view, "GenLevel") or view.GenLevel is None:

    forms.alert("Open a plan view.")

    script.exit()
 
level = view.GenLevel
 
# =====================================================

# PICK FAMILY INSTANCE

# =====================================================
 
try:

    fam_ref = uidoc.Selection.PickObject(

        ObjectType.Element,

        "Pick placed family instance"

    )
 
except:

    script.exit()
 
picked_element = doc.GetElement(fam_ref.ElementId)
 
if not isinstance(picked_element, FamilyInstance):

    forms.alert("Not a family instance.")

    script.exit()
 
family_symbol = picked_element.Symbol
 
# =====================================================

# PICK CAD

# =====================================================
 
try:

    cad_ref = uidoc.Selection.PickObject(

        ObjectType.Element,

        "Pick CAD Import"

    )
 
except:

    script.exit()
 
cad_import = doc.GetElement(cad_ref.ElementId)
 
if not isinstance(cad_import, ImportInstance):

    forms.alert("Not CAD.")

    script.exit()
 
# =====================================================

# GET LAYERS

# =====================================================
 
layers = []
 
cat = cad_import.Category
 
if cat and cat.SubCategories:
 
    for subcat in cat.SubCategories:

        layers.append(subcat.Name)
 
layers = sorted(list(set(layers)))
 
selected_layer = forms.SelectFromList.show(

    layers,

    title='Select Point Layer',

    multiselect=False

)
 
if not selected_layer:

    script.exit()
 
# =====================================================

# OPTIONS

# =====================================================
 
opt = Options()

opt.IncludeNonVisibleObjects = True
 
geo = cad_import.get_Geometry(opt)
 
points = []
 
# =====================================================

# RECURSIVE READER

# =====================================================
 
def process_geo(geo_element):
 
    for obj in geo_element:
 
        # ---------------------------------------------

        # NESTED

        # ---------------------------------------------

        if isinstance(obj, GeometryInstance):
 
            process_geo(obj.GetInstanceGeometry())
 
        # ---------------------------------------------

        # POINTS

        # ---------------------------------------------

        elif isinstance(obj, Point):
 
            try:
 
                gs = doc.GetElement(obj.GraphicsStyleId)
 
                if not gs:

                    continue
 
                layer_name = gs.GraphicsStyleCategory.Name
 
                if layer_name != selected_layer:

                    continue
 
                cad_pt = obj.Coord
 
                pt = XYZ(

                    cad_pt.X,

                    cad_pt.Y,

                    level.Elevation

                )
 
                duplicate = False
 
                for p in points:
 
                    if p.DistanceTo(pt) < 0.1:

                        duplicate = True

                        break
 
                if not duplicate:

                    points.append(pt)
 
            except:

                pass
 
 
# RUN

process_geo(geo)
 
# =====================================================

# CHECK

# =====================================================
 
if not points:

    forms.alert(

        "No AutoCAD POINT entities found.\n\n"

        "Use POINT command in AutoCAD."

    )

    script.exit()
 
# =====================================================

# PLACE

# =====================================================
 
t = Transaction(doc, "Place Families")

t.Start()
 
if not family_symbol.IsActive:

    family_symbol.Activate()

    doc.Regenerate()
 
placed_ids = List[ElementId]()
 
count = 0
 
for pt in points:
 
    try:
 
        inst = doc.Create.NewFamilyInstance(

            pt,

            family_symbol,

            level,

            StructuralType.NonStructural

        )
 
        placed_ids.Add(inst.Id)
 
        count += 1
 
    except:

        pass
 
t.Commit()
 
# =====================================================

# SELECT

# =====================================================
 
if count > 0:

    uidoc.Selection.SetElementIds(placed_ids)
 
forms.alert(

    "{} family instances placed.".format(count),

    title="Completed"

)
 