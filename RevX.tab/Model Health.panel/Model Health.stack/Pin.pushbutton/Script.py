import clr
clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import FilteredElementCollector, BuiltInCategory, Transaction, RevitLinkInstance, ImportInstance

# Get the current document
doc = __revit__.ActiveUIDocument.Document

# Collect all Grids, Levels, Revit Links, and CAD Links
grids = list(FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Grids).WhereElementIsNotElementType().ToElements())
levels = list(FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Levels).WhereElementIsNotElementType().ToElements())
revit_links = list(FilteredElementCollector(doc).OfClass(RevitLinkInstance).ToElements())

# Collect CAD Links (ImportInstance includes both linked and imported CAD files)
all_cad = list(FilteredElementCollector(doc).OfClass(ImportInstance).ToElements())
# Separate linked CAD from imported CAD
cad_links = [c for c in all_cad if c.IsLinked]
cad_imports = [c for c in all_cad if not c.IsLinked]

# Combine all elements to pin
elements_to_pin = grids + levels + revit_links + cad_links

total_grids = len(grids)
total_levels = len(levels)
total_rvt_links = len(revit_links)
total_cad_links = len(cad_links)
total_cad_imports = len(cad_imports)
total_elements = len(elements_to_pin)

# Check if there are any elements
if total_elements == 0:
    print("No Grids, Levels, Revit Links, or CAD Links found in the project.")
else:
    # Check how many are already pinned
    already_pinned = [el for el in elements_to_pin if el.Pinned]
    unpinned = [el for el in elements_to_pin if not el.Pinned]

    if len(already_pinned) == total_elements:
        print("All {0} elements are already pinned!\n".format(total_elements))
        print("  - Grids:       {0}".format(total_grids))
        print("  - Levels:      {0}".format(total_levels))
        print("  - Revit Links: {0}".format(total_rvt_links))
        print("  - CAD Links:   {0}".format(total_cad_links))
        if total_cad_imports > 0:
            print("\n  Note: {0} imported CAD file(s) found (not linked). "
                  "These were also pinned.".format(total_cad_imports))
    else:
        # Start a transaction to modify the database
        t = Transaction(doc, "Pin All Grids, Levels, Revit Links, and CAD Links")
        t.Start()

        count = 0
        for el in unpinned:
            el.Pinned = True
            count += 1

        t.Commit()

        # Get individual counts of newly pinned
        newly_pinned_grids = len([el for el in grids if el in unpinned])
        newly_pinned_levels = len([el for el in levels if el in unpinned])
        newly_pinned_rvt_links = len([el for el in revit_links if el in unpinned])
        newly_pinned_cad_links = len([el for el in cad_links if el in unpinned])

        # Get individual counts of already pinned
        already_pinned_grids = total_grids - newly_pinned_grids
        already_pinned_levels = total_levels - newly_pinned_levels
        already_pinned_rvt_links = total_rvt_links - newly_pinned_rvt_links
        already_pinned_cad_links = total_cad_links - newly_pinned_cad_links

        print("========== PIN SUMMARY ==========\n")
        print("Category       | Total | Newly Pinned | Already Pinned")
        print("---------------|-------|-------------|---------------")
        print("Grids          | {0:>5} | {1:>11} | {2:>14}".format(
            total_grids, newly_pinned_grids, already_pinned_grids))
        print("Levels         | {0:>5} | {1:>11} | {2:>14}".format(
            total_levels, newly_pinned_levels, already_pinned_levels))
        print("Revit Links    | {0:>5} | {1:>11} | {2:>14}".format(
            total_rvt_links, newly_pinned_rvt_links, already_pinned_rvt_links))
        print("CAD Links      | {0:>5} | {1:>11} | {2:>14}".format(
            total_cad_links, newly_pinned_cad_links, already_pinned_cad_links))
        print("---------------|-------|-------------|---------------")
        print("TOTAL          | {0:>5} | {1:>11} | {2:>14}".format(
            total_elements, count, len(already_pinned)))
        print("\nAll elements are now pinned!")

        if total_cad_imports > 0:
            print("\nNote: {0} imported CAD file(s) detected (not linked). "
                  "Consider converting them to linked CAD for better performance.".format(total_cad_imports))

print("\n=================================")
print("Script completed successfully!")