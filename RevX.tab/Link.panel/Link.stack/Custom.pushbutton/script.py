# -*- coding: utf-8 -*-
"""Copy RVT Link display overrides from one view to one or more views/templates.

Copies:
- Link visibility type (Custom)
- ALL category overrides (Model, Annotation, Analytical, Import)
  including visibility, halftone, line weight, color, pattern, transparency
- Category visibility (on/off)

Workflow:
1. Select link(s)
2. Select source view
3. Choose target type: View Templates / Floor Plans / Ceiling Plans /
   3D Views / Sections / Elevations / Area Plans
4. Select target(s) from filtered list
5. Apply overrides

Handles views with or without view templates:
- If target has no template (or template doesn't control RVT Links): writes to view
- If target has a template controlling RVT Links: writes to the template directly

Requires Revit 2018+ (View.GetLinkOverrides / SetLinkOverrides API).
"""

from pyrevit import revit, DB, forms, script
import Autodesk.Revit.DB as RDB

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()


# ─────────────────────────────────────────────────────────────────────
# SAFE ELEMENT ID TO INT
# ─────────────────────────────────────────────────────────────────────

def eid_to_int(element_id):
    """Convert ElementId to int - handles both old and new Revit API."""
    try:
        return element_id.IntegerValue
    except AttributeError:
        pass
    try:
        return element_id.Value
    except AttributeError:
        pass
    try:
        return int(element_id.ToString())
    except Exception:
        pass
    return id(element_id)


# ─────────────────────────────────────────────────────────────────────
# FIND LinkVisibilityType ENUM
# ─────────────────────────────────────────────────────────────────────

def find_link_visibility_type():
    try:
        lvt = DB.LinkVisibilityType
        _ = lvt.Custom
        return lvt
    except AttributeError:
        pass
    try:
        lvt = RDB.LinkVisibilityType
        _ = lvt.Custom
        return lvt
    except AttributeError:
        pass
    try:
        s = RDB.RevitLinkGraphicsSettings()
        return type(s.LinkVisibilityType)
    except Exception:
        pass
    try:
        import clr
        clr.AddReference("RevitAPI")
        import Autodesk
        lvt = Autodesk.Revit.DB.LinkVisibilityType
        _ = lvt.Custom
        return lvt
    except Exception:
        pass
    return None


LinkVisibilityType = find_link_visibility_type()

if LinkVisibilityType is None:
    forms.alert("Cannot find LinkVisibilityType enum. Script cannot proceed.",
                exitscript=True)


# ─────────────────────────────────────────────────────────────────────
# SAFE NAME HELPER
# ─────────────────────────────────────────────────────────────────────

def safe_name(element):
    if element is None:
        return "<None>"
    try:
        name_param = element.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if name_param and name_param.HasValue:
            return name_param.AsString()
    except Exception:
        pass
    try:
        return element.Name
    except Exception:
        pass
    try:
        return DB.Element.Name.__get__(element)
    except Exception:
        pass
    try:
        return element.get_Name()
    except Exception:
        pass
    try:
        name_param = element.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
        if name_param and name_param.HasValue:
            return name_param.AsString()
    except Exception:
        pass
    return "Id:{}".format(element.Id)


# ─────────────────────────────────────────────────────────────────────
# VIEW TYPE DEFINITIONS - Which views support RVT Links
# ─────────────────────────────────────────────────────────────────────

# View types that DO NOT support RVT Link overrides (no V/G Revit Links tab)
EXCLUDED_VIEW_TYPES = {
    DB.ViewType.Schedule,
    DB.ViewType.DrawingSheet,
    DB.ViewType.Legend,
    DB.ViewType.DraftingView,       # Drafting views have no model/links
    DB.ViewType.ProjectBrowser,
    DB.ViewType.SystemBrowser,
    DB.ViewType.Internal,
    DB.ViewType.Undefined,
    DB.ViewType.Report,
    DB.ViewType.CostReport,
    DB.ViewType.LoadsReport,
    DB.ViewType.PresureLossReport,
    DB.ViewType.ColumnSchedule,
    DB.ViewType.PanelSchedule,
    DB.ViewType.Walkthrough,
    DB.ViewType.Rendering,
}

# Friendly names for the target type chooser
TARGET_TYPE_CHOICES = {
    "View Templates":   "TEMPLATES",
    "Floor Plans":      DB.ViewType.FloorPlan,
    "Ceiling Plans":    DB.ViewType.CeilingPlan,
    "3D Views":         DB.ViewType.ThreeD,
    "Sections":         DB.ViewType.Section,
    "Elevations":       DB.ViewType.Elevation,
    "Area Plans":       DB.ViewType.AreaPlan,
    "Detail Views":     DB.ViewType.Detail,
    "Engineering Plans": DB.ViewType.EngineeringPlan,
}


# ─────────────────────────────────────────────────────────────────────
# CORE HELPERS
# ─────────────────────────────────────────────────────────────────────

def is_custom(settings):
    if settings is None:
        return False
    try:
        return settings.LinkVisibilityType == LinkVisibilityType.Custom
    except Exception:
        try:
            return int(settings.LinkVisibilityType) == 2
        except Exception:
            return False


def make_custom_settings():
    s = RDB.RevitLinkGraphicsSettings()
    try:
        s.LinkVisibilityType = LinkVisibilityType.Custom
    except Exception:
        try:
            s.LinkVisibilityType = LinkVisibilityType(2)
        except Exception:
            pass
    return s


def link_label(link):
    return safe_name(link)


def get_link_type(link_inst):
    try:
        tid = link_inst.GetTypeId()
        if tid and tid != DB.ElementId.InvalidElementId:
            return doc.GetElement(tid)
    except Exception:
        pass
    return None


def get_linked_doc(link_inst):
    try:
        return link_inst.GetLinkDocument()
    except Exception:
        return None


def get_all_link_instances():
    return list(
        DB.FilteredElementCollector(doc)
          .OfClass(DB.RevitLinkInstance)
          .WhereElementIsNotElementType()
          .ToElements()
    )


def get_source_eligible_views():
    """Views that can serve as source - any view with V/G RVT Links support."""
    return [
        v for v in DB.FilteredElementCollector(doc)
                     .OfClass(DB.View)
                     .WhereElementIsNotElementType()
                     .ToElements()
        if not v.IsTemplate
        and v.ViewType not in EXCLUDED_VIEW_TYPES
    ]


def get_views_by_type(view_type):
    """Get non-template views of a specific ViewType (excluding unsupported)."""
    return [
        v for v in DB.FilteredElementCollector(doc)
                     .OfClass(DB.View)
                     .WhereElementIsNotElementType()
                     .ToElements()
        if not v.IsTemplate
        and v.ViewType == view_type
        and v.ViewType not in EXCLUDED_VIEW_TYPES
    ]


def get_view_templates():
    """Get all view templates that could potentially control RVT Links.
    Excludes templates for schedule/legend/drafting type views."""
    templates = []
    for v in DB.FilteredElementCollector(doc) \
                .OfClass(DB.View) \
                .WhereElementIsNotElementType() \
                .ToElements():
        if v.IsTemplate:
            # Try to exclude templates that are clearly for non-link views
            if v.ViewType not in EXCLUDED_VIEW_TYPES:
                templates.append(v)
    return templates


# ─────────────────────────────────────────────────────────────────────
# TEMPLATE HELPERS
# ─────────────────────────────────────────────────────────────────────

def get_rvt_link_param_id():
    bip = getattr(DB.BuiltInParameter, "VIS_GRAPHICS_RVT_LINKS", None)
    if bip is not None:
        return DB.ElementId(bip)
    return None


RVT_LINK_PARAM_ID = get_rvt_link_param_id()


def template_controls_link_overrides(view):
    if view.ViewTemplateId == DB.ElementId.InvalidElementId:
        return False
    template = doc.GetElement(view.ViewTemplateId)
    if template is None:
        return False
    if RVT_LINK_PARAM_ID is None:
        return None
    non_controlled = template.GetNonControlledTemplateParameterIds()
    return RVT_LINK_PARAM_ID not in non_controlled


def get_settings_owner(view):
    """Return the element that actually owns the link override data.
    If view has a template controlling RVT Links -> returns the template.
    Otherwise -> returns the view itself."""
    if view.IsTemplate:
        return view
    if template_controls_link_overrides(view) is True:
        return doc.GetElement(view.ViewTemplateId)
    return view


# ─────────────────────────────────────────────────────────────────────
# FULL LINK OVERRIDE COPY
# ─────────────────────────────────────────────────────────────────────

def copy_full_link_overrides(source_view, target_view_or_template, link_inst):
    """
    Copy all link display settings from source to target.
    target_view_or_template can be either a View or a View Template.
    """
    result = {
        "link_override_copied": False,
        "categories_copied": 0,
        "categories_errored": 0,
        "cat_errors": [],
        "overall_error": None
    }

    link_id = link_inst.Id
    link_type = get_link_type(link_inst)

    # Step 1: Copy link-level settings
    for eid in [link_id] + ([link_type.Id] if link_type else []):
        try:
            s = source_view.GetLinkOverrides(eid)
            if s is not None and is_custom(s):
                target_view_or_template.SetLinkOverrides(eid, s)
                result["link_override_copied"] = True
                break
        except Exception:
            continue

    if not result["link_override_copied"]:
        try:
            cs = make_custom_settings()
            target_view_or_template.SetLinkOverrides(link_id, cs)
            result["link_override_copied"] = True
        except Exception as e:
            result["overall_error"] = str(e)
            return result

    # Step 2: Copy per-category overrides
    linked_doc = get_linked_doc(link_inst)
    if linked_doc is None:
        result["overall_error"] = "Could not access linked document"
        return result

    all_cats = []
    try:
        for cat in linked_doc.Settings.Categories:
            all_cats.append(cat)
            try:
                if cat.SubCategories:
                    for sub in cat.SubCategories:
                        all_cats.append(sub)
            except Exception:
                pass
    except Exception as e:
        result["overall_error"] = "Could not read linked doc categories: {}".format(e)
        return result

    for cat in all_cats:
        try:
            cat_id = cat.Id
            override_copied = False

            try:
                src_ovr = source_view.GetCategoryOverrides(cat_id)
                src_hidden = source_view.GetCategoryHidden(cat_id)

                target_view_or_template.SetCategoryOverrides(cat_id, src_ovr)
                target_view_or_template.SetCategoryHidden(cat_id, src_hidden)
                override_copied = True
            except Exception:
                pass

            if override_copied:
                result["categories_copied"] += 1

        except Exception as e:
            result["categories_errored"] += 1
            err = str(e)
            if err not in result["cat_errors"]:
                result["cat_errors"].append(err)

    return result


# ─────────────────────────────────────────────────────────────────────
# SOURCE OVERRIDE CHECK
# ─────────────────────────────────────────────────────────────────────

def get_real_source_override(owner_view, link_inst):
    candidates = []
    candidates.append(("INSTANCE", link_inst.Id, link_label(link_inst)))

    link_type = get_link_type(link_inst)
    if link_type:
        candidates.append(("TYPE", link_type.Id, safe_name(link_type)))

    for kind, eid, label in candidates:
        try:
            s = owner_view.GetLinkOverrides(eid)
            if s is not None:
                return kind, eid, label, s
        except Exception:
            pass

    return None, None, None, None


# ═════════════════════════════════════════════════════════════════════
#  MAIN WORKFLOW
# ═════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────
# STEP 1 – Pick link(s)
# ─────────────────────────────────────────────────────────────────────

links = get_all_link_instances()
if not links:
    forms.alert("No linked Revit models found.", exitscript=True)

link_map = {}
for l in links:
    lbl = link_label(l)
    if lbl in link_map:
        lbl = "{} (Id:{})".format(lbl, l.Id)
    link_map[lbl] = l

selected_link_names = forms.SelectFromList.show(
    sorted(link_map.keys()),
    title="Step 1: Select Link(s) to Copy Settings For",
    multiselect=True,
    button_name="Select Link(s)",
)
if not selected_link_names:
    script.exit()

selected_links = [link_map[n] for n in selected_link_names]


# ─────────────────────────────────────────────────────────────────────
# STEP 2 – Pick source view
# ─────────────────────────────────────────────────────────────────────

all_source_views = get_source_eligible_views()
if not all_source_views:
    forms.alert("No eligible source views found.", exitscript=True)

source_view_map = {}
for v in all_source_views:
    key = "{} - {}".format(v.ViewType, safe_name(v))
    if key in source_view_map:
        key = "{} (Id:{})".format(key, v.Id)
    source_view_map[key] = v

source_key = forms.SelectFromList.show(
    sorted(source_view_map.keys()),
    title="Step 2: Select SOURCE View (copy FROM)",
    multiselect=False,
    button_name="Select Source View",
)
if not source_key:
    script.exit()

source_view = source_view_map[source_key]
source_owner = get_settings_owner(source_view)
source_is_tpl = source_owner.Id != source_view.Id


# ─────────────────────────────────────────────────────────────────────
# STEP 3 – Validate source has Custom overrides
# ─────────────────────────────────────────────────────────────────────

valid_links = []
missing_links = []

for link in selected_links:
    kind, eid, label, settings = get_real_source_override(source_owner, link)

    if settings is None:
        missing_links.append(link)
    elif not is_custom(settings):
        missing_links.append(link)
    else:
        valid_links.append(link)

if not valid_links:
    forms.alert(
        "No Custom overrides found on the source view.\n\n"
        "Open Visibility/Graphics for '{}', go to Revit Links tab, "
        "set the link to 'Custom', configure your category overrides, "
        "then re-run.".format(safe_name(source_view)),
        exitscript=True,
    )

if missing_links:
    names = "\n".join("  - " + link_label(l) for l in missing_links)
    if not forms.alert(
        "These links have no Custom override on source:\n\n{}\n\n"
        "Skip them and continue?".format(names),
        yes=True, no=True
    ):
        script.exit()


# ─────────────────────────────────────────────────────────────────────
# STEP 4 – Choose target TYPE (Templates / Floor Plans / Sections etc.)
# ─────────────────────────────────────────────────────────────────────

# Build available choices - only show types that have views in the project
available_choices = []

# Check templates
templates = get_view_templates()
if templates:
    available_choices.append("View Templates")

# Check each view type
for display_name, vtype in TARGET_TYPE_CHOICES.items():
    if display_name == "View Templates":
        continue  # already handled
    views_of_type = get_views_by_type(vtype)
    if views_of_type:
        available_choices.append(display_name)

if not available_choices:
    forms.alert("No eligible target views or templates found.", exitscript=True)

selected_type = forms.SelectFromList.show(
    available_choices,
    title="Step 3: What Do You Want to Apply Overrides To?",
    multiselect=False,
    button_name="Select Target Type",
)
if not selected_type:
    script.exit()


# ─────────────────────────────────────────────────────────────────────
# STEP 5 – Pick target view(s) or template(s) from filtered list
# ─────────────────────────────────────────────────────────────────────

is_template_mode = (selected_type == "View Templates")

if is_template_mode:
    # Show templates
    candidates = get_view_templates()
    target_map = {}
    for t in candidates:
        key = safe_name(t)
        if key in target_map:
            key = "{} (Id:{})".format(key, t.Id)
        target_map[key] = t

    list_title = "Step 4: Select Target Template(s)"

else:
    # Show views of the selected type
    vtype = TARGET_TYPE_CHOICES[selected_type]
    candidates = get_views_by_type(vtype)

    # Remove the source view from target list
    candidates = [v for v in candidates
                  if eid_to_int(v.Id) != eid_to_int(source_view.Id)]

    target_map = {}
    for v in candidates:
        # Show template info if applicable
        tpl_info = ""
        if v.ViewTemplateId != DB.ElementId.InvalidElementId:
            tpl = doc.GetElement(v.ViewTemplateId)
            if tpl:
                controls = template_controls_link_overrides(v)
                if controls is True:
                    tpl_info = "  [Template: {} - controls links]".format(
                        safe_name(tpl))
                elif controls is False:
                    tpl_info = "  [Template: {} - links NOT controlled]".format(
                        safe_name(tpl))

        key = "{}{}".format(safe_name(v), tpl_info)
        if key in target_map:
            key = "{} (Id:{})".format(key, v.Id)
        target_map[key] = v

    list_title = "Step 4: Select Target {} (copy TO)".format(selected_type)

if not target_map:
    forms.alert("No eligible targets found for '{}'.".format(selected_type),
                exitscript=True)

target_keys = forms.SelectFromList.show(
    sorted(target_map.keys()),
    title=list_title,
    multiselect=True,
    button_name="Select Target(s)",
)
if not target_keys:
    script.exit()

target_elements = [target_map[k] for k in target_keys]


# ─────────────────────────────────────────────────────────────────────
# STEP 6 – Resolve target owners & deduplicate
#   For templates mode: write directly to template
#   For views mode: check if template controls links
# ─────────────────────────────────────────────────────────────────────

target_owner_map = {}

if is_template_mode:
    # Templates are the direct targets
    for tpl in target_elements:
        owner_key = eid_to_int(tpl.Id)
        if owner_key not in target_owner_map:
            target_owner_map[owner_key] = {
                "owner": tpl,
                "is_template": True,
                "view_names": [safe_name(tpl)],
            }
else:
    # Views - resolve actual owner (view or its template)
    template_warning_views = []

    for v in target_elements:
        owner = get_settings_owner(v)
        owner_key = eid_to_int(owner.Id)
        is_tpl = owner.Id != v.Id

        if is_tpl:
            template_warning_views.append(
                "  {} -> template: {}".format(safe_name(v), safe_name(owner))
            )

        if owner_key not in target_owner_map:
            target_owner_map[owner_key] = {
                "owner": owner,
                "is_template": is_tpl,
                "view_names": [],
            }
        target_owner_map[owner_key]["view_names"].append(safe_name(v))

    # Warn user if some views will be modified via their template
    if template_warning_views:
        warn_msg = (
            "These views have templates controlling RVT Link overrides.\n"
            "Changes will be written to the TEMPLATE (affecting all views "
            "using that template):\n\n{}\n\n"
            "Continue?"
        ).format("\n".join(template_warning_views))

        if not forms.alert(warn_msg, yes=True, no=True):
            script.exit()


# ─────────────────────────────────────────────────────────────────────
# STEP 7 – Apply overrides
# ─────────────────────────────────────────────────────────────────────

success_count = 0
fail_count = 0
template_write_count = 0
view_write_count = 0
details = []

t = DB.Transaction(doc, "Copy RVT Link Display Settings + Category Overrides")
t.Start()

try:
    for owner_key, info in target_owner_map.items():
        owner = info["owner"]
        is_tpl = info["is_template"]
        view_names = info["view_names"]
        affected = len(view_names)

        for link in valid_links:
            try:
                r = copy_full_link_overrides(source_owner, owner, link)

                if r["overall_error"]:
                    fail_count += affected
                    details.append(
                        "FAIL: {} -> {} : {}".format(
                            link_label(link),
                            safe_name(owner),
                            r["overall_error"]
                        )
                    )
                else:
                    success_count += affected
                    if is_tpl:
                        template_write_count += 1
                    else:
                        view_write_count += affected

                    detail_msg = "OK: {} -> {} ({} cats".format(
                        link_label(link),
                        safe_name(owner),
                        r["categories_copied"]
                    )
                    if r["categories_errored"]:
                        detail_msg += ", {} errors".format(r["categories_errored"])
                    detail_msg += ")"
                    if is_tpl:
                        detail_msg += " [TEMPLATE - affects {} view(s)]".format(
                            affected)
                    details.append(detail_msg)

            except Exception as ex:
                fail_count += affected
                details.append(
                    "FAIL: {} -> {} : {}".format(
                        link_label(link), safe_name(owner), str(ex))
                )

    t.Commit()

except Exception as ex:
    t.RollBack()
    forms.alert("Transaction failed:\n\n{}".format(ex))
    script.exit()


# ─────────────────────────────────────────────────────────────────────
# STEP 8 – Report
# ─────────────────────────────────────────────────────────────────────

# Print details to output
output.print_md("## Copy RVT Link Overrides - Results")
output.print_md("**Source:** {}{}".format(
    safe_name(source_view),
    " (via template: {})".format(safe_name(source_owner)) if source_is_tpl else ""
))
output.print_md("**Links:** {}".format(
    ", ".join(link_label(l) for l in valid_links)))
output.print_md("**Target type:** {}".format(selected_type))
output.print_md("---")

for d in details:
    if d.startswith("FAIL"):
        output.print_md(":cross_mark: {}".format(d))
    else:
        output.print_md(":white_heavy_check_mark: {}".format(d))

output.print_md("---")
output.print_md("**Total views affected:** {} success, {} failed".format(
    success_count, fail_count))

if template_write_count:
    output.print_md(
        "_({} unique template(s) modified, {} direct view writes)_".format(
            template_write_count, view_write_count))

# Toast
if fail_count == 0:
    msg = "Link overrides copied to {} view(s).".format(success_count)
    if template_write_count > 0:
        msg += " ({} via template)".format(template_write_count)
    forms.toaster.send_toast(msg, title="Copy Link Overrides")
else:
    forms.toaster.send_toast(
        "Copied: {} | Failed: {}".format(success_count, fail_count),
        title="Copy Link Overrides"
    )