# -*- coding: utf-8 -*-

from pyrevit import revit, forms, script

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ParameterFilterElement,
    ElementId
)

output = script.get_output()

# ==========================================================
# DOCUMENTS
# ==========================================================

app = revit.doc.Application

docs = []

for d in app.Documents:
    if not d.IsFamilyDocument:
        docs.append(d)

if len(docs) < 2:
    forms.alert(
        "Open at least two Revit projects.",
        exitscript=True
    )

# ==========================================================
# SOURCE DOCUMENT
# ==========================================================

source_name = forms.SelectFromList.show(
    sorted([d.Title for d in docs]),
    title="Select SOURCE Project",
    multiselect=False
)

if not source_name:
    script.exit()

source_doc = None

for d in docs:
    if d.Title == source_name:
        source_doc = d
        break

# ==========================================================
# DESTINATION DOCUMENT
# ==========================================================

dest_name = forms.SelectFromList.show(
    sorted([
        d.Title for d in docs
        if d.Title != source_name
    ]),
    title="Select DESTINATION Project",
    multiselect=False
)

if not dest_name:
    script.exit()

dest_doc = None

for d in docs:
    if d.Title == dest_name:
        dest_doc = d
        break

# ==========================================================
# SOURCE FILTERS
# ==========================================================

source_filters = list(
    FilteredElementCollector(source_doc)
    .OfClass(ParameterFilterElement)
)

if not source_filters:
    forms.alert(
        "No filters found.",
        exitscript=True
    )

filter_dict = {}

for f in source_filters:
    try:
        filter_dict[f.Name] = f
    except:
        pass

selected = forms.SelectFromList.show(
    sorted(filter_dict.keys()),
    title="Select Filters",
    multiselect=True,
    button_name="Transfer"
)

if not selected:
    script.exit()

# ==========================================================
# EXISTING FILTERS
# ==========================================================

existing_names = set()

for f in FilteredElementCollector(dest_doc).OfClass(ParameterFilterElement):
    try:
        existing_names.add(f.Name)
    except:
        pass

# ==========================================================
# TRANSFER
# ==========================================================

success = []
failed = []
skipped = []

with revit.Transaction(
    "Transfer Filters",
    doc=dest_doc
):

    for name in selected:

        try:

            if name in existing_names:
                skipped.append(name)
                continue

            src_filter = filter_dict[name]

            categories = src_filter.GetCategories()

            rule_data = src_filter.GetElementFilter()

            new_filter = ParameterFilterElement.Create(
                dest_doc,
                src_filter.Name,
                categories
            )

            try:
                new_filter.SetElementFilter(rule_data)
            except:
                pass

            success.append(name)

        except Exception as ex:

            failed.append(
                "{} --> {}".format(
                    name,
                    str(ex)
                )
            )

# ==========================================================
# REPORT
# ==========================================================

output.print_md("# Filter Transfer Report")

output.print_md(
    "## Success ({})".format(
        len(success)
    )
)

for s in success:
    output.print_md("✔ {}".format(s))

if skipped:

    output.print_md(
        "\n## Skipped ({})".format(
            len(skipped)
        )
    )

    for s in skipped:
        output.print_md("• {}".format(s))

if failed:

    output.print_md(
        "\n## Failed ({})".format(
            len(failed)
        )
    )

    for f in failed:
        output.print_md("✘ {}".format(f))

forms.alert(
    "Transferred: {}\nSkipped: {}\nFailed: {}".format(
        len(success),
        len(skipped),
        len(failed)
    )
)