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
    SortOrder, Padding, DataGridViewTriState, ScrollBars, TextRenderer,
    DataGridViewContentAlignment, DataGridViewCellBorderStyle,
    DataGridViewHeaderBorderStyle, BorderStyle, TextFormatFlags
)
from System.Drawing import (
    Size, Font, FontStyle, Color, Point, ContentAlignment, SolidBrush,
    Rectangle, Pen
)

from pyrevit import revit, DB, forms, script

doc = revit.doc


# -----------------------------------------------------------------------------
# THEME
# -----------------------------------------------------------------------------

class Theme(object):
    """Cohesive, modern palette designed for Revit BIM workflows."""
    BG = Color.FromArgb(245, 247, 250)
    PANEL_BG = Color.FromArgb(255, 255, 255)
    BORDER = Color.FromArgb(220, 224, 230)

    HEADER_BG = Color.FromArgb(236, 240, 246)
    HEADER_FG = Color.FromArgb(40, 50, 68)
    HEADER_BORDER = Color.FromArgb(208, 214, 222)

    FROZEN_HEADER_BG = Color.FromArgb(226, 234, 246)
    FROZEN_BG = Color.FromArgb(242, 246, 252)
    FROZEN_SEL = Color.FromArgb(195, 215, 245)

    ROW_ALT = Color.FromArgb(248, 250, 253)
    GRID_LINES = Color.FromArgb(228, 232, 238)

    ACCENT = Color.FromArgb(0, 115, 210)
    ACCENT_TEXT = Color.White

    TEXT = Color.FromArgb(35, 42, 52)
    SUBTEXT = Color.FromArgb(100, 110, 125)

    FONT_FAMILY = "Segoe UI"


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


def estimate_column_width(text, font):
    """Measure how wide `text` needs to render in `font` with padding."""
    try:
        measured = TextRenderer.MeasureText(text, font).Width
    except Exception:
        measured = 8 * len(text)
    width = measured + 32
    if width < 120:
        width = 120
    if width > 240:
        width = 240
    return width


# -----------------------------------------------------------------------------
# GUI - EDIT TABLE
# -----------------------------------------------------------------------------

class EditTableForm(Form):

    HEADER_HEIGHT = 46
    ROW_HEIGHT = 28

    def __init__(self, titleblocks, parameter_names):
        self.titleblocks = titleblocks
        self.parameter_names = parameter_names
        self.changes = {}
        self.original_values = {}

        self._header_bg_brush = SolidBrush(Theme.HEADER_BG)
        self._frozen_header_brush = SolidBrush(Theme.FROZEN_HEADER_BG)
        self._header_border_pen = Pen(Theme.HEADER_BORDER)
        self._header_font = Font(Theme.FONT_FAMILY, 9.5, FontStyle.Bold)

        self.Text = "Edit Title Block Parameters"
        self.Size = Size(1400, 780)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.Sizable
        self.MinimumSize = Size(800, 400)
        self.BackColor = Theme.BG
        self.Font = Font(Theme.FONT_FAMILY, 9)

        # -------------------------------------------------------------------
        # BOTTOM: Button panel
        # -------------------------------------------------------------------
        btn_panel = Panel()
        btn_panel.Height = 62
        btn_panel.Dock = DockStyle.Bottom
        btn_panel.BackColor = Theme.PANEL_BG
        btn_panel.Padding = Padding(12)

        top_border = Panel()
        top_border.Height = 1
        top_border.Dock = DockStyle.Top
        top_border.BackColor = Theme.BORDER
        btn_panel.Controls.Add(top_border)

        self.cancel_btn = Button()
        self.cancel_btn.Text = "Cancel"
        self.cancel_btn.Size = Size(120, 36)
        self.cancel_btn.Dock = DockStyle.Right
        self.cancel_btn.Margin = Padding(8, 0, 0, 0)
        self.cancel_btn.Font = Font(Theme.FONT_FAMILY, 9.5)
        self.cancel_btn.FlatStyle = FlatStyle.Flat
        self.cancel_btn.FlatAppearance.BorderColor = Theme.BORDER
        self.cancel_btn.BackColor = Theme.PANEL_BG
        self.cancel_btn.ForeColor = Theme.TEXT
        self.cancel_btn.Click += self.on_cancel
        btn_panel.Controls.Add(self.cancel_btn)

        self.apply_btn = Button()
        self.apply_btn.Text = "Apply Changes"
        self.apply_btn.Size = Size(150, 36)
        self.apply_btn.Dock = DockStyle.Right
        self.apply_btn.BackColor = Theme.ACCENT
        self.apply_btn.ForeColor = Theme.ACCENT_TEXT
        self.apply_btn.FlatStyle = FlatStyle.Flat
        self.apply_btn.FlatAppearance.BorderSize = 0
        self.apply_btn.Font = Font(Theme.FONT_FAMILY, 9.5, FontStyle.Bold)
        self.apply_btn.Click += self.on_apply
        btn_panel.Controls.Add(self.apply_btn)

        self.fit_btn = Button()
        self.fit_btn.Text = "Fit Columns to Content"
        self.fit_btn.Size = Size(180, 36)
        self.fit_btn.Dock = DockStyle.Left
        self.fit_btn.Font = Font(Theme.FONT_FAMILY, 9.5)
        self.fit_btn.FlatStyle = FlatStyle.Flat
        self.fit_btn.FlatAppearance.BorderColor = Theme.BORDER
        self.fit_btn.BackColor = Theme.PANEL_BG
        self.fit_btn.ForeColor = Theme.TEXT
        self.fit_btn.Click += self.on_fit_columns
        btn_panel.Controls.Add(self.fit_btn)

        self.Controls.Add(btn_panel)

        # -------------------------------------------------------------------
        # TOP: Info label  (clean, full words, no unicode escapes)
        # -------------------------------------------------------------------
        info_panel = Panel()
        info_panel.Height = 38
        info_panel.Dock = DockStyle.Top
        info_panel.BackColor = Theme.PANEL_BG

        bottom_border = Panel()
        bottom_border.Height = 1
        bottom_border.Dock = DockStyle.Bottom
        bottom_border.BackColor = Theme.BORDER
        info_panel.Controls.Add(bottom_border)

        lbl = Label()
        lbl.Text = (
            "  {0} sheet(s)  |  {1} parameter(s)  |  "
            "Shift+Click = select range   "
            "Ctrl+C / Ctrl+V = copy / paste   "
            "Ctrl+A = select column"
        ).format(len(titleblocks), len(parameter_names))
        lbl.Dock = DockStyle.Fill
        lbl.Font = Font(Theme.FONT_FAMILY, 9)
        lbl.ForeColor = Theme.SUBTEXT
        lbl.TextAlign = ContentAlignment.MiddleLeft
        info_panel.Controls.Add(lbl)
        self.Controls.Add(info_panel)

        # -------------------------------------------------------------------
        # FILL: DataGridView
        # -------------------------------------------------------------------
        outer = Panel()
        outer.Dock = DockStyle.Fill
        outer.BackColor = Theme.BG
        outer.Padding = Padding(10)

        self.grid = DataGridView()
        self.grid.Dock = DockStyle.Fill
        self.grid.BackgroundColor = Theme.PANEL_BG
        self.grid.BorderStyle = BorderStyle.None
        self.grid.CellBorderStyle = DataGridViewCellBorderStyle.SingleHorizontal
        self.grid.ColumnHeadersBorderStyle = DataGridViewHeaderBorderStyle.Single
        self.grid.RowHeadersBorderStyle = DataGridViewHeaderBorderStyle.Single
        self.grid.GridColor = Theme.GRID_LINES
        self.grid.AllowUserToAddRows = False
        self.grid.AllowUserToDeleteRows = False
        self.grid.AllowUserToResizeRows = False
        self.grid.AllowUserToResizeColumns = True
        self.grid.RowTemplate.Height = self.ROW_HEIGHT
        self.grid.SelectionMode = DataGridViewSelectionMode.CellSelect
        self.grid.MultiSelect = True
        self.grid.ClipboardCopyMode = DataGridViewClipboardCopyMode.EnableWithoutHeaderText
        self.grid.EditMode = DataGridViewEditMode.EditOnKeystrokeOrF2
        self.grid.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.None
        self.grid.RowHeadersWidth = 32
        self.grid.RowHeadersVisible = True
        self.grid.EnableHeadersVisualStyles = False
        self.grid.ShowCellToolTips = True
        self.grid.ScrollBars = ScrollBars.Both

        self.grid.ColumnHeadersHeightSizeMode = \
            DataGridViewColumnHeadersHeightSizeMode.EnableResizing
        self.grid.ColumnHeadersHeight = self.HEADER_HEIGHT

        header_style = DataGridViewCellStyle()
        header_style.BackColor = Theme.HEADER_BG
        header_style.ForeColor = Theme.HEADER_FG
        header_style.Font = self._header_font
        header_style.SelectionBackColor = Theme.HEADER_BG
        header_style.SelectionForeColor = Theme.HEADER_FG
        header_style.WrapMode = DataGridViewTriState.True
        header_style.Alignment = DataGridViewContentAlignment.MiddleLeft
        header_style.Padding = Padding(8, 2, 8, 2)
        self.grid.ColumnHeadersDefaultCellStyle = header_style

        self.grid.CellPainting += self.on_cell_painting

        self.grid.DefaultCellStyle.Font = Font(Theme.FONT_FAMILY, 9.5)
        self.grid.DefaultCellStyle.ForeColor = Theme.TEXT
        self.grid.DefaultCellStyle.SelectionBackColor = Theme.ACCENT
        self.grid.DefaultCellStyle.SelectionForeColor = Color.White
        self.grid.DefaultCellStyle.Padding = Padding(6, 0, 6, 0)
        self.grid.AlternatingRowsDefaultCellStyle.BackColor = Theme.ROW_ALT
        self.grid.RowHeadersDefaultCellStyle.BackColor = Theme.FROZEN_BG
        self.grid.RowHeadersDefaultCellStyle.ForeColor = Theme.SUBTEXT

        col_sheet = DataGridViewTextBoxColumn()
        col_sheet.HeaderText = "Sheet #"
        col_sheet.Name = "SheetNum"
        col_sheet.ReadOnly = True
        col_sheet.Width = 105
        col_sheet.Frozen = True
        col_sheet.DefaultCellStyle.BackColor = Theme.FROZEN_BG
        col_sheet.DefaultCellStyle.SelectionBackColor = Theme.FROZEN_SEL
        col_sheet.DefaultCellStyle.SelectionForeColor = Theme.TEXT
        col_sheet.DefaultCellStyle.Font = Font(Theme.FONT_FAMILY, 9.5, FontStyle.Bold)
        self.grid.Columns.Add(col_sheet)

        col_name = DataGridViewTextBoxColumn()
        col_name.HeaderText = "Sheet Name"
        col_name.Name = "SheetName"
        col_name.ReadOnly = True
        col_name.Width = 220
        col_name.Frozen = True
        col_name.DefaultCellStyle.BackColor = Theme.FROZEN_BG
        col_name.DefaultCellStyle.SelectionBackColor = Theme.FROZEN_SEL
        col_name.DefaultCellStyle.SelectionForeColor = Theme.TEXT
        self.grid.Columns.Add(col_name)

        for pname in parameter_names:
            col = DataGridViewTextBoxColumn()
            col.HeaderText = pname
            col.Name = "PARAM__" + pname
            col.Width = estimate_column_width(pname, self._header_font)
            col.MinimumWidth = 90
            col.HeaderCell.ToolTipText = pname
            self.grid.Columns.Add(col)

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

        self.grid.KeyDown += self.on_key_down

        try:
            self.grid.Sort(self.grid.Columns["SheetNum"], SortOrder.Ascending)
        except Exception:
            pass

        outer.Controls.Add(self.grid)
        self.Controls.Add(outer)

    # -----------------------------
    # Event Handlers
    # -----------------------------

    def on_fit_columns(self, sender, e):
        """Auto-fit parameter columns to content with sensible minimums."""
        try:
            self.grid.AutoResizeColumns(
                DataGridViewAutoSizeColumnsMode.AllCellsExceptHeader)
            for col in self.grid.Columns:
                if not col.Name.startswith("PARAM__"):
                    continue
                needed = estimate_column_width(col.HeaderText, self._header_font)
                if col.Width < needed:
                    col.Width = needed
        except Exception as ex:
            MessageBox.Show("Could not fit columns: " + str(ex))

    def on_cell_painting(self, sender, e):
        """Cleanly render headers without visual clashes or glitches."""
        try:
            is_corner = (e.RowIndex == -1 and e.ColumnIndex == -1)
            is_header = (e.RowIndex == -1 and e.ColumnIndex >= 0)

            if not (is_corner or is_header):
                return

            g = e.Graphics
            rect = e.CellBounds

            is_frozen = False
            if is_header:
                col = self.grid.Columns[e.ColumnIndex]
                if col.Frozen:
                    is_frozen = True
            elif is_corner:
                is_frozen = True

            bg_brush = self._frozen_header_brush if is_frozen else self._header_bg_brush
            g.FillRectangle(bg_brush, rect)

            g.DrawLine(self._header_border_pen, rect.Left, rect.Bottom - 1, rect.Right, rect.Bottom - 1)
            g.DrawLine(self._header_border_pen, rect.Right - 1, rect.Top, rect.Right - 1, rect.Bottom - 1)

            if is_header:
                text = e.FormattedValue if e.FormattedValue is not None else ""
                text = str(text)

                text_rect = Rectangle(rect.X + 8, rect.Y + 2, rect.Width - 16, rect.Height - 4)
                flags = (
                    TextFormatFlags.Left |
                    TextFormatFlags.VerticalCenter |
                    TextFormatFlags.WordBreak |
                    TextFormatFlags.EndEllipsis
                )
                TextRenderer.DrawText(
                    g,
                    text,
                    self._header_font,
                    text_rect,
                    Theme.HEADER_FG,
                    flags
                )

            e.Handled = True

        except Exception:
            pass

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

            if len(lines) == 1 and "\t" not in lines[0] and self.grid.SelectedCells.Count > 1:
                value = lines[0]
                for cell in self.grid.SelectedCells:
                    if not cell.ReadOnly:
                        cell.Value = value
                return

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

    form = EditTableForm(titleblocks, selected)
    result = form.ShowDialog()

    if result != DialogResult.OK:
        return

    if not form.changes:
        forms.alert("No changes were made.")
        return

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

    msg = "Updated {} value(s).".format(success)
    if failed:
        msg += "\n\nFailed:\n" + "\n".join(failed[:20])
        if len(failed) > 20:
            msg += "\n... and {} more".format(len(failed) - 20)

    forms.alert(msg)


main()