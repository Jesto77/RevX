# -*- coding: utf-8 -*-
"""
Edit Title Block Instance Parameters
Author: Jesto Joy

Works on Revit 2024, 2025, 2026, 2027
"""

import clr
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from System.Windows.Forms import (
    Form, DataGridView, DataGridViewTextBoxColumn,
    DataGridViewAutoSizeColumnsMode,
    DataGridViewSelectionMode, DataGridViewClipboardCopyMode, DockStyle,
    Button, FormBorderStyle, FormStartPosition, DialogResult, MessageBox,
    Keys, Panel, AnchorStyles,
    DataGridViewCellStyle, DataGridViewColumnHeadersHeightSizeMode,
    Clipboard, TextDataFormat, Label, DataGridViewEditMode, FlatStyle,
    SortOrder, Padding
)
from System.Drawing import Size, Font, FontStyle, Color, Point, ContentAlignment

from pyrevit import revit, DB, forms, script

doc = revit.doc


# -----------------------------------------------------------------------------
# COMPATIBILITY
# -----------------------------------------------------------------------------

def eid_val(eid):
    """Return int value of ElementId. Works on Revit 2023 and 2024+."""
    try:
        return eid.Value
    except AttributeError:
        return eid.IntegerValue


# -----------------------------------------------------------------------------
# TITLEBLOCK HELPERS
# -----------------------------------------------------------------------------

def get_all_titleblock_instances(document):
    """Return all title block INSTANCES (not types) in the document."""
    return list(
        DB.FilteredElementCollector(document)
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsNotElementType()
        .ToElements()
    )


def get_instance_parameters(element):
    """Return all editable instance parameters of an element."""
    params = []
    for p in element.Parameters:
        try:
            if p.IsReadOnly:
                continue
            if p.Definition is None:
                continue
            params.append(p)
        except Exception:
            continue
    return params


def get_param_value_as_string(param):
    """Return parameter value as a display string."""
    try:
        if param.StorageType == DB.StorageType.String:
            v = param.AsString()
            return v if v else ""
        elif param.StorageType == DB.StorageType.Integer:
            v = param.AsInteger()
            return str(v) if v is not None else ""
        elif param.StorageType == DB.StorageType.Double:
            v = param.AsDouble()
            return str(v) if v is not None else ""
        elif param.StorageType == DB.StorageType.ElementId:
            eid = param.AsElementId()
            if eid and eid_val(eid) > 0:
                el = doc.GetElement(eid)
                if el:
                    try:
                        return el.Name
                    except Exception:
                        return str(eid_val(eid))
            return ""
    except Exception:
        pass
    return ""


def set_param_value_from_string(param, value_str):
    """Set parameter value from a string. Returns True if successful."""
    try:
        if param.IsReadOnly:
            return False

        if param.StorageType == DB.StorageType.String:
            param.Set(value_str if value_str is not None else "")
            return True

        elif param.StorageType == DB.StorageType.Integer:
            if value_str is None or value_str.strip() == "":
                return False
            try:
                param.Set(int(float(value_str.strip())))
                return True
            except Exception:
                return False

        elif param.StorageType == DB.StorageType.Double:
            if value_str is None or value_str.strip() == "":
                return False
            try:
                param.Set(float(value_str.strip()))
                return True
            except Exception:
                return False

        elif param.StorageType == DB.StorageType.ElementId:
            return False

    except Exception:
        return False
    return False


def get_sheet_of_titleblock(tb):
    """Return the sheet number/name of the sheet this title block sits on."""
    try:
        p = tb.get_Parameter(DB.BuiltInParameter.SHEET_NUMBER)
        sheet_number = (p.AsString() if p else "") or "?"
    except Exception:
        sheet_number = "?"

    try:
        p = tb.get_Parameter(DB.BuiltInParameter.SHEET_NAME)
        sheet_name = (p.AsString() if p else "") or ""
    except Exception:
        sheet_name = ""

    return sheet_number, sheet_name


# -----------------------------------------------------------------------------
# GUI - EDIT TABLE
# -----------------------------------------------------------------------------

class EditTableForm(Form):

    def __init__(self, titleblocks, parameter_names):
        self.titleblocks = titleblocks
        self.parameter_names = parameter_names
        self.changes = {}
        self.original_values = {}

        # ---- Form settings ----
        self.Text = "Edit Title Block Parameters"
        self.Size = Size(1200, 700)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.Sizable
        self.MinimumSize = Size(800, 400)

        # ==================================================================
        # ORDER OF CONTROL ADDITION MATTERS:
        # Docked controls fill in reverse order of how they're added.
        # Add BOTTOM first, then TOP, then FILL last so FILL fits in between.
        # ==================================================================

        # -------------------------------------------------------------------
        # BOTTOM: Button panel (added FIRST so it docks to the bottom)
        # -------------------------------------------------------------------
        btn_panel = Panel()
        btn_panel.Height = 60
        btn_panel.Dock = DockStyle.Bottom
        btn_panel.BackColor = Color.FromArgb(230, 230, 230)
        btn_panel.Padding = Padding(10)

        # Cancel button (docked right first, so it goes to the far right)
        self.cancel_btn = Button()
        self.cancel_btn.Text = "Cancel"
        self.cancel_btn.Size = Size(140, 40)
        self.cancel_btn.Dock = DockStyle.Right
        self.cancel_btn.Font = Font("Segoe UI", 10, FontStyle.Regular)
        self.cancel_btn.FlatStyle = FlatStyle.Standard
        self.cancel_btn.Click += self.on_cancel
        btn_panel.Controls.Add(self.cancel_btn)

        # Apply button (docked right AFTER cancel, so it appears LEFT of cancel)
        self.apply_btn = Button()
        self.apply_btn.Text = "Apply Changes"
        self.apply_btn.Size = Size(160, 40)
        self.apply_btn.Dock = DockStyle.Right
        self.apply_btn.BackColor = Color.FromArgb(0, 122, 204)
        self.apply_btn.ForeColor = Color.White
        self.apply_btn.FlatStyle = FlatStyle.Flat
        self.apply_btn.Font = Font("Segoe UI", 10, FontStyle.Bold)
        self.apply_btn.Click += self.on_apply
        btn_panel.Controls.Add(self.apply_btn)

        self.Controls.Add(btn_panel)

        # -------------------------------------------------------------------
        # TOP: Info label
        # -------------------------------------------------------------------
        info_panel = Panel()
        info_panel.Height = 40
        info_panel.Dock = DockStyle.Top
        info_panel.BackColor = Color.FromArgb(240, 240, 240)

        lbl = Label()
        lbl.Text = ("  Tip: Shift+Click to select range | Ctrl+C to copy | "
                    "Ctrl+V to paste | Ctrl+A to select column | Double-click cell to edit")
        lbl.Dock = DockStyle.Fill
        lbl.Font = Font("Segoe UI", 9, FontStyle.Regular)
        lbl.TextAlign = ContentAlignment.MiddleLeft
        info_panel.Controls.Add(lbl)
        self.Controls.Add(info_panel)

        # -------------------------------------------------------------------
        # FILL: DataGridView (added LAST so it fills the remaining space)
        # -------------------------------------------------------------------
        self.grid = DataGridView()
        self.grid.Dock = DockStyle.Fill
        self.grid.AllowUserToAddRows = False
        self.grid.AllowUserToDeleteRows = False
        self.grid.AllowUserToResizeRows = False
        self.grid.SelectionMode = DataGridViewSelectionMode.CellSelect
        self.grid.MultiSelect = True
        self.grid.ClipboardCopyMode = DataGridViewClipboardCopyMode.EnableWithoutHeaderText
        self.grid.EditMode = DataGridViewEditMode.EditOnKeystrokeOrF2
        self.grid.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.None
        self.grid.ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.AutoSize
        self.grid.RowHeadersWidth = 40
        self.grid.EnableHeadersVisualStyles = False

        # Header styling
        header_style = DataGridViewCellStyle()
        header_style.BackColor = Color.FromArgb(50, 50, 50)
        header_style.ForeColor = Color.White
        header_style.Font = Font("Segoe UI", 9, FontStyle.Bold)
        header_style.SelectionBackColor = Color.FromArgb(50, 50, 50)
        header_style.SelectionForeColor = Color.White
        self.grid.ColumnHeadersDefaultCellStyle = header_style

        # --- Always-visible identifier columns ---
        col_sheet = DataGridViewTextBoxColumn()
        col_sheet.HeaderText = "Sheet #"
        col_sheet.Name = "SheetNum"
        col_sheet.ReadOnly = True
        col_sheet.Width = 100
        col_sheet.Frozen = True
        col_sheet.DefaultCellStyle.BackColor = Color.FromArgb(220, 230, 245)
        col_sheet.DefaultCellStyle.SelectionBackColor = Color.FromArgb(180, 200, 230)
        self.grid.Columns.Add(col_sheet)

        col_name = DataGridViewTextBoxColumn()
        col_name.HeaderText = "Sheet Name"
        col_name.Name = "SheetName"
        col_name.ReadOnly = True
        col_name.Width = 220
        col_name.Frozen = True
        col_name.DefaultCellStyle.BackColor = Color.FromArgb(220, 230, 245)
        col_name.DefaultCellStyle.SelectionBackColor = Color.FromArgb(180, 200, 230)
        self.grid.Columns.Add(col_name)

        # --- User-selected parameter columns ---
        for pname in parameter_names:
            col = DataGridViewTextBoxColumn()
            col.HeaderText = pname
            col.Name = "PARAM__" + pname
            col.Width = 150
            self.grid.Columns.Add(col)

        # --- Populate rows ---
        for tb in titleblocks:
            sheet_num, sheet_name = get_sheet_of_titleblock(tb)
            row_data = [sheet_num, sheet_name]
            for pname in parameter_names:
                p = tb.LookupParameter(pname)
                val = get_param_value_as_string(p) if p else ""
                row_data.append(val)
                self.original_values[(eid_val(tb.Id), pname)] = val

            row_index = self.grid.Rows.Add(*row_data)
            self.grid.Rows[row_index].Tag = tb

        # Key handling for paste
        self.grid.KeyDown += self.on_key_down

        # Sort rows by sheet number ascending
        try:
            self.grid.Sort(self.grid.Columns["SheetNum"], SortOrder.Ascending)
        except Exception:
            pass

        # Add grid LAST so it fills remaining space between top and bottom panels
        self.Controls.Add(self.grid)

    # -----------------------------
    # Event Handlers
    # -----------------------------

    def on_key_down(self, sender, e):
        """Handle Ctrl+V paste and Ctrl+A select-column."""
        try:
            if e.Control and e.KeyCode == Keys.V:
                self.paste_from_clipboard()
                e.Handled = True

            elif e.Control and e.KeyCode == Keys.A:
                if self.grid.CurrentCell:
                    col_idx = self.grid.CurrentCell.ColumnIndex
                    for row in self.grid.Rows:
                        try:
                            row.Cells[col_idx].Selected = True
                        except Exception:
                            pass
                    e.Handled = True

        except Exception:
            pass

    def paste_from_clipboard(self):
        """Paste tab-delimited clipboard content starting from current cell."""
        try:
            if not Clipboard.ContainsText():
                return

            text = Clipboard.GetText(TextDataFormat.Text)
            if not text:
                return

            lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            while lines and lines[-1] == "":
                lines.pop()

            if not lines:
                return

            start_row = self.grid.CurrentCell.RowIndex if self.grid.CurrentCell else 0
            start_col = self.grid.CurrentCell.ColumnIndex if self.grid.CurrentCell else 2

            # Single value → fill all selected cells
            if len(lines) == 1 and "\t" not in lines[0] and self.grid.SelectedCells.Count > 1:
                value = lines[0]
                for cell in self.grid.SelectedCells:
                    if not cell.ReadOnly:
                        cell.Value = value
                return

            # Normal paste
            for i, line in enumerate(lines):
                target_row = start_row + i
                if target_row >= self.grid.Rows.Count:
                    break

                cells = line.split("\t")
                for j, cell_val in enumerate(cells):
                    target_col = start_col + j
                    if target_col >= self.grid.Columns.Count:
                        break
                    try:
                        cell = self.grid.Rows[target_row].Cells[target_col]
                        if not cell.ReadOnly:
                            cell.Value = cell_val
                    except Exception:
                        pass

        except Exception as ex:
            MessageBox.Show("Paste error: " + str(ex))

    def on_apply(self, sender, e):
        """Collect changes and close with OK result."""
        # Commit any in-progress cell edit before reading values
        try:
            self.grid.EndEdit()
        except Exception:
            pass

        self.changes = {}

        for row in self.grid.Rows:
            tb = row.Tag
            if tb is None:
                continue

            tb_id = eid_val(tb.Id)

            for pname in self.parameter_names:
                col_name = "PARAM__" + pname
                col_idx = self.grid.Columns[col_name].Index
                new_val = row.Cells[col_idx].Value
                if new_val is None:
                    new_val = ""
                else:
                    new_val = str(new_val)

                original = self.original_values.get((tb_id, pname), "")
                if new_val != original:
                    self.changes[(tb_id, pname)] = new_val

        self.DialogResult = DialogResult.OK
        self.Close()

    def on_cancel(self, sender, e):
        self.DialogResult = DialogResult.Cancel
        self.Close()


# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------

def main():
    titleblocks = get_all_titleblock_instances(doc)
    if not titleblocks:
        forms.alert("No title blocks found in this project.")
        return

    # Collect all unique instance parameter names
    all_param_names = set()
    for tb in titleblocks:
        for p in get_instance_parameters(tb):
            try:
                all_param_names.add(p.Definition.Name)
            except Exception:
                pass

    if not all_param_names:
        forms.alert("No editable instance parameters found on title blocks.")
        return

    sorted_names = sorted(all_param_names)

    # Let user select parameters
    selected = forms.SelectFromList.show(
        sorted_names,
        title="Select parameters to edit",
        multiselect=True,
        button_name="Edit Selected"
    )

    if not selected:
        return

    selected = list(selected)

    if not selected:
        forms.alert("No parameters selected.")
        return

    # Show edit table
    form = EditTableForm(titleblocks, selected)
    result = form.ShowDialog()

    if result != DialogResult.OK:
        return

    if not form.changes:
        forms.alert("No changes were made.")
        return

    # Apply changes in a transaction
    success = 0
    failed = []

    with revit.Transaction("Edit Title Block Parameters"):
        for (tb_id, pname), new_val in form.changes.items():
            try:
                tb = None
                for t in titleblocks:
                    if eid_val(t.Id) == tb_id:
                        tb = t
                        break
                if tb is None:
                    continue

                p = tb.LookupParameter(pname)
                if p is None:
                    failed.append("{} / {} (parameter not found)".format(tb_id, pname))
                    continue

                if set_param_value_from_string(p, new_val):
                    success += 1
                else:
                    failed.append("{} / {}".format(tb_id, pname))

            except Exception as ex:
                failed.append("{} / {} ({})".format(tb_id, pname, ex))

    # Report
    msg = "Updated {} value(s).".format(success)
    if failed:
        msg += "\n\nFailed:\n" + "\n".join(failed[:20])
        if len(failed) > 20:
            msg += "\n... and {} more".format(len(failed) - 20)

    forms.alert(msg)


main()