# -*- coding: utf-8 -*-
"""Reset shape of selected floors with detailed feedback"""

__title__ = 'Reset Floor Shapes'
__author__ = 'Your Name'
__doc__ = 'Resets shape editing for selected floors'

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument

# Get selected elements
selection = uidoc.Selection.GetElementIds()

if not selection:
    TaskDialog.Show("Error", "Please select floors first")
    sys.exit(0)

# Filter for floors only
floors = []
for elem_id in selection:
    elem = doc.GetElement(elem_id)
    if isinstance(elem, Floor):
        floors.append(elem)

if not floors:
    TaskDialog.Show("Error", "No floors found in selection")
    sys.exit(0)

# Start transaction
t = Transaction(doc, "Reset Floor Shapes")
t.Start()

success_count = 0
no_edit_count = 0
error_count = 0

for floor in floors:
    try:
        # Get the SlabShapeEditor
        slabShapeEditor = floor.GetSlabShapeEditor()
        
        # Check if shape editing is enabled using IsEnabled property
        if slabShapeEditor.IsEnabled:
            slabShapeEditor.ResetSlabShape()
            success_count += 1
        else:
            no_edit_count += 1
    except Exception as e:
        error_count += 1
        print("Error on floor {}: {}".format(floor.Id.IntegerValue, str(e)))

t.Commit()

# Show results
TaskDialog.Show("Complete", 
                "Successfully reset: {} floor(s)\n"
                "No shape editing: {} floor(s)\n"
                "Errors: {} floor(s)".format(success_count, no_edit_count, error_count))