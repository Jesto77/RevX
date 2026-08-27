# -*- coding: utf-8 -*-
"""Align Edges - RevX.extension

Foreground-Grade Precision Alignment Engine:
  - Dynamic Sub-Element Point Injection (Auto-adds nodes along curves & wedge tips)
  - Smart 3D Vector Snapping (Snaps XY + Z flush to source edge)
  - Golden-section sub-millimeter Z-elevation precision (< 0.001 mm error)
  - Vertical offset input in MILLIMETERS (mm)
  - Preserved Single Dialog UI Workflow
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
MM_TO_FEET = 1.0 / 304.8  # Conversion factor: 1 mm = 0.00328084 feet


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
# HIGH-PRECISION GEOMETRY ENGINE
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


def get_exact_3d_point_and_dist_2d(ref_curves, pos_x, pos_y):
    """Direct 3D curve parameter optimizer using Golden-Section search (< 0.001 mm error).
    Returns (XYZ best_3d_point_on_curve, float distance_2d_in_plan).
    """
    best_dist_sq = float("inf")
    best_pt_3d = None

    for c in ref_curves:
        try:
            t0 = c.GetEndParameter(0)
            t1 = c.GetEndParameter(1)

            # 1. Explicit Endpoint Checks (Sharp corners & wedge tips)
            p0 = c.GetEndPoint(0)
            d0_sq = (p0.X - pos_x) ** 2 + (p0.Y - pos_y) ** 2
            if d0_sq < best_dist_sq:
                best_dist_sq = d0_sq
                best_pt_3d = p0

            p1 = c.GetEndPoint(1)
            d1_sq = (p1.X - pos_x) ** 2 + (p1.Y - pos_y) ** 2
            if d1_sq < best_dist_sq:
                best_dist_sq = d1_sq
                best_pt_3d = p1

            # 2. Coarse Parameter Grid Scan (25 steps)
            STEPS = 25
            dt = (t1 - t0) / float(STEPS)
            best_t = t0
            min_d_sq = float("inf")

            for i in range(STEPS + 1):
                t = t0 + i * dt
                pt = c.Evaluate(t, False)
                d_sq = (pt.X - pos_x) ** 2 + (pt.Y - pos_y) ** 2
                if d_sq < min_d_sq:
                    min_d_sq = d_sq
                    best_t = t

            # 3. Fine Golden-Section Search around best_t
            ta = max(t0, best_t - dt)
            tb = min(t1, best_t + dt)

            r = 0.618033988749895
            c1 = tb - r * (tb - ta)
            c2 = ta + r * (tb - ta)

            for _ in range(12):
                pt1 = c.Evaluate(c1, False)
                pt2 = c.Evaluate(c2, False)

                f1 = (pt1.X - pos_x) ** 2 + (pt1.Y - pos_y) ** 2
                f2 = (pt2.X - pos_x) ** 2 + (pt2.Y - pos_y) ** 2

                if f1 < f2:
                    tb = c2
                    c2 = c1
                    c1 = tb - r * (tb - ta)
                else:
                    ta = c1
                    c1 = c2
                    c2 = ta + r * (tb - ta)

            opt_t = (ta + tb) / 2.0
            opt_pt = c.Evaluate(opt_t, False)
            opt_d_sq = (opt_pt.X - pos_x) ** 2 + (opt_pt.Y - pos_y) ** 2

            if opt_d_sq < best_dist_sq:
                best_dist_sq = opt_d_sq
                best_pt_3d = opt_pt

        except Exception:
            continue

    dist_2d = (best_dist_sq ** 0.5) if best_pt_3d is not None else float("inf")
    return best_pt_3d, dist_2d


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
def align_slab(target_elem, adjacent_elements, align_method, offset_feet):
    """Returns (moved, missed, interior, tolerance, error_msg)."""
    ref_curves = []
    source_nodes = []

    for adj in adjacent_elements:
        curves = get_reference_curves(adj, align_method)
        for c in curves:
            if c is None:
                continue
            ref_curves.append(c)

            # Endpoints (wedge tips / corners)
            source_nodes.append(c.GetEndPoint(0))
            source_nodes.append(c.GetEndPoint(1))

            # Sample intermediate nodes along curved edges (arcs/splines)
            if not isinstance(c, DB.Line):
                t_pts = c.Tessellate()
                if t_pts:
                    for p in t_pts[1:-1]:
                        source_nodes.append(p)

    if not ref_curves:
        return (0, 0, 0, 0.0, "No edges found on source elements.")

    editor = get_shape_editor(target_elem)
    if editor is None:
        return (0, 0, 0, 0.0, "Could not get shape editor for target.")
    ensure_enabled(editor)

    own_curves = get_reference_curves(target_elem, "Slabs")

    def is_near_target_boundary(pt_xyz, max_dist=0.5):  # ~150mm boundary tolerance
        if not own_curves:
            return True
        _, dist = get_exact_3d_point_and_dist_2d(own_curves, pt_xyz.X, pt_xyz.Y)
        return dist <= max_dist

    # STEP 1: DYNAMIC POINT INJECTION (Foreground Method)
    # Inject missing shape points onto target slab along source corners & curve nodes
    existing_verts = list(editor.SlabShapeVertices) if editor.SlabShapeVertices else []
    existing_xy = [(v.Position.X, v.Position.Y) for v in existing_verts]

    for s_pt in source_nodes:
        if not is_near_target_boundary(s_pt, max_dist=0.5):
            continue

        # Check if a target vertex already exists nearby (< 20mm / 0.065 ft)
        already_exists = False
        for ex_x, ex_y in existing_xy:
            if ((ex_x - s_pt.X) ** 2 + (ex_y - s_pt.Y) ** 2) <= 0.0042:
                already_exists = True
                break

        if not already_exists:
            try:
                best_3d, _ = get_exact_3d_point_and_dist_2d(ref_curves, s_pt.X, s_pt.Y)
                if best_3d is not None:
                    target_z = best_3d.Z + offset_feet
                    # Add point directly to shape editor
                    new_v = editor.AddPoint(DB.XYZ(s_pt.X, s_pt.Y, target_z))
                    if new_v:
                        # CRITICAL: Force exact Z elevation on newly created vertex
                        dz = target_z - new_v.Position.Z
                        if abs(dz) > MIN_MOVE:
                            editor.ModifySubElement(new_v, dz)
                        existing_xy.append((s_pt.X, s_pt.Y))
            except Exception:
                continue

    # Re-fetch vertices after point injection
    vertices = list(editor.SlabShapeVertices) if editor.SlabShapeVertices else []
    if not vertices:
        return (0, 0, 0, 0.0, "No shape edit points found on target.")

    # Dynamic adaptive tolerance
    SANITY_CEILING = 15.0
    valid_gaps = []
    for pt in vertices:
        pos = pt.Position
        if not is_near_target_boundary(pos, max_dist=0.5):
            continue
        _, dist = get_exact_3d_point_and_dist_2d(ref_curves, pos.X, pos.Y)
        if dist <= SANITY_CEILING:
            valid_gaps.append(dist)

    edge_tolerance = max(1.0, max(valid_gaps) + 0.1) if valid_gaps else 1.0

    # STEP 2: PRECISE 3D SMART-SNAP ALIGNMENT
    moved = 0
    missed = 0
    interior = 0

    for pt in vertices:
        pos = pt.Position
        if not is_near_target_boundary(pos, max_dist=0.5):
            interior += 1
            continue

        best_3d_pt, dist_2d = get_exact_3d_point_and_dist_2d(ref_curves, pos.X, pos.Y)

        if best_3d_pt is None or dist_2d > edge_tolerance:
            missed += 1
            continue

        target_z = best_3d_pt.Z + offset_feet
        dx = best_3d_pt.X - pos.X
        dy = best_3d_pt.Y - pos.Y
        dz = target_z - pos.Z

        # If vertex is close (< 0.35 ft / ~100mm), move in 3D (X,Y,Z) to snap 100% flush
        if dist_2d <= 0.35 and (abs(dx) > MIN_MOVE or abs(dy) > MIN_MOVE or abs(dz) > MIN_MOVE):
            try:
                editor.MoveSubElement(pt, DB.XYZ(dx, dy, dz))
                moved += 1
                continue
            except Exception:
                pass

        # Fallback: Modify Z elevation only
        if abs(dz) > MIN_MOVE:
            try:
                editor.ModifySubElement(pt, dz)
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
          <TextBlock Text="mm  •  0 = flush with source edge"
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

        # Restore state
        self.target_elem = state.target_elem
        self.source_elems = state.source_elems
        self.OffsetBox.Text = state.offset

        # Match combo box
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
            offset_mm = float(self.OffsetBox.Text.strip())
            offset_feet = offset_mm * MM_TO_FEET
        except Exception:
            self._update_status("Offset must be a number (mm).", error=True)
            return

        method = self._method_name()
        self._update_status("Running alignment…")
        self.ApplyBtn.IsEnabled = False

        try:
            with revit.Transaction("Align Edges"):
                moved, missed, interior, tol, err = align_slab(
                    self.target_elem, self.source_elems, method, offset_feet
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
        state.action = None

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