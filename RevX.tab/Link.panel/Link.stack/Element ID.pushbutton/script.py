# -*- coding: utf-8 -*-

from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.DB import RevitLinkInstance
from pyrevit import revit, script

uidoc = revit.uidoc
doc = revit.doc
output = script.get_output()


def get_linked_type_name(linked_doc, element):
    """
    Robust type name extractor for all Revit versions (2023–2026)
    """

    try:
        # Method 1: Standard type lookup
        type_id = element.GetTypeId()
        if type_id:
            el_type = linked_doc.GetElement(type_id)
            if el_type and hasattr(el_type, "Name"):
                return el_type.Name
    except:
        pass

    try:
        # Method 2: FamilyInstance symbol (VERY IMPORTANT FIX)
        if hasattr(element, "Symbol") and element.Symbol:
            return element.Symbol.FamilyName + " : " + element.Symbol.Name
    except:
        pass

    try:
        # Method 3: Direct element name
        if hasattr(element, "Name") and element.Name:
            return element.Name
    except:
        pass

    try:
        # Method 4: Category fallback (always works)
        if element.Category:
            return element.Category.Name
    except:
        pass

    return "Unknown Type"


try:
    refs = uidoc.Selection.PickObjects(
        ObjectType.LinkedElement,
        "Select linked elements"
    )

    table_data = []

    for ref in refs:

        link_instance = doc.GetElement(ref.ElementId)

        if not isinstance(link_instance, RevitLinkInstance):
            continue

        linked_doc = link_instance.GetLinkDocument()
        if linked_doc is None:
            continue

        linked_element_id = ref.LinkedElementId
        if not linked_element_id or linked_element_id.Value == -1:
            continue

        linked_element = linked_doc.GetElement(linked_element_id)
        if not linked_element:
            continue

        # -------------------------------
        # FIXED TYPE NAME LOGIC
        # -------------------------------
        type_name = get_linked_type_name(linked_doc, linked_element)

        table_data.append([
            link_instance.Name,
            ref.ElementId.Value,
            linked_element.Id.IntegerValue,
            type_name
        ])

    if table_data:
        output.print_table(
            table_data=table_data,
            title="Linked Elements with Type Names (Revit 2023–2026)",
            columns=[
                "Link Name",
                "Host Instance ID",
                "Linked Element ID",
                "Type Name"
            ]
        )
    else:
        print("No valid linked elements found.")

except Exception as e:
    print("Cancelled or failed:", str(e))