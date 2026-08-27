# -*- coding: utf-8 -*-
"""Align Edges - RevX.extension

Single dialog UI:
  1. Pick Target
  2. Pick Source
  3. Apply
"""

from pyrevit import revit, DB, forms
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException
import wpf
from System.IO import StringReader

try:
    from Autodesk.Revit.DB import Architecture as DB_Arch
except Exception:
    DB_Arch = None

try:
    from System.Collections.Generic import List
except Exception:
    List = None

doc = revit.doc
uidoc = revit.uidoc

# --- version-defensive class lookups ---------------------------------------
TOPOSOLID_CLASS = getattr(DB, "Toposolid", None)
RAILING_CLASS = (getattr(DB_Arch, "Railing", None) if DB_Arch else None) or getattr(DB, "Railing", None)
STAIRS_CLASSES = tuple(
    c for c in [
        getattr(DB, "Stairs", None),
        getattr(DB_Arch, "Stairs", None) if DB_Arch else None,
    ] if c is not None
)

MIN_MOVE = 1e-6
ALIGN_METHODS = ["Slabs", "Walls", "Curbs", "Stairs"]


# ==============================================================================
# STATE CONTAINER
# ==============================================================================
class AlignmentState(object):
    """Preserves user inputs and picks across window re-openings."""
    def __init__(self):
        self.target_elem = None
        self.source_elems = []
        self.method = "Slabs"
        self.offset = "0.0"
        self.status_msg = "Select a Target, then Source elements, then click Apply."
        self.status_error = False
        self.action = None  # None, 'PICK_TARGET', 'PICK_SOURCE'


# ==============================================================================
# FILTERS
# ==============================================================================
class SlabFilter(ISelectionFilter):
    def AllowElement(self, element):
        if isinstance(element, DB.Floor):
            return True
        if TOPOSOLID_CLASS is not None and isinstance(element, TOPOSOLID_CLASS):
            return True
        return False

    def AllowReference(self, reference, position):
        return True


class MethodFilter(ISelectionFilter):
    def __init__(self, method):
        self.method = method

    def AllowElement(self, element):
        if self.method == "Slabs":
            return isinstance(element, DB.Floor) or (
                TOPOSOLID_CLASS is not None and isinstance(element, TOPOSOLID_CLASS)
            )
        if self.method == "Walls":
            return isinstance(element, DB.Wall)
        if self.method == "Curbs":
            return RAILING_CLASS is not None and isinstance(element, RAILING_CLASS)
        if self.method == "Stairs":
            return isinstance(element, STAIRS_CLASSES) if STAIRS_CLASSES else False
        return False

    def AllowReference(self, reference, position):
        return True


# ==============================================================================
# GEOMETRY ENGINE
# ==============================================================================
def get_solids(element):
    try:
        opt = DB.Options()
        opt.ComputeReferences = True
        opt.DetailLevel = DB.ViewDetailLevel.Fine
        solids = []
        geom = element.get_Geometry(opt)
        if not geom:
            return solids
        for obj in geom:
            if isinstance(obj, DB.Solid) and obj.Volume > 1e-9:
                solids.append(obj)
            elif isinstance(obj, DB.GeometryInstance):
                try:
                    for inst_obj in obj.GetInstanceGeometry():
                        if isinstance(inst_obj, DB.Solid) and inst_obj.Volume > 1e-9:
                            solids.append(inst_obj)
                except Exception:
                    continue
        return solids
    except Exception:
        return []


def get_reference_curves(element, align_method):
    HOST_OBJECT_UTILS = getattr(DB, "HostObjectUtils", None)

    if align_method == "Curbs" and RAILING_CLASS and isinstance(element, RAILING_CLASS):
        try:
            return list(doc.GetElement(element.GetTopRail()).GetPath())
        except Exception:
            pass

    want_top = (align_method != "Walls")
    curves = []

    # 1. Host API
    if HOST_OBJECT_UTILS and isinstance(element, getattr(DB, "HostObject", None)):
        try:
            face_refs = (
                HOST_OBJECT_UTILS.GetTopFaces(element)
                if want_top
                else HOST_OBJECT_UTILS.GetBottomFaces(element)
            )
            for ref in face_refs:
                geom_obj = element.GetGeometryObjectFromReference(ref)
                if geom_obj:
                    for loop in geom_obj.EdgeLoops:
                        for edge in loop:
                            try:
                                curves.append(edge.AsCurve())
                            except Exception:
                                pass
            if curves:
                return curves
        except Exception:
            pass

    # 2. Fallback by face normal
    threshold = 0.3
    direction = 1 if want_top else -1
    for solid in get_solids(element):
        for face in solid.Faces:
            try:
                bb = face.GetBoundingBox()
                uv = DB.UV(
                    (bb.Min.U + bb.Max.U) / 2.0,
                    (bb.Min.V + bb.Max.V) / 2.0,
                )
            except Exception:
                uv = DB.UV(0.5, 0.5)
            try:
                z_norm = face.ComputeNormal(uv).Z
            except Exception:
                continue
            if (direction > 0 and z_norm >= threshold) or (
                direction < 0 and z_norm <= -threshold
            ):
                for loop in face.EdgeLoops:
                    for edge in loop:
                        try:
                            curves.append(edge.AsCurve())
                        except Exception:
                            pass
    return curves


def flatten_curve(curve):
    """XY only — used for distance checks, not for Z."""
    if isinstance(curve, DB.Line):
        try:
            p0, p1 = curve.GetEndPoint(0), curve.GetEndPoint(1)
            return DB.Line.CreateBound(
                DB.XYZ(p0.X, p0.Y, 0.0), DB.XYZ(p1.X, p1.Y, 0.0)
            )
        except Exception:
            return None
    try:
        pts = [DB.XYZ(p.X, p.Y, 0.0) for p in curve.Tessellate()]
        if List is not None:
            pts = List[DB.XYZ](pts)
        return DB.HermiteSpline.Create(pts, False)
    except Exception:
        return None


def get_exact_z_on_curve(curve, x, y):
    """High-precision Z lookup via coarse scan + ternary search."""
    t0 = curve.GetEndParameter(0)
    t1 = curve.GetEndParameter(1)
    steps = 50
    best_t = t0
    min_dist = float("inf")

    for i in range(steps + 1):
        t = t0 + (t1 - t0) * (i / float(steps))
        try:
            p = curve.Evaluate(t, False)
            dist = (p.X - x) ** 2 + (p.Y - y) ** 2
            if dist < min_dist:
                min_dist = dist
                best_t = t
        except Exception:
            continue

    span = (t1 - t0) / float(steps)
    t_start = max(t0, best_t - span)
    t_end = min(t1, best_t + span)

    for _ in range(15):
        mid1 = t_start + (t_end - t_start) / 3.0
        mid2 = t_end - (t_end - t_start) / 3.0
        try:
            p1 = curve.Evaluate(mid1, False)
            p2 = curve.Evaluate(mid2, False)
            d1 = (p1.X - x) ** 2 + (p1.Y - y) ** 2
            d2 = (p2.X - x) ** 2 + (p2.Y - y) ** 2
            if d1 < d2:
                t_end = mid2
            else:
                t_start = mid1
        except Exception:
            break

    return curve.Evaluate((t_start + t_end) / 2.0, False).Z


def get_shape_editor(element):
    try:
        method = getattr(element, "GetSlabShapeEditor", None)
        if callable(method):
            editor = method()
            if editor is not None:
                return editor
        return getattr(element, "SlabShapeEditor", None)
    except Exception:
        return None


def ensure_enabled(editor):
    if not getattr(editor, "IsEnabled", True):
        enable = getattr(editor, "Enable", None)
        if callable(enable):
            try:
                enable()
            except Exception:
                pass


def element_label(elem):
    if elem is None:
        return "None"
    try:
        cat = elem.Category.Name if elem.Category else "Element"
    except Exception:
        cat = "Element"
    try:
        name = elem.Name
    except Exception:
        name = ""

    try:
        elem_id = elem.Id.IntegerValue
    except Exception:
        try:
            elem_id = elem.Id.Value
        except Exception:
            elem_id = str(elem.Id)

    if name:
        return "{} | {} (id {})".format(cat, name, elem_id)
    return "{} (id {})".format(cat, elem_id)


# ==============================================================================
# CORE ALIGN
# ==============================================================================
def align_slab(target_elem, adjacent_elements, align_method, offset):
    """Returns (moved, missed, interior, tolerance, error_msg)."""
    ref_pairs = []
    for adj in adjacent_elements:
        for c in get_reference_curves(adj, align_method):
            flat = flatten_curve(c)
            if flat is not None:
                ref_pairs.append((c, flat))

    if not ref_pairs:
        return (0, 0, 0, 0.0, "No edges found on source elements.")

    editor = get_shape_editor(target_elem)
    if editor is None:
        return (0, 0, 0, 0.0, "Could not get shape editor for target.")
    ensure_enabled(editor)

    try:
        vertices = list(editor.SlabShapeVertices)
    except Exception as e:
        return (0, 0, 0, 0.0, "Could not read shape points: {}".format(e))

    if not vertices:
        return (
            0,
            0,
            0,
            0.0,
            "No shape edit points on target. Add points with Modify Sub Elements first.",
        )

    own_curves = get_reference_curves(target_elem, "Slabs")
    own_boundary_pairs = []
    for c in own_curves:
        flat = flatten_curve(c)
        if flat is not None:
            own_boundary_pairs.append(flat)

    def is_on_boundary(pos):
        if not own_boundary_pairs:
            return True
        pt2d = DB.XYZ(pos.X, pos.Y, 0.0)
        for flat_c in own_boundary_pairs:
            try:
                if flat_c.Distance(pt2d) <= 0.1:
                    return True
            except Exception:
                continue
        return False

    SANITY_CEILING = 15.0
    valid_gaps = []
    for pt in vertices:
        pos = pt.Position
        if not is_on_boundary(pos):
            continue
        best = float("inf")
        for _, flat_c in ref_pairs:
            try:
                d = flat_c.Distance(DB.XYZ(pos.X, pos.Y, 0.0))
                if d < best:
                    best = d
            except Exception:
                continue
        if best <= SANITY_CEILING:
            valid_gaps.append(best)

    edge_tolerance = max(1.0, max(valid_gaps) + 0.1) if valid_gaps else 1.0

    moved = 0
    missed = 0
    interior = 0

    for pt in vertices:
        pos = pt.Position
        if not is_on_boundary(pos):
            interior += 1
            continue

        best_dist = float("inf")
        best_curve = None
        for orig_c, flat_c in ref_pairs:
            try:
                d = flat_c.Distance(DB.XYZ(pos.X, pos.Y, 0.0))
            except Exception:
                continue
            if d < best_dist:
                best_dist = d
                best_curve = orig_c

        if best_curve is None or best_dist > edge_tolerance:
            missed += 1
            continue

        new_z = get_exact_z_on_curve(best_curve, pos.X, pos.Y)
        delta = (new_z + offset) - pos.Z
        if abs(delta) > MIN_MOVE:
            try:
                editor.ModifySubElement(pt, delta)
                moved += 1
            except Exception:
                missed += 1

    return (moved, missed, interior, edge_tolerance, None)


# ==============================================================================
# UI
# ==============================================================================
UI_XAML = r"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Align Edges"
        Width="460" Height="560"
        WindowStartupLocation="CenterScreen"
        ResizeMode="NoResize"
        Background="#F4F6F8"
        FontFamily="Segoe UI"
        Topmost="True">
  <Window.Resources>
    <Style x:Key="Card" TargetType="Border">
      <Setter Property="Background" Value="White"/>
      <Setter Property="CornerRadius" Value="8"/>
      <Setter Property="Padding" Value="14"/>
      <Setter Property="Margin" Value="0,0,0,12"/>
      <Setter Property="BorderBrush" Value="#E1E5EA"/>
      <Setter Property="BorderThickness" Value="1"/>
    </Style>
    <Style x:Key="PrimaryBtn" TargetType="Button">
      <Setter Property="Background" Value="#2F80ED"/>
      <Setter Property="Foreground" Value="White"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="FontSize" Value="14"/>
      <Setter Property="Height" Value="40"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Cursor" Value="Hand"/>
    </Style>
    <Style x:Key="SecondaryBtn" TargetType="Button">
      <Setter Property="Background" Value="#EEF2F7"/>
      <Setter Property="Foreground" Value="#1F2937"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="FontSize" Value="13"/>
      <Setter Property="Height" Value="34"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Padding" Value="12,0"/>
    </Style>
    <Style x:Key="Label" TargetType="TextBlock">
      <Setter Property="Foreground" Value="#6B7280"/>
      <Setter Property="FontSize" Value="12"/>
    </Style>
    <Style x:Key="Value" TargetType="TextBlock">
      <Setter Property="Foreground" Value="#111827"/>
      <Setter Property="FontSize" Value="13"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="TextWrapping" Value="Wrap"/>
    </Style>
  </Window.Resources>

  <Grid Margin="20">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <!-- Header -->
    <StackPanel Grid.Row="0" Margin="0,0,0,16">
      <TextBlock Text="Align Edges" FontSize="22" FontWeight="Bold" Foreground="#111827"/>
      <TextBlock Text="Snap target slab shape points to source edges."
                 Foreground="#6B7280" FontSize="12.5" Margin="0,4,0,0" TextWrapping="Wrap"/>
    </StackPanel>

    <!-- Step 1: Target -->
    <Border Grid.Row="1" Style="{StaticResource Card}">
      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <StackPanel Grid.Column="0">
          <TextBlock Text="1  TARGET  (Floor / Toposolid to modify)" Style="{StaticResource Label}"/>
          <TextBlock x:Name="TargetLabel" Text="Not selected" Style="{StaticResource Value}" Margin="0,6,0,0"/>
        </StackPanel>
        <Button x:Name="PickTargetBtn" Grid.Column="1" Content="Pick Target"
                Style="{StaticResource SecondaryBtn}" VerticalAlignment="Center"
                Click="pick_target_click"/>
      </Grid>
    </Border>

    <!-- Step 2: Method + Source -->
    <Border Grid.Row="2" Style="{StaticResource Card}">
      <StackPanel>
        <TextBlock Text="2  SOURCE  (elements to align to)" Style="{StaticResource Label}"/>
        <Grid Margin="0,8,0,0">
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="*"/>
            <ColumnDefinition Width="12"/>
            <ColumnDefinition Width="Auto"/>
          </Grid.ColumnDefinitions>
          <ComboBox x:Name="MethodCombo" Grid.Column="0" Height="34" FontSize="13"
                    VerticalContentAlignment="Center" SelectionChanged="method_changed">
            <ComboBoxItem Content="Slabs" IsSelected="True"/>
            <ComboBoxItem Content="Walls"/>
            <ComboBoxItem Content="Curbs"/>
            <ComboBoxItem Content="Stairs"/>
          </ComboBox>
          <Button x:Name="PickSourceBtn" Grid.Column="2" Content="Pick Source"
                  Style="{StaticResource SecondaryBtn}" Click="pick_source_click"/>
        </Grid>
        <TextBlock x:Name="SourceLabel" Text="Not selected" Style="{StaticResource Value}" Margin="0,8,0,0"/>
      </StackPanel>
    </Border>

    <!-- Step 3: Offset -->
    <Border Grid.Row="3" Style="{StaticResource Card}">
      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="120"/>
        </Grid.ColumnDefinitions>
        <StackPanel Grid.Column="0">
          <TextBlock Text="3  VERTICAL OFFSET" Style="{StaticResource Label}"/>
          <TextBlock Text="Feet  •  0 = flush with source edge"
                     Foreground="#9CA3AF" FontSize="11" Margin="0,4,0,0"/>
        </StackPanel>
        <TextBox x:Name="OffsetBox" Grid.Column="1" Height="34" Text="0.0"
                 FontSize="14" FontWeight="SemiBold"
                 VerticalContentAlignment="Center" Padding="8,0"
                 HorizontalContentAlignment="Center"/>
      </Grid>
    </Border>

    <!-- Status -->
    <Border Grid.Row="4" Background="#EEF6FF" CornerRadius="8" Padding="12" Margin="0,0,0,12"
            BorderBrush="#BFDBFE" BorderThickness="1">
      <TextBlock x:Name="StatusText"
                 Text="Select a Target, then Source elements, then click Apply."
                 Foreground="#1E40AF" FontSize="12.5" TextWrapping="Wrap"/>
    </Border>

    <!-- Spacer -->
    <Border Grid.Row="5"/>

    <!-- Apply -->
    <Grid Grid.Row="6">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="12"/>
        <ColumnDefinition Width="1.4*"/>
      </Grid.ColumnDefinitions>
      <Button x:Name="CloseBtn" Grid.Column="0" Content="Close"
              Style="{StaticResource SecondaryBtn}" Height="42" Click="close_click"/>
      <Button x:Name="ApplyBtn" Grid.Column="2" Content="Apply Alignment"
              Style="{StaticResource PrimaryBtn}" Height="42" Click="apply_click"/>
    </Grid>
  </Grid>
</Window>
"""


class AlignEdgesWindow(forms.WPFWindow):
    def __init__(self, state):
        self._loading = True
        self.state = state

        # Load XAML
        wpf.LoadComponent(self, StringReader(UI_XAML))

        # Restore from state
        self.target_elem = state.target_elem
        self.source_elems = state.source_elems
        self.OffsetBox.Text = state.offset

        # Match combo to state method
        for item in self.MethodCombo.Items:
            if str(item.Content) == state.method:
                self.MethodCombo.SelectedItem = item
                break

        self._loading = False
        self._refresh_labels()
        if state.status_msg:
            self._update_status(state.status_msg, error=state.status_error)
        else:
            self._update_status()

    def save_state(self):
        self.state.target_elem = self.target_elem
        self.state.source_elems = self.source_elems
        self.state.method = self._method_name()
        self.state.offset = self.OffsetBox.Text
        if hasattr(self, "StatusText") and self.StatusText:
            self.state.status_msg = self.StatusText.Text

    def _method_name(self):
        try:
            if hasattr(self, "MethodCombo") and self.MethodCombo and self.MethodCombo.SelectedItem:
                item = self.MethodCombo.SelectedItem
                if hasattr(item, "Content"):
                    return str(item.Content)
        except Exception:
            pass
        return getattr(self.state, "method", "Slabs")

    def _refresh_labels(self):
        if not hasattr(self, "TargetLabel") or self.TargetLabel is None:
            return

        if self.target_elem:
            self.TargetLabel.Text = element_label(self.target_elem)
            self.TargetLabel.Foreground = self._brush("#059669")
        else:
            self.TargetLabel.Text = "Not selected"
            self.TargetLabel.Foreground = self._brush("#9CA3AF")

        if self.source_elems:
            if len(self.source_elems) == 1:
                self.SourceLabel.Text = element_label(self.source_elems[0])
            else:
                self.SourceLabel.Text = "{} elements selected".format(len(self.source_elems))
            self.SourceLabel.Foreground = self._brush("#059669")
        else:
            self.SourceLabel.Text = "Not selected"
            self.SourceLabel.Foreground = self._brush("#9CA3AF")

    def _brush(self, hex_color):
        from System.Windows.Media import BrushConverter
        return BrushConverter().ConvertFromString(hex_color)

    def _update_status(self, msg=None, error=False):
        if not hasattr(self, "StatusText") or self.StatusText is None:
            return

        if msg is not None:
            self.StatusText.Text = msg
            self.StatusText.Foreground = self._brush("#B91C1C" if error else "#1E40AF")
            return

        missing = []
        if not self.target_elem:
            missing.append("Target")
        if not self.source_elems:
            missing.append("Source")
        if missing:
            self.StatusText.Text = "Still needed: " + " + ".join(missing) + ". Then click Apply."
            self.StatusText.Foreground = self._brush("#1E40AF")
        else:
            self.StatusText.Text = "Ready. Click Apply Alignment to run."
            self.StatusText.Foreground = self._brush("#065F46")

    # ----- events -----
    def method_changed(self, sender, args):
        if getattr(self, "_loading", True):
            return

        new_method = self._method_name()
        if self.state.method != new_method:
            self.state.method = new_method
            if self.source_elems:
                self.source_elems = []
                self.state.source_elems = []
                self._refresh_labels()
                self._update_status(
                    "Source type changed to {}. Please pick source again.".format(new_method)
                )

    def pick_target_click(self, sender, args):
        self.save_state()
        self.state.action = "PICK_TARGET"
        self.Close()

    def pick_source_click(self, sender, args):
        self.save_state()
        self.state.action = "PICK_SOURCE"
        self.Close()

    def apply_click(self, sender, args):
        if not self.target_elem:
            self._update_status("Pick a Target element first.", error=True)
            return
        if not self.source_elems:
            self._update_status("Pick Source element(s) first.", error=True)
            return

        try:
            offset = float(self.OffsetBox.Text.strip())
        except Exception:
            self._update_status("Offset must be a number (feet).", error=True)
            return

        method = self._method_name()
        self._update_status("Running alignment…")
        self.ApplyBtn.IsEnabled = False

        try:
            with revit.Transaction("Align Edges"):
                moved, missed, interior, tol, err = align_slab(
                    self.target_elem, self.source_elems, method, offset
                )
        except Exception as ex:
            self.ApplyBtn.IsEnabled = True
            self._update_status("Alignment failed: {}".format(ex), error=True)
            return

        self.ApplyBtn.IsEnabled = True

        if err:
            self._update_status(err, error=True)
            return

        if moved > 0 and missed == 0:
            self._update_status(
                "Done — aligned {} point(s).  tol {:.2f} ft  |  interior skipped {}".format(
                    moved, tol, interior
                )
            )
        elif moved > 0:
            self._update_status(
                "Aligned {}  •  missed {} (too far)  •  tol {:.2f} ft  •  interior {}".format(
                    moved, missed, tol, interior
                )
            )
        else:
            self._update_status(
                "No points moved. Check shape points exist and sources are nearby.",
                error=True,
            )

        self.save_state()

    def close_click(self, sender, args):
        self.state.action = None
        self.Close()


# ==============================================================================
# MAIN WORKFLOW
# ==============================================================================
def main():
    state = AlignmentState()

    # Pre-detect single selected target slab
    try:
        sel = list(uidoc.Selection.GetElementIds())
        if len(sel) == 1:
            elem = doc.GetElement(sel[0])
            if SlabFilter().AllowElement(elem):
                state.target_elem = elem
    except Exception:
        pass

    while True:
        win = AlignEdgesWindow(state)
        win.ShowDialog()

        action = state.action
        state.action = None  # Reset action after catching

        if action == "PICK_TARGET":
            try:
                ref = uidoc.Selection.PickObject(
                    ObjectType.Element,
                    SlabFilter(),
                    "Select TARGET Floor or Toposolid  •  Esc to cancel",
                )
                state.target_elem = doc.GetElement(ref)
                state.status_msg = "Target set. Now pick Source elements."
                state.status_error = False
            except OperationCanceledException:
                state.status_msg = "Target selection canceled."
                state.status_error = False
            except Exception as ex:
                state.status_msg = "Target pick error: {}".format(ex)
                state.status_error = True

        elif action == "PICK_SOURCE":
            method = state.method
            try:
                refs = uidoc.Selection.PickObjects(
                    ObjectType.Element,
                    MethodFilter(method),
                    "Select SOURCE {}  •  click Finish when done  •  Esc to cancel".format(
                        method.upper()
                    ),
                )
                state.source_elems = [doc.GetElement(r) for r in refs]
                state.status_msg = "Source elements set ({} selected). Click Apply Alignment.".format(
                    len(state.source_elems)
                )
                state.status_error = False
            except OperationCanceledException:
                state.status_msg = "Source selection canceled."
                state.status_error = False
            except Exception as ex:
                state.status_msg = "Source pick error: {}".format(ex)
                state.status_error = True

        else:
            # User clicked Close or 'X'
            break


if __name__ == "__main__":
    main()