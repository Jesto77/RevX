# -*- coding: utf-8 -*-
__title__ = "Fill Patterns"
__doc__ = "Add preset / create custom Model fill patterns (staggered & uniform) in the project."

import math
import clr
clr.AddReference("System")
from System.Collections.Generic import List
from System.Collections.ObjectModel import ObservableCollection
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs

from Autodesk.Revit.DB import (Transaction, FillPattern, FillPatternElement, FillGrid,
                               FillPatternTarget, FillPatternHostOrientation, UV)
from pyrevit import forms

doc = __revit__.ActiveUIDocument.Document
MM = 1.0 / 304.8  # mm -> internal feet


# ------------------------------------------------------------------ geometry
# Size convention: W = unit width (horizontal, X), H = unit height (vertical, Y)
def _grid(angle_deg, offset_mm, shift_mm=0.0, segments_mm=None):
    g = FillGrid()
    g.Angle = math.radians(angle_deg)
    g.Origin = UV(0, 0)
    g.Offset = offset_mm * MM
    g.Shift = shift_mm * MM
    if segments_mm:
        g.SetSegments(List[float]([s * MM for s in segments_mm]))
    return g


def uniform_grids(w, h):
    """Stack bond: continuous joints, units aligned."""
    return [_grid(0, h),     # horizontal joints every H
            _grid(90, w)]    # vertical joints every W


def staggered_grids(w, h):
    """Running bond: vertical joints of alternate rows sit at the centre of the unit below.
    Vertical lines every W/2; each is dashed (H drawn, H gap) and every next line is
    shifted by H, so even lines draw in even rows and odd lines in odd rows."""
    return [_grid(0, h),
            _grid(90, w / 2.0, shift_mm=h, segments_mm=[h, h])]


def pattern_name(w, h, kind):
    return "Model {}x{} {}".format(int(w), int(h), kind)


# ------------------------------------------------------------------ presets
WIDTHS = [200, 300, 400, 500, 600]
MAX_H = 1200
STEP = 100


def preset_sizes():
    out = []
    for w in WIDTHS:
        for h in range(w, MAX_H + 1, STEP):
            out.append((w, h))
    return out


# ------------------------------------------------------------------ view model
class PatternItem(INotifyPropertyChanged):
    def __init__(self, w, h, kind):
        self._checked = False
        self._handlers = []
        self.W, self.H, self.Kind = w, h, kind
        self.Name = pattern_name(w, h, kind)
        self.Label = "{} x {}".format(w, h)
        self.Status = "exists" if pattern_exists(self.Name) else ""

    def add_PropertyChanged(self, h):
        self._handlers.append(h)

    def remove_PropertyChanged(self, h):
        if h in self._handlers:
            self._handlers.remove(h)

    @property
    def Checked(self):
        return self._checked

    @Checked.setter
    def Checked(self, v):
        self._checked = bool(v)
        for h in list(self._handlers):
            h(self, PropertyChangedEventArgs("Checked"))

    def build(self):
        if self.Kind == "Staggered":
            return staggered_grids(self.W, self.H)
        return uniform_grids(self.W, self.H)


def pattern_exists(name):
    return FillPatternElement.GetFillPatternElementByName(
        doc, FillPatternTarget.Model, name) is not None


def apply_patterns(specs, overwrite):
    """specs: list of (name, grids_builder). Returns summary text."""
    created, updated, skipped, failed = [], [], [], []
    t = Transaction(doc, "Model Fill Patterns")
    t.Start()
    try:
        for name, builder in specs:
            try:
                existing = FillPatternElement.GetFillPatternElementByName(
                    doc, FillPatternTarget.Model, name)
                if existing is not None and not overwrite:
                    skipped.append(name)
                    continue
                fp = FillPattern(name, FillPatternTarget.Model,
                                 FillPatternHostOrientation.ToHost)
                fp.SetFillGrids(List[FillGrid](builder()))
                if existing is not None:
                    existing.SetFillPattern(fp)
                    updated.append(name)
                else:
                    FillPatternElement.Create(doc, fp)
                    created.append(name)
            except Exception as ex:
                failed.append("{}: {}".format(name, ex))
        t.Commit()
    except Exception as ex:
        t.RollBack()
        return "Failed:\n{}".format(ex)

    parts = []
    if created: parts.append("Created ({}):\n  ".format(len(created)) + "\n  ".join(created))
    if updated: parts.append("Updated ({}):\n  ".format(len(updated)) + "\n  ".join(updated))
    if skipped: parts.append("Skipped, already exist ({}):\n  ".format(len(skipped)) + "\n  ".join(skipped))
    if failed:  parts.append("Failed ({}):\n  ".format(len(failed)) + "\n  ".join(failed))
    return "\n\n".join(parts) if parts else "Nothing to do."


# ------------------------------------------------------------------ window
class FillPatternWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, "ui.xaml")
        self.stag_items = [PatternItem(w, h, "Staggered") for w, h in preset_sizes()]
        self.uni_items = [PatternItem(w, h, "Uniform") for w, h in preset_sizes()]
        self._bind()
        self.custom_changed(None, None)

    def _bind(self):
        for lb, items in ((self.staggered_list, self.stag_items),
                          (self.uniform_list, self.uni_items)):
            coll = ObservableCollection[object]()
            for i in items:
                coll.Add(i)
            lb.ItemsSource = coll

    def _refresh_status(self):
        for i in self.stag_items + self.uni_items:
            i.Status = "exists" if pattern_exists(i.Name) else ""
        self._bind()

    # ---- select helpers
    def stag_all(self, s, a):
        for i in self.stag_items: i.Checked = True

    def stag_none(self, s, a):
        for i in self.stag_items: i.Checked = False

    def uni_all(self, s, a):
        for i in self.uni_items: i.Checked = True

    def uni_none(self, s, a):
        for i in self.uni_items: i.Checked = False

    def close_click(self, s, a):
        self.Close()

    # ---- Add tab
    def add_click(self, s, a):
        chosen = [i for i in self.stag_items + self.uni_items if i.Checked]
        if not chosen:
            forms.alert("Tick at least one pattern.", title="Model Fill Patterns")
            return
        specs = [(i.Name, i.build) for i in chosen]
        msg = apply_patterns(specs, bool(self.overwrite_chk.IsChecked))
        self._refresh_status()
        forms.alert(msg, title="Model Fill Patterns")

    # ---- Create tab
    def _custom_values(self):
        try:
            w = float(self.custom_w.Text)
            h = float(self.custom_h.Text)
        except Exception:
            return None
        if w < 10 or h < 10 or w > 10000 or h > 10000:
            return None
        return w, h

    def _custom_kinds(self):
        idx = self.custom_type.SelectedIndex
        return ["Staggered"] if idx == 0 else ["Uniform"] if idx == 1 else ["Staggered", "Uniform"]

    def custom_changed(self, s, a):
        if not hasattr(self, "custom_preview"):
            return
        v = self._custom_values()
        if v is None:
            self.custom_preview.Text = "Enter width and height between 10 and 10000 mm."
            return
        names = [pattern_name(v[0], v[1], k) for k in self._custom_kinds()]
        self.custom_preview.Text = "Will create:  " + "   |   ".join(names)

    def custom_type_changed(self, s, a):
        self.custom_changed(s, a)

    def create_click(self, s, a):
        v = self._custom_values()
        if v is None:
            forms.alert("Enter width and height between 10 and 10000 mm.",
                        title="Model Fill Patterns")
            return
        w, h = v
        specs = []
        for k in self._custom_kinds():
            b = (lambda k=k: staggered_grids(w, h) if k == "Staggered" else uniform_grids(w, h))
            specs.append((pattern_name(w, h, k), b))
        msg = apply_patterns(specs, bool(self.overwrite_chk.IsChecked))
        self._refresh_status()
        forms.alert(msg, title="Model Fill Patterns")


if __name__ == "__main__":
    FillPatternWindow().ShowDialog()
