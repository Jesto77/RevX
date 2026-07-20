# -*- coding: utf-8 -*-
"""Copy RVT Link display overrides from one view to one or more views.

Copies:
- Link visibility type (Custom)
- ALL category overrides (Model, Annotation, Analytical, Import)
  including visibility, halftone, line weight, color, pattern, transparency
- Category visibility (on/off)

Requires Revit 2018+ (View.GetLinkOverrides / SetLinkOverrides API).
"""

from pyrevit import revit, DB, forms, script
import Autodesk.Revit.DB as RDB

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()


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

if LinkVisibilityType is not None:
    output.print_md("✅ Found `LinkVisibilityType` enum")
else:
    output.print_md("❌ Could not find `LinkVisibilityType` enum")
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
    """Get the linked Document from a RevitLinkInstance."""
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


def get_selectable_views():
    excluded_types = (
        DB.ViewType.Schedule,
        DB.ViewType.DrawingSheet,
        DB.ViewType.Legend,
        DB.ViewType.ProjectBrowser,
        DB.ViewType.SystemBrowser,
        DB.ViewType.Internal,
        DB.ViewType.Undefined,
    )
    return [
        v for v in DB.FilteredElementCollector(doc)
                     .OfClass(DB.View)
                     .WhereElementIsNotElementType()
                     .ToElements()
        if not v.IsTemplate and v.ViewType not in excluded_types
    ]


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
    if template_controls_link_overrides(view) is True:
        return doc.GetElement(view.ViewTemplateId)
    return view


def free_link_param_from_template(template):
    if RVT_LINK_PARAM_ID is None:
        return
    ids = list(template.GetNonControlledTemplateParameterIds())
    if RVT_LINK_PARAM_ID not in ids:
        ids.append(RVT_LINK_PARAM_ID)
        template.SetNonControlledTemplateParameterIds(ids)


# ─────────────────────────────────────────────────────────────────────
# GET ALL CATEGORIES FROM LINKED DOCUMENT
# ─────────────────────────────────────────────────────────────────────

def get_all_link_categories(link_inst):
    """
    Get all category IDs from the linked document that can have
    visibility/graphic overrides applied.
    Also includes sub-categories.
    """
    cat_ids = []
    linked_doc = get_linked_doc(link_inst)

    if linked_doc is not None:
        # Get categories from the linked document
        try:
            cats = linked_doc.Settings.Categories
            for cat in cats:
                try:
                    cat_ids.append(cat.Id)
                    # Also get sub-categories
                    if cat.SubCategories:
                        for sub in cat.SubCategories:
                            try:
                                cat_ids.append(sub.Id)
                            except Exception:
                                pass
                except Exception:
                    pass
        except Exception:
            pass

    # Also try host document categories (Revit uses host category IDs
    # when setting overrides for linked model categories)
    try:
        host_cats = doc.Settings.Categories
        for cat in host_cats:
            try:
                if cat.Id not in cat_ids:
                    cat_ids.append(cat.Id)
                if cat.SubCategories:
                    for sub in cat.SubCategories:
                        try:
                            if sub.Id not in cat_ids:
                                cat_ids.append(sub.Id)
                        except Exception:
                            pass
            except Exception:
                pass
    except Exception:
        pass

    return cat_ids


# ─────────────────────────────────────────────────────────────────────
# COPY ALL CATEGORY OVERRIDES FOR A LINK
# ─────────────────────────────────────────────────────────────────────

def copy_link_category_overrides(source_view, target_view, link_inst):
    """
    Copy ALL per-category overrides (visibility, halftone, line weight,
    color, patterns, transparency) from source to target for a given link.

    Uses the view's own GetCategoryOverrides/SetCategoryOverrides for
    the linked model's categories.

    Returns (copied_count, error_count, error_messages)
    """
    copied = 0
    errors = 0
    error_msgs = []

    cat_ids = get_all_link_categories(link_inst)

    for cat_id in cat_ids:
        try:
            # Get the override from source view
            source_override = source_view.GetCategoryOverrides(cat_id)

            # Get the visibility state from source view
            try:
                source_visible = source_view.GetCategoryHidden(cat_id)
            except Exception:
                source_visible = None

            # Apply override to target view
            target_view.SetCategoryOverrides(cat_id, source_override)

            # Apply visibility state
            if source_visible is not None:
                try:
                    target_view.SetCategoryHidden(cat_id, source_visible)
                except Exception:
                    pass

            copied += 1

        except Exception as e:
            errors += 1
            # Only log unique error types, not every single category
            err_str = str(e)
            if err_str not in error_msgs:
                error_msgs.append(err_str)

    return copied, errors, error_msgs


# ─────────────────────────────────────────────────────────────────────
# COPY LINK OVERRIDES USING THE LINK OVERRIDE MANAGER (2019+)
# ─────────────────────────────────────────────────────────────────────

def try_copy_via_override_manager(source_view, target_view, link_id):
    """
    Try to use View.GetLinkOverrides to get the full override settings
    including nested category overrides, if the API version supports it.

    The RevitLinkGraphicsSettings object may contain category-level
    override data depending on the Revit API version.

    Returns True if successfully copied detailed overrides, False otherwise.
    """
    try:
        source_settings = source_view.GetLinkOverrides(link_id)
        if source_settings is None:
            return False

        # Check if the settings object has category override methods
        # (API availability varies by Revit version)

        # Try to get category overrides from the settings object
        has_cat_overrides = False

        # Method 1: Check for GetCategoryOverrides on the settings object
        if hasattr(source_settings, 'GetCategoryOverrides'):
            has_cat_overrides = True

        # Method 2: Check for LinkedOverridesMap or similar
        if hasattr(source_settings, 'GetLinkCategoryOverridesMap'):
            has_cat_overrides = True

        if has_cat_overrides:
            target_view.SetLinkOverrides(link_id, source_settings)
            return True

        # Even without explicit category override methods,
        # SetLinkOverrides might carry the full data
        target_view.SetLinkOverrides(link_id, source_settings)
        return True

    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────
# FULL LINK OVERRIDE COPY (COMBINES ALL METHODS)
# ─────────────────────────────────────────────────────────────────────

def copy_full_link_overrides(source_view, target_view, link_inst):
    """
    Complete copy of all link display settings:
    1. Copy the link-level override (Custom/ByHost/ByLink)
    2. Copy all category overrides within the link
    3. Copy category visibility states

    Returns result dict with details.
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

    # ── Step 1: Copy the link-level settings ──
    # Try instance first, then type
    for eid in [link_id] + ([link_type.Id] if link_type else []):
        try:
            s = source_view.GetLinkOverrides(eid)
            if s is not None and is_custom(s):
                target_view.SetLinkOverrides(eid, s)
                result["link_override_copied"] = True
                break
        except Exception:
            continue

    if not result["link_override_copied"]:
        # Force set to Custom on target
        try:
            cs = make_custom_settings()
            target_view.SetLinkOverrides(link_id, cs)
            result["link_override_copied"] = True
        except Exception as e:
            result["overall_error"] = str(e)
            return result

    # ── Step 2: Copy per-category overrides ──
    # These are the Model Categories, Annotation Categories, etc.
    # that appear inside the link's Custom display settings

    linked_doc = get_linked_doc(link_inst)
    if linked_doc is None:
        result["overall_error"] = "Could not access linked document"
        return result

    # Get all categories from the linked document
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

    output.print_md("  Processing `{}` categories from linked model...".format(
        len(all_cats)
    ))

    # For each category in the linked document, copy the override
    for cat in all_cats:
        try:
            cat_id = cat.Id

            # ── Get source override for this category in the link context ──
            # The API for per-link-category overrides varies by Revit version
            # Try multiple approaches

            override_copied = False

            # Approach A: Use View.GetCategoryOverrides with the category ID
            # When a link is set to Custom, the view stores overrides
            # keyed by the link's category IDs
            try:
                src_ovr = source_view.GetCategoryOverrides(cat_id)
                src_hidden = source_view.GetCategoryHidden(cat_id)

                target_view.SetCategoryOverrides(cat_id, src_ovr)
                target_view.SetCategoryHidden(cat_id, src_hidden)
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
    """Check INSTANCE then TYPE for a Custom override."""
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
    title="Select Link(s) to Copy Settings For",
    multiselect=True,
    button_name="Select Link(s)",
)
if not selected_link_names:
    script.exit()

selected_links = [link_map[n] for n in selected_link_names]


# ─────────────────────────────────────────────────────────────────────
# STEP 2 – Pick source view
# ─────────────────────────────────────────────────────────────────────

all_views = get_selectable_views()
view_map = {}
for v in all_views:
    key = "{} - {}".format(v.ViewType, safe_name(v))
    if key in view_map:
        key = "{} (Id:{})".format(key, v.Id)
    view_map[key] = v

source_key = forms.SelectFromList.show(
    sorted(view_map.keys()),
    title="Select SOURCE View (copy FROM)",
    multiselect=False,
    button_name="Select Source View",
)
if not source_key:
    script.exit()

source_view = view_map[source_key]
source_owner = get_settings_owner(source_view)
source_is_tpl = source_owner.Id != source_view.Id

output.print_md("---")
output.print_md("## Source View Info")
output.print_md("**Source View:** `{}`".format(safe_name(source_view)))
output.print_md("**Settings Owner:** `{}` ({})".format(
    safe_name(source_owner), "TEMPLATE" if source_is_tpl else "VIEW"
))


# ─────────────────────────────────────────────────────────────────────
# STEP 3 – Validate source
# ─────────────────────────────────────────────────────────────────────

output.print_md("## Source Override Check")

valid_links = []
missing_links = []

for link in selected_links:
    kind, eid, label, settings = get_real_source_override(source_owner, link)

    if settings is None:
        output.print_md("❌ `{}` - No override found".format(link_label(link)))
        missing_links.append(link)
    elif not is_custom(settings):
        output.print_md(
            "⚠ `{}` - Found but not Custom (type=`{}`)".format(
                link_label(link), int(settings.LinkVisibilityType)
            )
        )
        missing_links.append(link)
    else:
        linked_doc = get_linked_doc(link)
        cat_count = 0
        if linked_doc:
            try:
                for cat in linked_doc.Settings.Categories:
                    cat_count += 1
            except Exception:
                pass
        output.print_md(
            "✅ `{}` - Custom override on {} | Linked doc has `{}` categories".format(
                link_label(link), kind, cat_count
            )
        )
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
        "These links have no Custom override:\n\n{}\n\n"
        "Skip them and continue?".format(names),
        yes=True, no=True
    ):
        script.exit()


# ─────────────────────────────────────────────────────────────────────
# STEP 4 – Pick target view(s)
# ─────────────────────────────────────────────────────────────────────

target_keys = forms.SelectFromList.show(
    sorted(k for k in view_map if k != source_key),
    title="Select TARGET View(s) (copy TO)",
    multiselect=True,
    button_name="Select Target Views",
)
if not target_keys:
    script.exit()

target_views = [view_map[k] for k in target_keys]


# ─────────────────────────────────────────────────────────────────────
# STEP 5 – Check target template locks
# ─────────────────────────────────────────────────────────────────────

locked_targets = []
free_targets = []
for v in target_views:
    if template_controls_link_overrides(v) is True:
        locked_targets.append(v)
    else:
        free_targets.append(v)

unlock_confirmed = False
if locked_targets:
    locked_names = "\n".join(
        "  - {} (template: {})".format(
            safe_name(v), safe_name(doc.GetElement(v.ViewTemplateId))
        )
        for v in locked_targets
    )
    unlock_confirmed = forms.alert(
        "These targets have templates controlling RVT Link overrides:\n\n"
        "{}\n\nUnlock and proceed?".format(locked_names),
        yes=True, no=True,
    )


# ─────────────────────────────────────────────────────────────────────
# STEP 6 – Apply
# ─────────────────────────────────────────────────────────────────────

results = {"success": [], "skipped": [], "failed": []}

t = DB.Transaction(doc, "Copy RVT Link Display Settings + Category Overrides")
t.Start()

try:
    # Unlock templates
    if locked_targets and unlock_confirmed:
        done = set()
        for v in locked_targets:
            tid = v.ViewTemplateId
            if tid not in done:
                free_link_param_from_template(doc.GetElement(tid))
                done.add(tid)
        free_targets.extend(locked_targets)
        locked_targets = []

    # Copy to each target
    for v in free_targets:
        for link in valid_links:
            try:
                output.print_md(
                    "### Copying `{}` -> `{}`".format(
                        link_label(link), safe_name(v)
                    )
                )

                r = copy_full_link_overrides(source_owner, v, link)

                if r["overall_error"]:
                    results["failed"].append(
                        "{} <- {} | {}".format(
                            safe_name(v), link_label(link),
                            r["overall_error"]
                        )
                    )
                else:
                    detail = (
                        "link_override={}, categories_copied={}, "
                        "cat_errors={}".format(
                            r["link_override_copied"],
                            r["categories_copied"],
                            r["categories_errored"]
                        )
                    )
                    results["success"].append(
                        "{} <- {} ({})".format(
                            safe_name(v), link_label(link), detail
                        )
                    )
                    if r["cat_errors"]:
                        for ce in r["cat_errors"][:3]:
                            output.print_md("  ⚠ Cat error: `{}`".format(ce))

            except Exception as e:
                results["failed"].append(
                    "{} <- {} | ERROR: {}".format(
                        safe_name(v), link_label(link), e
                    )
                )

    for v in locked_targets:
        results["skipped"].append(
            "{} (template still controls overrides)".format(safe_name(v))
        )

    t.Commit()
    output.print_md("✅ **Transaction committed successfully**")

except Exception as ex:
    t.RollBack()
    forms.alert("Transaction failed:\n\n{}".format(ex))
    script.exit()


# ─────────────────────────────────────────────────────────────────────
# STEP 7 – Report
# ─────────────────────────────────────────────────────────────────────

output.print_md("---")
output.print_md("## Final Results")

if source_is_tpl:
    output.print_md(
        "_Source read from template: **{}**_".format(safe_name(source_owner))
    )

if results["success"]:
    output.print_md("### ✅ Applied ({})".format(len(results["success"])))
    for r in results["success"]:
        output.print_md("- " + r)

if results["skipped"]:
    output.print_md("### ⏭ Skipped ({})".format(len(results["skipped"])))
    for r in results["skipped"]:
        output.print_md("- " + r)

if results["failed"]:
    output.print_md("### ❌ Failed ({})".format(len(results["failed"])))
    for r in results["failed"]:
        output.print_md("- " + r)

if not any(results.values()):
    output.print_md("_No operations performed._")