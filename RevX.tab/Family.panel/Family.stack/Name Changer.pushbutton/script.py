# -*- coding: utf-8 -*-
"""Family Renamer.

1. Choose a category.
2. All loadable families of that category in the project are listed.
3. Live-preview renaming: prefix, suffix, find/replace, case change -
   with current -> new name shown side by side.
4. 'Edit in Notepad' opens all checked names in Notepad, one per line:
   edit them freely, save and close Notepad, and the edited names are
   pulled back into the preview.
5. Apply renames the checked families in one transaction (instant,
   no family reload needed).

Compatible with Revit 2018 through 2026+ (IronPython engines).
"""

__title__ = "Family\nRenamer"
__author__ = "pyRevit user"
__doc__ = "Rename families easily: pick a category, then use " \
          "prefix/suffix, find & replace, case tools or free-edit " \
          "all names in Notepad - with live preview."

import os
import tempfile
import subprocess
import codecs

from pyrevit import revit, forms, script

from Autodesk.Revit.DB import (
    Family,
    FamilySymbol,
    Element,
    ElementType,
    FilteredElementCollector,
    Transaction,
    CategoryType,
)

from System.Windows.Controls import (
    Grid, ColumnDefinition, TextBlock, CheckBox, Border,
)
from System.Windows import (
    GridLength, GridUnitType, Thickness, CornerRadius,
    VerticalAlignment, HorizontalAlignment, TextTrimming,
    WindowState,
)
from System.Windows.Media import SolidColorBrush, Color

logger = script.get_logger()
doc = revit.doc


# ------------------------------------------------------------- compat utils
def get_id_value(element_id):
    try:
        return element_id.Value          # Revit 2024+
    except AttributeError:
        return element_id.IntegerValue   # Revit <= 2023


def get_elem_name(element):
    """Safely read an element's name on any engine/version."""
    try:
        return element.Name
    except Exception:
        pass
    try:
        return Element.Name.__get__(element)
    except Exception:
        pass
    try:
        from Autodesk.Revit.DB import ElementType as _ET
        return _ET.Name.GetValue(element)
    except Exception:
        return None


def set_elem_name(element, new_name):
    """Safely set an element's name on any engine/version."""
    try:
        element.Name = new_name
        return True
    except AttributeError:
        Element.Name.__set__(element, new_name)
        return True


def brush(hexstr):
    """'#RRGGBB' -> SolidColorBrush"""
    h = hexstr.lstrip('#')
    return SolidColorBrush(Color.FromRgb(
        int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))


# ----------------------------------------------------------- title case util
def title_case(text):
    out = []
    cap_next = True
    for ch in text:
        if ch.isalpha():
            out.append(ch.upper() if cap_next else ch.lower())
            cap_next = False
        else:
            out.append(ch)
            cap_next = True
    return ''.join(out)


# ------------------------------------------------- STEP 1: choose category
class CategoryOption(forms.TemplateListItem):
    @property
    def name(self):
        return "{}  ({} names)".format(self.item[0], len(self.item[1]))


items_by_cat = {}

# ---- loadable families
for fam in FilteredElementCollector(doc).OfClass(Family).ToElements():
    if not fam.IsEditable or fam.IsInPlace:
        continue
    cat = fam.FamilyCategory
    cat_name_key = cat.Name if cat else "<no category>"
    items_by_cat.setdefault(cat_name_key, []).append(
        {'id': fam.Id, 'name': fam.Name, 'kind': 'family'})

# ---- system family types
for et in FilteredElementCollector(doc).WhereElementIsElementType() \
        .ToElements():

    if not isinstance(et, ElementType):
        continue

    if isinstance(et, FamilySymbol):
        continue

    cat = et.Category

    try:
        if cat is not None and cat.CategoryType not in (
                CategoryType.Model, CategoryType.Annotation):
            continue
    except Exception:
        pass

    type_name = get_elem_name(et)
    if not type_name:
        continue

    try:
        fam_name = et.FamilyName
    except Exception:
        fam_name = ''

    try:
        cat_key = cat.Name if cat is not None else "<no category>"
    except Exception:
        cat_key = "<no category>"

    items_by_cat.setdefault(cat_key, []).append(
        {'id': et.Id, 'name': type_name, 'kind': 'type',
         'label_prefix': fam_name or ''})

if not items_by_cat:
    forms.alert("No families or system types found in this project.",
                exitscript=True)

sorted_cats = sorted(items_by_cat.items(), key=lambda kv: kv[0].lower())

picked = forms.SelectFromList.show(
    [CategoryOption(item) for item in sorted_cats],
    title="Family Renamer - choose a category",
    button_name="Next: List Names",
    multiselect=False,
)
if not picked:
    script.exit()

cat_name, fam_data = picked
fam_data = sorted(fam_data,
                  key=lambda d: (d.get('label_prefix', ''),
                                 d['name'].lower()))


# --------------------------------------------------- STEP 2: rename window
class FamilyRenamerWindow(forms.WPFWindow):

    def __init__(self, xaml_file, category_name, fam_data):
        forms.WPFWindow.__init__(self, xaml_file)
        self.category_name = category_name
        self.fam_data = fam_data
        self.rows = []
        self.result = None
        self.manual_names = {}

        self.category_text.Text = u"\u25B6  {}   ({} families)".format(
            category_name, len(fam_data))

        self._build_rows()
        self._hook_events()
        self.update_preview()

    # ---------------------------------------------------------- UI build
    def _build_rows(self):
        alt = False
        for data in self.fam_data:
            border = Border()
            border.Background = brush('#2B2B3A') if alt else brush('#26263A')
            border.CornerRadius = CornerRadius(4)
            border.Padding = Thickness(4)
            border.Margin = Thickness(0, 1, 0, 1)
            alt = not alt

            g = Grid()
            c0 = ColumnDefinition(); c0.Width = GridLength(34)
            c1 = ColumnDefinition(); c1.Width = GridLength(1, GridUnitType.Star)
            c2 = ColumnDefinition(); c2.Width = GridLength(30)
            c3 = ColumnDefinition(); c3.Width = GridLength(1, GridUnitType.Star)
            g.ColumnDefinitions.Add(c0)
            g.ColumnDefinitions.Add(c1)
            g.ColumnDefinitions.Add(c2)
            g.ColumnDefinitions.Add(c3)

            chk = CheckBox()
            chk.IsChecked = True
            chk.VerticalAlignment = VerticalAlignment.Center
            chk.Margin = Thickness(8, 0, 0, 0)
            chk.Checked += self._any_change
            chk.Unchecked += self._any_change
            Grid.SetColumn(chk, 0)

            cur = TextBlock()
            if data.get('label_prefix'):
                cur.Text = u"{} : {}".format(data['label_prefix'],
                                             data['name'])
            else:
                cur.Text = data['name']
            cur.Foreground = brush('#C8C8DA')
            cur.FontSize = 13
            cur.VerticalAlignment = VerticalAlignment.Center
            cur.TextTrimming = TextTrimming.CharacterEllipsis
            Grid.SetColumn(cur, 1)

            arrow = TextBlock()
            arrow.Text = u'\u2192'
            arrow.Foreground = brush('#6A6A8A')
            arrow.FontSize = 14
            arrow.HorizontalAlignment = HorizontalAlignment.Center
            arrow.VerticalAlignment = VerticalAlignment.Center
            Grid.SetColumn(arrow, 2)

            new = TextBlock()
            new.Text = data['name']
            new.Foreground = brush('#7FE0A7')
            new.FontSize = 13
            new.VerticalAlignment = VerticalAlignment.Center
            new.TextTrimming = TextTrimming.CharacterEllipsis
            Grid.SetColumn(new, 3)

            g.Children.Add(chk)
            g.Children.Add(cur)
            g.Children.Add(arrow)
            g.Children.Add(new)
            border.Child = g
            self.rows_panel.Children.Add(border)

            self.rows.append({'chk': chk, 'new_tb': new,
                              'arrow': arrow, 'data': data})

    def _hook_events(self):
        self.prefix_tb.TextChanged += self._any_change
        self.suffix_tb.TextChanged += self._any_change
        self.find_tb.TextChanged += self._any_change
        self.replace_tb.TextChanged += self._any_change
        self.case_cb.SelectionChanged += self._any_change
        self.all_btn.Click += self._select_all
        self.none_btn.Click += self._select_none
        self.invert_btn.Click += self._select_invert
        self.notepad_btn.Click += self._edit_in_notepad
        self.cancel_btn.Click += self._cancel
        self.apply_btn.Click += self._apply

    # --------------------------------------------------------- selection
    def _select_all(self, sender, args):
        for row in self.rows:
            row['chk'].IsChecked = True
        self.update_preview()

    def _select_none(self, sender, args):
        for row in self.rows:
            row['chk'].IsChecked = False
        self.update_preview()

    def _select_invert(self, sender, args):
        for row in self.rows:
            row['chk'].IsChecked = not row['chk'].IsChecked
        self.update_preview()

    def _any_change(self, sender, args):
        self.update_preview()

    # ----------------------------------------------------- name building
    def _compute_name(self, original):
        name = original

        find = self.find_tb.Text
        if find:
            name = name.replace(find, self.replace_tb.Text)

        name = self.prefix_tb.Text + name + self.suffix_tb.Text

        case_idx = self.case_cb.SelectedIndex
        if case_idx == 1:
            name = name.upper()
        elif case_idx == 2:
            name = name.lower()
        elif case_idx == 3:
            name = title_case(name)

        return name

    # --------------------------------------------------- notepad editing
    def _edit_in_notepad(self, sender, args):
        """Dump checked names to a temp txt, open Notepad (blocking),
        read the edited lines back as manual name overrides."""
        checked_rows = [r for r in self.rows if r['chk'].IsChecked]
        if not checked_rows:
            self.status_text.Text = "Nothing checked - check some " \
                                    "families first."
            self.status_text.Foreground = brush('#FF7070')
            return

        # current preview names so notepad starts from what you see
        lines = [r.get('_newname', r['data']['name']) for r in checked_rows]

        tmp_path = os.path.join(
            tempfile.gettempdir(),
            "pyrevit_family_rename_{}.txt".format(os.getpid()))

        header = (
            u"; FAMILY RENAMER - edit the names below, one per line.\r\n"
            u"; Do NOT add or delete lines - line order = family order.\r\n"
            u"; Lines starting with ';' are ignored.\r\n"
            u"; Save the file and CLOSE Notepad to apply.\r\n"
            u";\r\n")

        try:
            with codecs.open(tmp_path, 'w', encoding='utf-8-sig') as f:
                f.write(header)
                f.write(u"\r\n".join(lines))
        except Exception as err:
            self.status_text.Text = "Could not write temp file: " \
                                    "{}".format(err)
            self.status_text.Foreground = brush('#FF7070')
            return

        # Show status while Notepad is open
        self.status_text.Text = u"Notepad is open \u2013 save and " \
                                u"CLOSE it to continue\u2026"
        self.status_text.Foreground = brush('#FFD080')

        # Minimize instead of Hide to keep WPF message pump alive
        self.WindowState = WindowState.Minimized

        try:
            proc = subprocess.Popen(['notepad.exe', tmp_path])
            proc.wait()
        except Exception as err:
            self.WindowState = WindowState.Normal
            self.Activate()
            self.status_text.Text = "Could not open Notepad: " \
                                    "{}".format(err)
            self.status_text.Foreground = brush('#FF7070')
            return
        finally:
            # Restore and bring window to front
            self.WindowState = WindowState.Normal
            self.Activate()
            self.Focus()

        # read back
        try:
            with codecs.open(tmp_path, 'r', encoding='utf-8-sig') as f:
                content = f.read()
        except Exception as err:
            self.status_text.Text = "Could not read edited file: " \
                                    "{}".format(err)
            self.status_text.Foreground = brush('#FF7070')
            return
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

        edited = [ln.strip() for ln in content.splitlines()
                  if ln.strip() and not ln.strip().startswith(';')]

        if len(edited) != len(checked_rows):
            forms.alert(
                "Line count mismatch!\n\n"
                "Expected {} names but found {} lines.\n"
                "No changes applied - do not add or remove lines."
                .format(len(checked_rows), len(edited)))
            self.status_text.Text = "Notepad edit cancelled " \
                                    "(line count mismatch)."
            self.status_text.Foreground = brush('#FF7070')
            return

        # store as manual overrides (these win over prefix/suffix etc.)
        for row, newname in zip(checked_rows, edited):
            self.manual_names[get_id_value(row['data']['id'])] = newname

        # refresh preview immediately
        self.update_preview()

        self.status_text.Text = u"\u2713 Notepad edits loaded \u2013 " \
                                u"review below, then click Apply."
        self.status_text.Foreground = brush('#7FE0A7')

    # ------------------------------------------------------ live preview
    def update_preview(self):
        new_names = []
        changed = 0

        for row in self.rows:
            original = row['data']['name']
            if row['chk'].IsChecked:
                manual = self.manual_names.get(
                    get_id_value(row['data']['id']))
                if manual is not None:
                    newname = manual
                else:
                    newname = self._compute_name(original)
            else:
                newname = original
            row['_newname'] = newname
            row['new_tb'].Text = newname
            new_names.append((row, newname))
            if newname != original and row['chk'].IsChecked:
                changed += 1

        seen = {}
        for row, n in new_names:
            key = (row['data'].get('label_prefix', ''), n)
            seen.setdefault(key, []).append(row)

        dup_count = 0
        for row, n in new_names:
            key = (row['data'].get('label_prefix', ''), n)
            is_dup = len(seen[key]) > 1
            is_empty = (row['chk'].IsChecked and not n.strip())
            invalid = any(ch in n for ch in '\\:{}[]|;<>?`~')
            if row['chk'].IsChecked and (is_dup or is_empty or invalid):
                row['new_tb'].Foreground = brush('#FF7070')
                row['_invalid'] = True
                dup_count += 1
            elif row['chk'].IsChecked and n != row['data']['name']:
                row['new_tb'].Foreground = brush('#7FE0A7')
                row['_invalid'] = False
            else:
                row['new_tb'].Foreground = brush('#6A6A8A')
                row['_invalid'] = False

        if dup_count:
            self.status_text.Text = \
                u"\u26A0  {} invalid/duplicate name(s) - fix before applying" \
                .format(dup_count)
            self.status_text.Foreground = brush('#FF7070')
            self.apply_btn.IsEnabled = False
        else:
            self.status_text.Text = "{} of {} families will be renamed" \
                .format(changed, len(self.rows))
            self.status_text.Foreground = brush('#9A9AB0')
            self.apply_btn.IsEnabled = changed > 0

    # ------------------------------------------------------------ buttons
    def _cancel(self, sender, args):
        self.result = None
        self.Close()

    def _apply(self, sender, args):
        renames = []
        for row in self.rows:
            if not row['chk'].IsChecked:
                continue
            if row.get('_invalid'):
                continue
            newname = row.get('_newname', row['data']['name'])
            if newname != row['data']['name']:
                renames.append((row['data']['id'], newname))
        self.result = renames
        self.Close()


# ------------------------------------------------------------------- run UI
xaml_path = script.get_bundle_file('ui.xaml')
win = FamilyRenamerWindow(xaml_path, cat_name, fam_data)
win.ShowDialog()

if not win.result:
    script.exit()

# ------------------------------------------------------------ apply renames
renamed, failed = [], []

t = Transaction(doc, "Rename Families ({})".format(cat_name))
t.Start()
try:
    for elem_id, new_name in win.result:
        elem = doc.GetElement(elem_id)
        if elem is None:
            failed.append((str(get_id_value(elem_id)), "element not found"))
            continue
        old_name = get_elem_name(elem) or str(get_id_value(elem_id))
        try:
            set_elem_name(elem, new_name)
            renamed.append((old_name, new_name))
        except Exception as err:
            failed.append((old_name, str(err)))
    t.Commit()
except Exception as terr:
    if t.HasStarted():
        t.RollBack()
    forms.alert("Transaction failed:\n{}".format(terr), exitscript=True)

# --------------------------------------------------------- silent summary
if renamed:
    logger.info("Renamed {} families in '{}'".format(
        len(renamed), cat_name))
    for old, new in renamed:
        logger.info("  {} -> {}".format(old, new))

if failed:
    # only show alert if something actually failed
    msg_lines = ["Failed to rename {} item(s):".format(len(failed))]
    for name, err in failed:
        msg_lines.append("  {} - {}".format(name, err))
    forms.alert("\n".join(msg_lines))