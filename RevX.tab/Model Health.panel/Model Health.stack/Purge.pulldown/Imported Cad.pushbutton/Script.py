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


def get_imported_cad_files(doc):
    """Get all imported CAD files (not linked) in the project."""
    all_imports = FilteredElementCollector(doc).OfClass(ImportInstance).ToElements()
    
    imported_cads = []
    for imp in all_imports:
        try:
            if not imp.IsLinked:
                imported_cads.append(imp)
        except:
            continue
    
    imported_cads.sort(key=lambda i: get_import_name(i))
    return imported_cads


def get_import_name(import_instance):
    """Get a friendly name for the import instance."""
    try:
        type_id = import_instance.GetTypeId()
        imp_type = import_instance.Document.GetElement(type_id)
        if imp_type:
            name_param = imp_type.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
            if name_param:
                name = name_param.AsString()
                if name:
                    return name
        return "Unnamed Import"
    except:
        return "Unnamed Import"


def get_view_info(doc, import_instance):
    """Get the view where the CAD is placed."""
    try:
        view_id = import_instance.OwnerViewId
        if view_id and view_id != ElementId.InvalidElementId:
            view = doc.GetElement(view_id)
            if view:
                return "View: {0}".format(view.Name)
        return "Model (All Views)"
    except:
        return "Unknown"


# Get imported CAD files
imported_cads = get_imported_cad_files(doc)


class CADCleanupForm(Form):
    def __init__(self, document, cads_list):
        self.doc = document
        self.cads_to_delete = cads_list
        self.filtered_cads = list(cads_list)
        self.last_clicked_index = -1
        self.InitializeComponent()
        self.UpdateListBox()
    
    def InitializeComponent(self):
        self.Text = "CAD Import Cleanup - Delete Imported CAD Files"
        self.Size = Size(700, 620)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.Sizable
        self.MinimumSize = Size(550, 450)
        self.TopMost = True
        
        self.lblTitle = Label()
        self.lblTitle.Text = "Imported CAD Files"
        self.lblTitle.Font = Font("Arial", 14, FontStyle.Bold)
        self.lblTitle.Location = Point(15, 15)
        self.lblTitle.Size = Size(400, 30)
        self.Controls.Add(self.lblTitle)
        
        self.lblInfo = Label()
        self.lblInfo.Text = "These are IMPORTED CAD files (not linked). Deleting will remove them permanently."
        self.lblInfo.ForeColor = Color.FromArgb(180, 0, 0)
        self.lblInfo.Font = Font("Arial", 9, FontStyle.Italic)
        self.lblInfo.Location = Point(15, 45)
        self.lblInfo.Size = Size(600, 20)
        self.Controls.Add(self.lblInfo)
        
        self.lblSearch = Label()
        self.lblSearch.Text = "Search:"
        self.lblSearch.Font = Font("Arial", 10)
        self.lblSearch.Location = Point(15, 75)
        self.lblSearch.Size = Size(55, 22)
        self.Controls.Add(self.lblSearch)
        
        self.txtSearch = TextBox()
        self.txtSearch.Location = Point(75, 73)
        self.txtSearch.Size = Size(500, 25)
        self.txtSearch.Font = Font("Arial", 10)
        self.txtSearch.TextChanged += self.OnSearchTextChanged
        self.Controls.Add(self.txtSearch)
        
        self.btnClearSearch = Button()
        self.btnClearSearch.Text = "X"
        self.btnClearSearch.Location = Point(580, 72)
        self.btnClearSearch.Size = Size(30, 25)
        self.btnClearSearch.Click += self.OnClearSearchClick
        self.Controls.Add(self.btnClearSearch)
        
        self.lblInstructions = Label()
        self.lblInstructions.Text = "SHIFT+Click: Select range | CTRL+Click: Toggle individual"
        self.lblInstructions.ForeColor = Color.Gray
        self.lblInstructions.Font = Font("Arial", 9)
        self.lblInstructions.Location = Point(15, 105)
        self.lblInstructions.Size = Size(400, 20)
        self.Controls.Add(self.lblInstructions)
        
        self.chkListCads = CheckedListBox()
        self.chkListCads.Location = Point(15, 130)
        self.chkListCads.Size = Size(655, 340)
        self.chkListCads.CheckOnClick = True
        self.chkListCads.BorderStyle = BorderStyle.FixedSingle
        self.chkListCads.Font = Font("Consolas", 9)
        self.chkListCads.Anchor = (AnchorStyles.Top | AnchorStyles.Bottom | 
                                    AnchorStyles.Left | AnchorStyles.Right)
        self.chkListCads.ItemCheck += self.OnItemCheck
        self.chkListCads.MouseDown += self.OnMouseDown
        self.Controls.Add(self.chkListCads)
        
        self.lblCount = Label()
        self.lblCount.Text = "Total: 0 | Checked: 0"
        self.lblCount.Font = Font("Arial", 10, FontStyle.Bold)
        self.lblCount.Location = Point(15, 480)
        self.lblCount.Size = Size(300, 25)
        self.lblCount.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.Controls.Add(self.lblCount)
        
        self.pnlButtons = Panel()
        self.pnlButtons.Location = Point(15, 510)
        self.pnlButtons.Size = Size(655, 45)
        self.pnlButtons.Anchor = AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
        self.Controls.Add(self.pnlButtons)
        
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
        
        self.btnDelete = Button()
        self.btnDelete.Text = "Delete Checked"
        self.btnDelete.Location = Point(410, 5)
        self.btnDelete.Size = Size(150, 35)
        self.btnDelete.Font = Font("Arial", 9, FontStyle.Bold)
        self.btnDelete.BackColor = Color.FromArgb(220, 53, 69)
        self.btnDelete.ForeColor = Color.White
        self.btnDelete.FlatStyle = FlatStyle.Flat
        self.btnDelete.Click += self.OnDeleteClick
        self.btnDelete.Enabled = False
        self.pnlButtons.Controls.Add(self.btnDelete)
        
        self.btnClose = Button()
        self.btnClose.Text = "Close"
        self.btnClose.Location = Point(570, 5)
        self.btnClose.Size = Size(80, 35)
        self.btnClose.Font = Font("Arial", 9)
        self.btnClose.BackColor = Color.FromArgb(52, 58, 64)
        self.btnClose.ForeColor = Color.White
        self.btnClose.FlatStyle = FlatStyle.Flat
        self.btnClose.Click += self.OnCloseClick
        self.pnlButtons.Controls.Add(self.btnClose)
    
    def RefreshCads(self):
        self.cads_to_delete = get_imported_cad_files(self.doc)
        self.filtered_cads = list(self.cads_to_delete)
        self.UpdateListBox()
    
    def UpdateListBox(self):
        self.chkListCads.Items.Clear()
        for cad in self.filtered_cads:
            try:
                cad_name = get_import_name(cad)
                view_info = get_view_info(self.doc, cad)
                display_name = "{0}  |  {1}".format(cad_name.ljust(35), view_info)
                self.chkListCads.Items.Add(display_name)
            except:
                self.chkListCads.Items.Add("<Unknown CAD Import>")
        self.last_clicked_index = -1
        self.UpdateCount()
    
    def UpdateCount(self):
        total = self.chkListCads.Items.Count
        checked = self.chkListCads.CheckedItems.Count
        self.lblCount.Text = "Total: {0} | Checked: {1}".format(total, checked)
        self.btnDelete.Enabled = checked > 0
    
    def OnSearchTextChanged(self, sender, args):
        search_text = self.txtSearch.Text.lower()
        if search_text:
            self.filtered_cads = [c for c in self.cads_to_delete 
                                   if search_text in get_import_name(c).lower() or
                                   search_text in get_view_info(self.doc, c).lower()]
        else:
            self.filtered_cads = list(self.cads_to_delete)
        self.UpdateListBox()
    
    def OnClearSearchClick(self, sender, args):
        self.txtSearch.Text = ""
    
    def OnMouseDown(self, sender, args):
        index = self.chkListCads.IndexFromPoint(args.Location)
        
        if index < 0 or index >= self.chkListCads.Items.Count:
            return
        
        if Control.ModifierKeys == Keys.Shift:
            if self.last_clicked_index >= 0 and self.last_clicked_index < self.chkListCads.Items.Count:
                new_state = not self.chkListCads.GetItemChecked(index)
                start_idx = min(self.last_clicked_index, index)
                end_idx = max(self.last_clicked_index, index)
                
                for i in range(start_idx, end_idx + 1):
                    self.chkListCads.SetItemChecked(i, new_state)
                
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
        for i in range(self.chkListCads.Items.Count):
            self.chkListCads.SetItemChecked(i, True)
        self.UpdateCount()
    
    def OnUncheckAllClick(self, sender, args):
        for i in range(self.chkListCads.Items.Count):
            self.chkListCads.SetItemChecked(i, False)
        self.UpdateCount()
    
    def OnInvertClick(self, sender, args):
        for i in range(self.chkListCads.Items.Count):
            current_state = self.chkListCads.GetItemChecked(i)
            self.chkListCads.SetItemChecked(i, not current_state)
        self.UpdateCount()
    
    def OnDeleteClick(self, sender, args):
        checked_indices = list(self.chkListCads.CheckedIndices)
        
        if not checked_indices:
            return
        
        selected_cads = [self.filtered_cads[i] for i in checked_indices]
        
        # Confirmation popup with list
        confirm_msg = "Are you sure you want to delete {0} imported CAD file(s)?\n\n".format(len(selected_cads))
        if len(selected_cads) <= 20:
            for cad in selected_cads:
                confirm_msg += "  - {0}\n".format(get_import_name(cad))
        else:
            for cad in selected_cads[:20]:
                confirm_msg += "  - {0}\n".format(get_import_name(cad))
            confirm_msg += "  ... and {0} more\n".format(len(selected_cads) - 20)
        
        confirm_msg += "\nThis will also remove the CAD type from the project.\nThis action cannot be undone!"
        
        result = MessageBox.Show(confirm_msg, "Confirm Deletion",
                                 MessageBoxButtons.YesNo, MessageBoxIcon.Warning)
        
        if result == DialogResult.Yes:
            self.DeleteCads(selected_cads)
    
    def DeleteCads(self, cads_to_delete):
        # Collect IDs first (both instance and type IDs)
        ids_to_delete = []
        type_ids_to_delete = set()
        
        for cad in cads_to_delete:
            try:
                ids_to_delete.append(cad.Id)
                type_id = cad.GetTypeId()
                if type_id and type_id != ElementId.InvalidElementId:
                    type_ids_to_delete.add(type_id)
            except:
                continue
        
        t = Transaction(self.doc, "Delete Imported CAD Files")
        
        try:
            t.Start()
            
            # First delete all instances
            for cad_id in ids_to_delete:
                sub_t = SubTransaction(self.doc)
                try:
                    sub_t.Start()
                    
                    element = self.doc.GetElement(cad_id)
                    if element is None:
                        sub_t.RollBack()
                        continue
                    
                    self.doc.Delete(cad_id)
                    sub_t.Commit()
                        
                except:
                    if sub_t.HasStarted() and not sub_t.HasEnded():
                        sub_t.RollBack()
            
            # Then try to delete the CAD types (silently ignore failures)
            for type_id in type_ids_to_delete:
                sub_t = SubTransaction(self.doc)
                try:
                    sub_t.Start()
                    
                    type_element = self.doc.GetElement(type_id)
                    if type_element is None:
                        sub_t.RollBack()
                        continue
                    
                    self.doc.Delete(type_id)
                    sub_t.Commit()
                        
                except:
                    # Silently ignore type deletion errors
                    if sub_t.HasStarted() and not sub_t.HasEnded():
                        sub_t.RollBack()
            
            t.Commit()
            
        except:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        
        # Refresh the list silently - no popups
        self.RefreshCads()
        self.txtSearch.Text = ""
    
    def OnCloseClick(self, sender, args):
        self.Close()


# Run the form directly
if len(imported_cads) > 0:
    form = CADCleanupForm(doc, imported_cads)
    form.ShowDialog()
else:
    MessageBox.Show("No imported CAD files found in the project.", 
                    "CAD Import Cleanup",
                    MessageBoxButtons.OK, MessageBoxIcon.Information)