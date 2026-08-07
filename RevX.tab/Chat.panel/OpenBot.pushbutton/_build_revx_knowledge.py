# -*- coding: utf-8 -*-
"""Build revx_tools_knowledge.py from revx_tools_catalog.json (run with CPython)."""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(HERE, "revx_tools_catalog.json")
OUT = os.path.join(HERE, "revx_tools_knowledge.py")

# ── Skip accidental non-tool pushbutton folders ────────────────────────
SKIP_BUTTONS = {
    "Highlighted walls are attached to, but miss, the highlighted targets",
    "Line in Sketch is slightly off axis and may cause inaccuracies",
    "Wall is slightly off axis and may cause inaccuracies",
}

# ── Fix internal/numeric panel segment names → human-readable labels ──
PANEL_NAME_MAP = {
    # Numeric sub-panels (Landscape)
    "01":             "Landscape",
    "02":             "Landscape",
    # Internal dotted names
    "Filter.Stack":   "Filter",
    # Abbreviations / CAD sub-panel
    "Cad":            "CAD Export",
    # Catch-all cleans
    "RevX":           "RevX",          # top-level tab label — kept as-is
}

# ── Map full ui_path strings to clean human-readable ribbon paths ──────
# Key   = exact ui_path from JSON
# Value = what the AI should say
UI_PATH_OVERRIDES = {
    "RevX tab > RevX > ACC":
        "RevX tab  ▶  ACC panel",
    "RevX tab > RevX > Convert":
        "RevX tab  ▶  Convert panel",
    "RevX tab > RevX > Detail Item":
        "RevX tab  ▶  Detail Item panel",
    "RevX tab > RevX > Export > Cad":
        "RevX tab  ▶  Export panel  ▶  CAD sub-group",
    "RevX tab > RevX > Family":
        "RevX tab  ▶  Family panel",
    "RevX tab > RevX > Filter > Filter.Stack":
        "RevX tab  ▶  Filter panel",
    "RevX tab > RevX > Floor":
        "RevX tab  ▶  Floor panel",
    "RevX tab > RevX > Landscape > 01":
        "RevX tab  ▶  Landscape panel  (top row)",
    "RevX tab > RevX > Landscape > 02":
        "RevX tab  ▶  Landscape panel  (second row)",
    "RevX tab > RevX > Link":
        "RevX tab  ▶  Link panel",
    "RevX tab > RevX > Model Health":
        "RevX tab  ▶  Model Health panel",
    "RevX tab > RevX > Model Health > Wipe":
        "RevX tab  ▶  Model Health panel  ▶  Wipe sub-group",
    "RevX tab > RevX > Schedule":
        "RevX tab  ▶  Schedule panel",
    "RevX tab > RevX > Sheets":
        "RevX tab  ▶  Sheets panel",
    "RevX tab > RevX > Tools":
        "RevX tab  ▶  Tools panel",
    "RevX tab > RevX > Toposolid":
        "RevX tab  ▶  Toposolid panel",
    "RevX tab > RevX > Warnings > Remove":
        "RevX tab  ▶  Warnings panel  ▶  Remove sub-group",
}


def clean_ui_path(raw_ui_path, button=None):
    """
    Return a human-readable ribbon location string.
    Priority:
      1. Exact match in UI_PATH_OVERRIDES
      2. Segment-by-segment cleanup via PANEL_NAME_MAP
      3. Raw value as fallback
    Then append the button name so the AI always knows what to click.
    """
    if not raw_ui_path:
        return "RevX tab  (location unknown)"

    # 1. Exact override
    base = UI_PATH_OVERRIDES.get(raw_ui_path)

    # 2. Segment cleanup
    if not base:
        segments = [s.strip() for s in raw_ui_path.split(">")]
        cleaned = []
        for seg in segments:
            mapped = PANEL_NAME_MAP.get(seg)
            if mapped:
                # Only add if different from previous to avoid "RevX > RevX"
                if not cleaned or cleaned[-1] != mapped:
                    cleaned.append(mapped)
            elif seg.isdigit():
                pass  # drop pure numbers
            else:
                cleaned.append(seg)
        base = "  ▶  ".join(cleaned) if cleaned else raw_ui_path

    # 3. Append button so the AI always references the exact button name
    if button:
        return "{}  ▶  click [{}] button".format(base, button)
    return base


# ── Hand-written usage guides ──────────────────────────────────────────
MANUAL_SUPPLEMENTS = {
    "Material List": (
        "Opens your Autodesk Construction Cloud (ACC) Docs material list folder "
        "in the default web browser.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  ACC panel  ▶  click [Material List] button.\n"
        "  2. Your browser opens the ACC Docs material list folder.\n"
        "No model selection required.\n"
        "Use when you need the latest shared material/spec documents from ACC, "
        "not for Revit material parameters."
    ),
    "Outlook": (
        "Opens Outlook Web App inbox in Google Chrome.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  ACC panel  ▶  click [Outlook] button.\n"
        "  2. Chrome opens the Outlook Web App inbox.\n"
        "Works across all Revit versions. No model selection needed."
    ),
    "Copy State": (
        "Saves view filter graphic overrides (colors, line weights, patterns, "
        "visibility) from the active view to a JSON file.\n"
        "Steps:\n"
        "  1. Open the source view where filters look correct.\n"
        "  2. RevX tab  ▶  Filter panel  ▶  click [Copy State] button.\n"
        "  3. Check the filters you want to copy in the dialog.\n"
        "  4. Confirm — overrides are stored ready for Paste State.\n"
        "Tip: Copy from a view that is NOT locked by a template, or ensure "
        "the template allows filter overrides."
    ),
    "Paste State": (
        "Applies previously copied filter overrides onto the active view or its "
        "view template (template-aware). Works within the same project or across "
        "two open projects if filter names match.\n"
        "Steps:\n"
        "  1. Run Copy State on the source view first.\n"
        "  2. Open the target view.\n"
        "  3. RevX tab  ▶  Filter panel  ▶  click [Paste State] button.\n"
        "  4. Choose which saved filters to apply and confirm.\n"
        "Troubleshooting: If nothing changes, the view template may control "
        "filters — the tool writes to the template when needed. Missing filters "
        "in the target project must exist or be transferred first with the "
        "Transfer Filters button."
    ),
    "Transfer": (
        "Copies view filter definitions (rules, categories, names) from one open "
        "Revit project to another.\n"
        "Steps:\n"
        "  1. Have source and target projects open in Revit.\n"
        "  2. RevX tab  ▶  Filter panel  ▶  click [Transfer] button.\n"
        "  3. Pick the source project and the filters to copy.\n"
        "  4. Pick the target project — filters are recreated there.\n"
        "Use this BEFORE Paste State when the target project does not contain "
        "the same filter names."
    ),
    "Export": (
        "Batch export sheets and/or views to PDF, DWG, DGN, DWF, NWC, IFC, or "
        "image formats with custom naming rules.\n"
        "Workflow: Selection → Format → Create.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  Sheets panel  ▶  click [Export] button  "
        "(labelled 'Sheet Export' on the ribbon).\n"
        "  2. Select sheets/views in the dialog "
        "(or load a saved export set).\n"
        "  3. Choose format (PDF, DWG, etc.) and set naming rules / presets.\n"
        "  4. Set the output folder and click Create.\n"
        "Compatible with Revit 2024, 2025, 2026, 2027. "
        "Naming presets can combine sheet number, name, revision, and custom tokens."
    ),
    "Para": (
        "Parameter Manager — browse and edit shared, project, and family "
        "parameters from one window.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  Tools panel  ▶  click [Para] button.\n"
        "  2. Use tabs/trees to switch between project parameters, "
        "shared parameters, and family parameters.\n"
        "  3. Add, rename, bind, or remove parameters as allowed by Revit rules.\n"
        "  4. Apply changes — some edits require saving the project or "
        "reloading families.\n"
        "Always coordinate shared parameter files with your BIM lead."
    ),
    "Table Gen": (
        "Imports Excel or CSV data and creates a native Revit schedule in the project.\n"
        "Steps:\n"
        "  1. Prepare your Excel/CSV with column headers matching desired "
        "schedule fields.\n"
        "  2. RevX tab  ▶  Schedule panel  ▶  click [Table Gen] button.\n"
        "  3. Pick the file, map columns to Revit fields, confirm schedule type.\n"
        "  4. Place or open the new schedule view.\n"
        "Verify units and data types match Revit parameters."
    ),
    "Health": (
        "Model Health dashboard — scans the project for common issues "
        "(unused views/templates/filters, imports, warnings, etc.) and offers "
        "cleanup actions from one window.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  Model Health panel  ▶  click [Health] button.\n"
        "  2. Review each category in the dashboard dialog.\n"
        "  3. Use the purge/delete buttons per row — read warnings before deleting.\n"
        "Use before issuing models; pair with Wipe tools for targeted cleanup."
    ),
    "Pin": (
        "Bulk pin or unpin elements or views from one dialog.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  Model Health panel  ▶  click [Pin] button.\n"
        "  2. Choose pin or unpin and select elements.\n"
        "Use to protect approved geometry from accidental moves; "
        "unpin before editing pinned items."
    ),
    "Imported Cad": (
        "Lists imported CAD instances (not links) in the project and helps "
        "remove them.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  Model Health panel  ▶  Wipe sub-group  "
        "▶  click [Imported Cad] button.\n"
        "  2. Review the list before deleting — imported CAD adds file size "
        "and Revit warnings."
    ),
    "Unused View Filters": (
        "Finds view filters not used by any view in the project.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  Model Health panel  ▶  Wipe sub-group  "
        "▶  click [Unused View Filters] button.\n"
        "  2. Review and delete only after confirming filters are not needed."
    ),
    "Unused View Template": (
        "Finds view templates not assigned to any view.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  Model Health panel  ▶  Wipe sub-group  "
        "▶  click [Unused View Template] button.\n"
        "  2. Review and delete safely."
    ),
    "Views not on Sheets": (
        "Reports views that are not placed on any sheet.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  Model Health panel  ▶  Wipe sub-group  "
        "▶  click [Views not on Sheets] button.\n"
        "  2. Review the list — useful before archiving or purging views."
    ),
    "Line Pattern": (
        "Finds and removes unused or problematic line patterns.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  Model Health panel  ▶  Wipe sub-group  "
        "▶  click [Line Pattern] button.\n"
        "  2. Review list and delete unused patterns."
    ),
    "Parameters": (
        "Edit title block instance parameters on sheets in bulk.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  Sheets panel  ▶  click [Parameters] button.\n"
        "  2. Select sheets or title blocks in the dialog.\n"
        "  3. Edit shared/instance parameters "
        "(sheet number, issue date, etc.) and apply.\n"
        "Compatible with Revit 2024, 2025, 2026, 2027."
    ),
    "Get Blocks": (
        "Places Revit families at locations from AutoCAD POINT entities.\n"
        "Steps:\n"
        "  1. Ensure your CAD import/link contains POINT objects with block "
        "names matching family types.\n"
        "  2. RevX tab  ▶  Landscape panel  ▶  click [Get Blocks] button.\n"
        "  3. Pick the CAD source, family mapping, and level.\n"
        "  4. Run placement.\n"
        "Use for planting or furniture layouts exported from CAD as points."
    ),
    "Object Outline": (
        "Generates filled regions with material-based surface patterns from "
        "selected Floors, Roofs, Ceilings, or Toposolids.\n"
        "Steps:\n"
        "  1. Select the floor/roof/ceiling/toposolid elements.\n"
        "  2. RevX tab  ▶  Landscape panel (top row)  "
        "▶  click [Object Outline] button "
        "(labelled 'Surface Pattern Region' on the ribbon).\n"
        "  3. The tool creates filled regions matching the topmost compound "
        "structure layer.\n"
        "Compatible: Revit 2018–2027+."
    ),
    "Match Slope": (
        "Transfers slab shape/slope from a source floor/toposolid to target "
        "elements (landscape grading).\n"
        "Steps:\n"
        "  1. RevX tab  ▶  Landscape panel (second row)  "
        "▶  click [Match Slope] button.\n"
        "  2. Pick the source element with the desired slope/shape.\n"
        "  3. Pick the target element(s) to receive that shape.\n"
        "Targets must support shape editing (floors/toposolids). "
        "Complex meshes may need manual touch-up after."
    ),
    "Toposolid": (
        "Creates a smooth organic terrain mound (Toposolid or TopographySurface) "
        "from selected closed boundary model lines.\n"
        "Steps:\n"
        "  1. Draw and select closed model lines defining the mound boundary.\n"
        "  2. RevX tab  ▶  Landscape panel (second row)  "
        "▶  click [Toposolid] button "
        "(labelled 'Organic Mound' on the ribbon).\n"
        "  3. Choose Slope Mode (height from slope ratio) or "
        "Max Height Mode (enter height in mm).\n"
        "  4. Confirm — the mound is created inside the boundary.\n"
        "Compatible: Revit 2018–2025+  |  IronPython 2.7 (pyRevit)."
    ),
    "Height offset": (
        "Resets or adjusts the height offset for selected floor elements.\n"
        "Steps:\n"
        "  1. Select the floor elements in the model.\n"
        "  2. RevX tab  ▶  Floor panel  ▶  click [Height offset] button.\n"
        "  3. Read the dialog carefully — choose reset vs offset behaviour.\n"
        "  4. Confirm to apply."
    ),
    "Offset": (
        "Resizes Floors, Roofs, Ceilings, and Toposolids by applying an inward "
        "or outward boundary offset.\n"
        "Steps:\n"
        "  1. Select the element(s) to resize.\n"
        "  2. RevX tab  ▶  Floor panel  ▶  click [Offset] button.\n"
        "  3. Enter the offset distance (positive = outward, negative = inward).\n"
        "  4. Confirm to apply."
    ),
    "Reset Shape": (
        "Resets shape-edited floors back to their original flat condition by "
        "removing all slab shape modifications.\n"
        "Steps:\n"
        "  1. Select the shape-edited floor(s).\n"
        "  2. RevX tab  ▶  Floor panel  ▶  click [Reset Shape] button.\n"
        "  3. Confirm — all slab shape edits are removed and the floor "
        "returns to flat."
    ),
    "Excel": (
        "Exports the active schedule view to Excel preserving layout, colors, "
        "borders, merged cells, and images.\n"
        "Steps:\n"
        "  1. Open the schedule view in Revit.\n"
        "  2. RevX tab  ▶  Schedule panel  ▶  click [Excel] button.\n"
        "  3. Choose the output file path.\n"
        "  4. The schedule is exported — requires Microsoft Excel installed.\n"
        "Compatible: Revit 2024, 2025, 2026, 2027."
    ),
    "Custom": (
        "Copies RVT link visibility and graphic overrides from one view to other "
        "views.\n"
        "Steps:\n"
        "  1. Set link display overrides (Custom mode) in the source view.\n"
        "  2. RevX tab  ▶  Link panel  ▶  click [Custom] button.\n"
        "  3. Pick the source view, then pick the target view(s).\n"
        "  4. Confirm — overrides are copied including visibility, halftone, "
        "line weight, color, pattern, and transparency.\n"
        "Handles views with or without view templates. "
        "Requires Revit 2018+."
    ),
    "Selection Box": (
        "Link panel utility for selection/bounding box workflows around linked "
        "models.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  Link panel  ▶  click [Selection Box] button.\n"
        "  2. Follow the on-screen prompts to define the selection or box."
    ),
    "Element ID": (
        "Shows robust type and element identification for linked or selected "
        "elements.\n"
        "Steps:\n"
        "  1. Select the element(s) in the model.\n"
        "  2. RevX tab  ▶  Link panel  ▶  click [Element ID] button.\n"
        "  3. The tool displays type names and element IDs.\n"
        "Use when debugging links or writing schedules that need stable type names.\n"
        "Compatible: Revit 2023–2026."
    ),
    "Dwg": (
        "DWG Export — Pattern + Blocks merged into a single DWG file.\n"
        "Steps:\n"
        "  1. Set up your view with the correct visibility settings.\n"
        "  2. RevX tab  ▶  Export panel  ▶  CAD sub-group  "
        "▶  click [Dwg] button.\n"
        "  3. Choose output path and confirm export."
    ),
    "Irrigation": (
        "DWG Export — Pattern and Blocks exported as two SEPARATE DWG files per "
        "view.\n"
        "  - ViewName_PAT.dwg  (pattern categories only, shape-reset)\n"
        "  - ViewName_BLK.dwg  (all other categories + annotations)\n"
        "Steps:\n"
        "  1. Set up your view visibility as needed.\n"
        "  2. RevX tab  ▶  Export panel  ▶  CAD sub-group  "
        "▶  click [Irrigation] button.\n"
        "  3. Choose output folder and confirm."
    ),
    "Category": (
        "Re-assigns a loadable family to a different Revit category.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  Family panel  ▶  click [Category] button.\n"
        "  2. Pick a family instance in the active view.\n"
        "  3. Choose a new category from the alphabetical list.\n"
        "  4. Click Apply — the family is opened in the background, "
        "its category changed, and reloaded into the project.\n"
        "Compatible: Revit 2018–2026+  (IronPython & CPython)."
    ),
    "Name Changer": (
        "Renames loadable families in bulk with live preview.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  Family panel  ▶  click [Name Changer] button.\n"
        "  2. Choose a family category.\n"
        "  3. All loadable families of that category are listed.\n"
        "  4. Use prefix, suffix, find/replace, or case change options — "
        "preview shows current → new name side by side.\n"
        "  5. Optionally click 'Edit in Notepad' to edit names freely, "
        "then save and close Notepad to pull names back.\n"
        "  6. Click Apply — families are renamed in one transaction.\n"
        "Compatible: Revit 2018–2026+  (IronPython)."
    ),
    "Server Families": (
        "Browse families from another Revit project and load them into the "
        "current project.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  Family panel  ▶  click [Server Families] button.\n"
        "  2. Pick a source Revit project (.rvt) to browse.\n"
        "  3. Browse loadable families by category.\n"
        "  4. Select one or more families and click Load.\n"
        "Compatible: Revit 2022–2027  (IronPython 2.7 & CPython 3)."
    ),
    "Convert": (
        "Detail Item panel: Converts a selected element to a Detail Item family "
        "via a temporary DWG export and re-import.\n"
        "Steps:\n"
        "  1. Select the element in the active view.\n"
        "  2. RevX tab  ▶  Detail Item panel  ▶  click [Convert] button.\n"
        "  3. Name the new family and choose where to save the .rfa file.\n"
        "  4. The family is created and loaded back into the project "
        "at the original location automatically.\n"
        "Non-fatal issues are written to the pyRevit output console."
    ),
    "Line to Detail Item": (
        "Converts a selected line or curve element into a new Detail Item "
        "family (.rfa).\n"
        "Steps:\n"
        "  1. Select the line/curve element in the active view.\n"
        "  2. RevX tab  ▶  Detail Item panel  "
        "▶  click [Line to Detail Item] button.\n"
        "  3. Name the new family and choose the save location.\n"
        "  4. The .rfa is saved and can be reloaded as needed.\n"
        "Compatible: Revit 2023, 2024, 2025, 2026, 2027  "
        "(IronPython & CPython)."
    ),
    "Filled Region to Floor": (
        "Converts selected Filled Regions to Floors or Floors to Filled Regions "
        "in one click.\n"
        "Steps:\n"
        "  1. Select any mix of Filled Regions and Floors.\n"
        "  2. RevX tab  ▶  Convert panel  "
        "▶  click [Filled Region to Floor] button.\n"
        "  3. The tool auto-detects each element type and converts in the "
        "correct direction.\n"
        "  4. If multiple types exist you will be prompted to choose one.\n"
        "Notes:\n"
        "  - Only boundary geometry transfers — line styles, hatch patterns, "
        "and instance parameters are NOT copied.\n"
        "  - Floor.Create() requires Revit 2022+. "
        "Older Revit uses the legacy NewFloor() API."
    ),
    "Filled Region to Toposolid": (
        "Converts selected Filled Regions to Toposolids or Toposolids to "
        "Filled Regions in one click.\n"
        "Steps:\n"
        "  1. Select any mix of Filled Regions and Toposolids.\n"
        "  2. RevX tab  ▶  Convert panel  "
        "▶  click [Filled Region to Toposolid] button.\n"
        "  3. The tool auto-detects each element type and converts accordingly.\n"
        "REQUIRES Revit 2024 or later (Toposolid API introduced in 2024).\n"
        "Notes:\n"
        "  - Only boundary geometry transfers — no slope/grading data, "
        "no line styles, no instance parameters."
    ),
    "Floor to Toposolid": (
        "Converts selected Floors to Toposolids or Toposolids to Floors in one "
        "click.\n"
        "Steps:\n"
        "  1. Select any mix of Floors and Toposolids.\n"
        "  2. RevX tab  ▶  Convert panel  "
        "▶  click [Floor to Toposolid] button.\n"
        "  3. Choose whether to delete original elements after conversion "
        "when prompted.\n"
        "REQUIRES Revit 2024 or later.\n"
        "Notes:\n"
        "  - Only sketch boundary transfers. Floor parameters and "
        "shape-edited slopes are NOT copied automatically."
    ),
    "Crop View": (
        "Changes the crop region line style and lineweight for multiple views "
        "at once.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  Tools panel  ▶  click [Crop View] button "
        "(labelled 'Override Crop Region' on the ribbon).\n"
        "  2. Select the view types to update in the dialog.\n"
        "  3. Choose the line style and lineweight settings.\n"
        "  4. Apply — crop region overrides are set on all selected views."
    ),
    "In-Place to Loadable": (
        "Converts an in-place family into a loadable family while preserving "
        "its geometry, category, and placement.\n"
        "Steps:\n"
        "  1. RevX tab  ▶  Tools panel  "
        "▶  click [In-Place to Loadable] button.\n"
        "  2. Pick the in-place family instance in the active view.\n"
        "  3. Name the new family and choose the save location.\n"
        "  4. The loadable .rfa is built, saved, loaded back, and placed at "
        "the same location automatically.\n"
        "Compatible: Revit 2018–2026+  (IronPython 2.7 & CPython 3.x)."
    ),
    "Elevation": (
        "Elevates selected Revit model lines by a user-defined vertical offset.\n"
        "Steps:\n"
        "  1. Select the model lines to elevate.\n"
        "  2. RevX tab  ▶  Toposolid panel  ▶  click [Elevation] button.\n"
        "  3. Enter the vertical offset value.\n"
        "  4. Confirm — lines are moved vertically in one transaction."
    ),
    "Mound": (
        "Creates a Topography surface or Toposolid (Revit 2024+) from selected "
        "model contour lines and boundary curves.\n"
        "Steps:\n"
        "  1. Draw model lines representing contour lines and boundary curves.\n"
        "  2. Select those lines.\n"
        "  3. RevX tab  ▶  Toposolid panel  ▶  click [Mound] button.\n"
        "  4. The tool extracts elevation points, cleans duplicates, and "
        "builds a valid terrain surface."
    ),
}


def format_tool_entry(tool):
    """Format one tool into a clean human-readable block for the AI."""
    lines = []

    btn   = tool.get("button") or ""
    title = tool.get("title")
    raw_ui = tool.get("ui_path") or ""

    # Prefer title as display label; fall back to button name
    label = title if title else btn

    # ── Header ──────────────────────────────────────────────────────
    lines.append("TOOL: {}".format(label))

    # ── Ribbon location (always human-readable) ──────────────────────
    ribbon_loc = clean_ui_path(raw_ui, btn)
    lines.append("How to open: {}".format(ribbon_loc))

    # ── Button name (if different from label) ────────────────────────
    if btn and btn != label:
        lines.append("Ribbon button label: {}".format(btn))

    # ── Tooltip / Summary ────────────────────────────────────────────
    tooltip = tool.get("tooltip") or ""
    if tooltip:
        lines.append("Summary: {}".format(tooltip.strip()))

    # ── Doc string (clean up excessive blank lines) ──────────────────
    doc = tool.get("doc") or tool.get("description") or ""
    if doc:
        doc = re.sub(r"\n{3,}", "\n\n", doc.strip())
        lines.append("Details:")
        lines.append(doc)

    # ── Manual supplement (step-by-step usage guide) ─────────────────
    extra = MANUAL_SUPPLEMENTS.get(btn) or MANUAL_SUPPLEMENTS.get(label)
    if extra:
        lines.append("Step-by-step usage guide:")
        lines.append(extra)

    lines.append("---")
    return "\n".join(lines)


def main():
    with open(CATALOG, "r", encoding="utf-8") as f:
        tools = json.load(f)

    # Filter out warning-dump buttons
    tools = [t for t in tools if t.get("button") not in SKIP_BUTTONS]

    blocks   = []
    keywords = set(["revx", "rev x"])

    for t in tools:
        blocks.append(format_tool_entry(t))
        for part in (t.get("button") or "", t.get("title") or ""):
            part = part.lower()
            if len(part) > 2:
                keywords.add(part)
            for word in re.split(r"[\s\-/]+", part):
                if len(word) > 3:
                    keywords.add(word)

    knowledge = (
        "REVX EXTENSION KNOWLEDGE (RevX tab in pyRevit — landscape & productivity tools).\n"
        "IMPORTANT: When telling users WHERE to find a tool, always use the exact "
        "ribbon path shown in 'How to open:' for that tool. "
        "Use the format:  RevX tab  ▶  [Panel name]  ▶  click [Button name] button.\n"
        "Never use numbers or internal codes as panel or button names.\n\n"
        + "\n".join(blocks)
    )

    # Only keep multi-word or long keywords to reduce false positives
    kw_list = sorted(k for k in keywords if (" " in k) or (len(k) >= 12))

    py = '''# -*- coding: utf-8 -*-
"""RevX tool knowledge for RevitBot (auto-generated — run _build_revx_knowledge.py)."""

REVX_PHRASE_KEYWORDS = {kw_list}

REVX_TOOLS_KNOWLEDGE = {knowledge}


def get_revx_knowledge_text():
    return REVX_TOOLS_KNOWLEDGE


def mentions_revx(message):
    if not message:
        return False
    msg = message.lower()
    if "revx" in msg or "rev x" in msg:
        return True
    for kw in REVX_PHRASE_KEYWORDS:
        if kw in msg:
            return True
    return False
'''.format(kw_list=repr(kw_list), knowledge=repr(knowledge))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(py)

    print("Wrote {} ({} chars, {} tools)".format(OUT, len(knowledge), len(tools)))


if __name__ == "__main__":
    main()