# -*- coding: utf-8 -*-

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ObjectType
from System.Collections.Generic import List

doc = revit.doc
uidoc = revit.uidoc

# ----------------------------------------------------------
# PICK MODEL LINES
# ----------------------------------------------------------

try:

    refs = uidoc.Selection.PickObjects(
        ObjectType.Element,
        "Select model lines"
    )

except:
    script.exit()

ids = List[ElementId]()

for ref in refs:

    el = doc.GetElement(ref.ElementId)

    if isinstance(el, ModelCurve):

        ids.Add(el.Id)

if ids.Count == 0:

    forms.alert("No model lines selected.")
    script.exit()

# ----------------------------------------------------------
# OFFSET INPUT
# ----------------------------------------------------------

val = forms.ask_for_string(
    default="1000",
    prompt="Enter elevation offset in mm"
)

if not val:
    script.exit()

try:

    offset_mm = float(val)

    # mm to feet
    offset_ft = offset_mm / 304.8

except:

    forms.alert("Invalid value")
    script.exit()

# ----------------------------------------------------------
# TRANSACTION
# ----------------------------------------------------------

t = Transaction(doc, "Elevate Model Lines")

t.Start()

try:

    move_vector = XYZ(0, 0, offset_ft)

    copied_ids = ElementTransformUtils.CopyElements(
        doc,
        ids,
        move_vector
    )

    # delete originals
    doc.Delete(ids)

    t.Commit()

    forms.alert(
        str(copied_ids.Count)
        + " model lines elevated by "
        + str(offset_mm)
        + " mm"
    )

except Exception as e:

    t.RollBack()

    forms.alert(str(e))