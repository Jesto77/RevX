import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import TaskDialog
from System.Windows.Forms import (
    Form, Button, Label, CheckedListBox,
    DialogResult, MessageBox,
    MessageBoxButtons, MessageBoxIcon, FormStartPosition,
    BorderStyle, FormBorderStyle, AnchorStyles, TextBox,
    Panel, FlatStyle, Keys, Control
)
from System.Drawing import Point, Size, Font, FontStyle, Color

# Get the current document
doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


def get_unused_view_templates(doc):
    """Get all view templates that are not applied to any view."""
    all_views = FilteredElementCollector(doc).OfClass(View).ToElements()
    
    all_templates = []
    used_template_ids = set()
    
    for view in all_views:
        try:
            if view.IsTemplate:
                all_templates.append(view)
            else:
                template_id = view.ViewTemplateId
                if template_id and template_id != ElementId.InvalidElementId:
                    used_template_ids.add(str(template_id))
        except:
            continue
    
    unused_templates = []
    for template in all_templates:
        try:
            if str(template.Id) not in used_template_ids:
                unused_templates.append(template)
        except:
            continue
    
    unused_templates.sort(key=lambda t: t.Name)
    return unused_templates


# Get unused templates
unused_templates = get_unused_view_templates(doc)


class TemplateCleanupForm(Form):
    def __init__(self, document, templates_list):
        self.doc = document
        self.templates_to_delete = templates_list
        self.filtered_templates = list(templates_list)
        self.last_clicked_index = -1
        self.InitializeComponent()
        self.UpdateListBox()
    
    def InitializeComponent(self):
        self.Text = "View Template Cleanup - Delete Unused Templates"
        self.Size = Size(600, 620)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.Sizable
        self.MinimumSize = Size(500, 450)
        self.TopMost = True
        
        # Title Label
        self.lblTitle = Label()
        self.lblTitle.Text = "Unused View Templates"
        self.lblTitle.Font = Font("Arial", 14, FontStyle.Bold)
        self.lblTitle.Location = Point(15, 15)
        self.lblTitle.Size = Size(400, 30)
        self.Controls.Add(self.lblTitle)
        
        # Info Label
        self.lblInfo = Label()
        self.lblInfo.Text = "These templates are not applied to any view in the project."
        self.lblInfo.ForeColor = Color.FromArgb(0, 100, 0)
        self.lblInfo.Font = Font("Arial", 9, FontStyle.Italic)
        self.lblInfo.Location = Point(15, 45)
        self.lblInfo.Size = Size(500, 20)
        self.Controls.Add(self.lblInfo)
        
        # Search Label
        self.lblSearch = Label()
        self.lblSearch.Text = "Search:"
        self.lblSearch.Font = Font("Arial", 10)
        self.lblSearch.Location = Point(15, 75)
        self.lblSearch.Size = Size(55, 22)
        self.Controls.Add(self.lblSearch)
        
        # Search TextBox
        self.txtSearch = TextBox()
        self.txtSearch.Location = Point(75, 73)
        self.txtSearch.Size = Size(400, 25)
        self.txtSearch.Font = Font("Arial", 10)
        self.txtSearch.TextChanged += self.OnSearchTextChanged
        self.Controls.Add(self.txtSearch)
        
        # Clear Search Button
        self.btnClearSearch = Button()
        self.btnClearSearch.Text = "X"
        self.btnClearSearch.Location = Point(480, 72)
        self.btnClearSearch.Size = Size(30, 25)
        self.btnClearSearch.Click += self.OnClearSearchClick
        self.Controls.Add(self.btnClearSearch)
        
        # Instructions Label
        self.lblInstructions = Label()
        self.lblInstructions.Text = "SHIFT+Click: Select range | CTRL+Click: Toggle individual"
        self.lblInstructions.ForeColor = Color.Gray
        self.lblInstructions.Font = Font("Arial", 9)
        self.lblInstructions.Location = Point(15, 105)
        self.lblInstructions.Size = Size(400, 20)
        self.Controls.Add(self.lblInstructions)
        
        # CheckedListBox for templates
        self.chkListTemplates = CheckedListBox()
        self.chkListTemplates.Location = Point(15, 130)
        self.chkListTemplates.Size = Size(555, 340)
        self.chkListTemplates.CheckOnClick = True
        self.chkListTemplates.BorderStyle = BorderStyle.FixedSingle
        self.chkListTemplates.Font = Font("Consolas", 9)
        self.chkListTemplates.Anchor = (AnchorStyles.Top | AnchorStyles.Bottom | 
                                         AnchorStyles.Left | AnchorStyles.Right)
        self.chkListTemplates.ItemCheck += self.OnItemCheck
        self.chkListTemplates.MouseDown += self.OnMouseDown
        self.Controls.Add(self.chkListTemplates)
        
        # Count Label
        self.lblCount = Label()
        self.lblCount.Text = "Total: 0 | Checked: 0"
        self.lblCount.Font = Font("Arial", 10, FontStyle.Bold)
        self.lblCount.Location = Point(15, 480)
        self.lblCount.Size = Size(300, 25)
        self.lblCount.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.Controls.Add(self.lblCount)
        
        # Button Panel
        self.pnlButtons = Panel()
        self.pnlButtons.Location = Point(15, 510)
        self.pnlButtons.Size = Size(555, 45)
        self.pnlButtons.Anchor = AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
        self.Controls.Add(self.pnlButtons)
        
        # Check All Button
        self.btnCheckAll = Button()
        self.btnCheckAll.Text = "Check All"
        self.btnCheckAll.Location = Point(0, 5)
        self.btnCheckAll.Size = Size(85, 35)
        self.btnCheckAll.Font = Font("Arial", 9)
        self.btnCheckAll.BackColor = Color.FromArgb(40, 167, 69)
        self.btnCheckAll.ForeColor = Color.White
        self.btnCheckAll.FlatStyle = FlatStyle.Flat
        self.btnCheckAll.Click += self.OnCheckAllClick
        self.pnlButtons.Controls.Add(self.btnCheckAll)
        
        # Uncheck All Button
        self.btnUncheckAll = Button()
        self.btnUncheckAll.Text = "Uncheck All"
        self.btnUncheckAll.Location = Point(95, 5)
        self.btnUncheckAll.Size = Size(85, 35)
        self.btnUncheckAll.Font = Font("Arial", 9)
        self.btnUncheckAll.BackColor = Color.FromArgb(108, 117, 125)
        self.btnUncheckAll.ForeColor = Color.White
        self.btnUncheckAll.FlatStyle = FlatStyle.Flat
        self.btnUncheckAll.Click += self.OnUncheckAllClick
        self.pnlButtons.Controls.Add(self.btnUncheckAll)
        
        # Invert Selection Button
        self.btnInvert = Button()
        self.btnInvert.Text = "Invert"
        self.btnInvert.Location = Point(190, 5)
        self.btnInvert.Size = Size(65, 35)
        self.btnInvert.Font = Font("Arial", 9)
        self.btnInvert.BackColor = Color.FromArgb(23, 162, 184)
        self.btnInvert.ForeColor = Color.White
        self.btnInvert.FlatStyle = FlatStyle.Flat
        self.btnInvert.Click += self.OnInvertClick
        self.pnlButtons.Controls.Add(self.btnInvert)
        
        # Delete Button
        self.btnDelete = Button()
        self.btnDelete.Text = "Delete Checked"
        self.btnDelete.Location = Point(310, 5)
        self.btnDelete.Size = Size(150, 35)
        self.btnDelete.Font = Font("Arial", 9, FontStyle.Bold)
        self.btnDelete.BackColor = Color.FromArgb(220, 53, 69)
        self.btnDelete.ForeColor = Color.White
        self.btnDelete.FlatStyle = FlatStyle.Flat
        self.btnDelete.Click += self.OnDeleteClick
        self.btnDelete.Enabled = False
        self.pnlButtons.Controls.Add(self.btnDelete)
        
        # Close Button
        self.btnClose = Button()
        self.btnClose.Text = "Close"
        self.btnClose.Location = Point(470, 5)
        self.btnClose.Size = Size(80, 35)
        self.btnClose.Font = Font("Arial", 9)
        self.btnClose.BackColor = Color.FromArgb(52, 58, 64)
        self.btnClose.ForeColor = Color.White
        self.btnClose.FlatStyle = FlatStyle.Flat
        self.btnClose.Click += self.OnCloseClick
        self.pnlButtons.Controls.Add(self.btnClose)
    
    def RefreshTemplates(self):
        self.templates_to_delete = get_unused_view_templates(self.doc)
        self.filtered_templates = list(self.templates_to_delete)
        self.UpdateListBox()
    
    def UpdateListBox(self):
        self.chkListTemplates.Items.Clear()
        for template in self.filtered_templates:
            try:
                view_type = str(template.ViewType).ljust(20)
                display_name = "[{0}] {1}".format(view_type, template.Name)
                self.chkListTemplates.Items.Add(display_name)
            except:
                self.chkListTemplates.Items.Add(template.Name)
        self.last_clicked_index = -1
        self.UpdateCount()
    
    def UpdateCount(self):
        total = self.chkListTemplates.Items.Count
        checked = self.chkListTemplates.CheckedItems.Count
        self.lblCount.Text = "Total: {0} | Checked: {1}".format(total, checked)
        self.btnDelete.Enabled = checked > 0
    
    def OnSearchTextChanged(self, sender, args):
        search_text = self.txtSearch.Text.lower()
        if search_text:
            self.filtered_templates = [t for t in self.templates_to_delete 
                                        if search_text in t.Name.lower() or
                                        search_text in str(t.ViewType).lower()]
        else:
            self.filtered_templates = list(self.templates_to_delete)
        self.UpdateListBox()
    
    def OnClearSearchClick(self, sender, args):
        self.txtSearch.Text = ""
    
    def OnMouseDown(self, sender, args):
        index = self.chkListTemplates.IndexFromPoint(args.Location)
        
        if index < 0 or index >= self.chkListTemplates.Items.Count:
            return
        
        if Control.ModifierKeys == Keys.Shift:
            if self.last_clicked_index >= 0 and self.last_clicked_index < self.chkListTemplates.Items.Count:
                new_state = not self.chkListTemplates.GetItemChecked(index)
                start_idx = min(self.last_clicked_index, index)
                end_idx = max(self.last_clicked_index, index)
                
                for i in range(start_idx, end_idx + 1):
                    self.chkListTemplates.SetItemChecked(i, new_state)
                
                self.UpdateCount()
            else:
                self.last_clicked_index = index
        elif Control.ModifierKeys == Keys.Control:
            self.last_clicked_index = index
        else:
            self.last_clicked_index = index
    
    def OnItemCheck(self, sender, args):
        self.BeginInvoke(System.Action(self.UpdateCount))
    
    def OnCheckAllClick(self, sender, args):
        for i in range(self.chkListTemplates.Items.Count):
            self.chkListTemplates.SetItemChecked(i, True)
        self.UpdateCount()
    
    def OnUncheckAllClick(self, sender, args):
        for i in range(self.chkListTemplates.Items.Count):
            self.chkListTemplates.SetItemChecked(i, False)
        self.UpdateCount()
    
    def OnInvertClick(self, sender, args):
        for i in range(self.chkListTemplates.Items.Count):
            current_state = self.chkListTemplates.GetItemChecked(i)
            self.chkListTemplates.SetItemChecked(i, not current_state)
        self.UpdateCount()
    
    def OnDeleteClick(self, sender, args):
        checked_indices = list(self.chkListTemplates.CheckedIndices)
        
        if not checked_indices:
            return
        
        selected_templates = [self.filtered_templates[i] for i in checked_indices]
        
        # Confirmation popup with list
        confirm_msg = "Are you sure you want to delete {0} view template(s)?\n\n".format(len(selected_templates))
        if len(selected_templates) <= 20:
            for template in selected_templates:
                confirm_msg += "  - {0}\n".format(template.Name)
        else:
            for template in selected_templates[:20]:
                confirm_msg += "  - {0}\n".format(template.Name)
            confirm_msg += "  ... and {0} more\n".format(len(selected_templates) - 20)
        
        confirm_msg += "\nThis action cannot be undone!"
        
        result = MessageBox.Show(confirm_msg, "Confirm Deletion",
                                 MessageBoxButtons.YesNo, MessageBoxIcon.Warning)
        
        if result == DialogResult.Yes:
            self.DeleteTemplates(selected_templates)
    
    def DeleteTemplates(self, templates_to_delete):
        t = Transaction(self.doc, "Delete Unused View Templates")
        t.Start()
        
        try:
            for template in templates_to_delete:
                try:
                    template_id = template.Id
                except:
                    continue
                
                sub_t = SubTransaction(self.doc)
                try:
                    sub_t.Start()
                    
                    if self.doc.GetElement(template_id) is None:
                        sub_t.RollBack()
                        continue
                    
                    self.doc.Delete(template_id)
                    sub_t.Commit()
                    
                except:
                    if sub_t.HasStarted() and not sub_t.HasEnded():
                        sub_t.RollBack()
            
            t.Commit()
            
        except:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        
        # Refresh the list silently
        self.RefreshTemplates()
        self.txtSearch.Text = ""
    
    def OnCloseClick(self, sender, args):
        self.Close()


# Run the form directly (no analysis popup)
if len(unused_templates) > 0:
    form = TemplateCleanupForm(doc, unused_templates)
    form.ShowDialog()
else:
    MessageBox.Show("All view templates are currently in use.\nNothing to delete!", 
                    "View Template Cleanup",
                    MessageBoxButtons.OK, MessageBoxIcon.Information)