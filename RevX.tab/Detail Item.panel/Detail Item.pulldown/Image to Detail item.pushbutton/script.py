# -*- coding: utf-8 -*-
"""
Trace a picked linked or imported image in the active view into smooth single-line Arcs and Lines,
build a new Detail Item family, save it, load it, and place it at the exact location and size.

Pipeline:
1. Prompts user to pick a linked or imported image element in the active view.
2. Extracts source image file and exact instance dimensions (width & height in feet).
3. Prompts user to specify a Family Name and select a Save Folder.
4. Processes image at high resolution (MAX_DIM=600) with smart binarization.
5. Thins raster strokes to 1-pixel centerlines via Zhang-Suen Thinning.
6. Clusters multi-pixel line junctions and connects chain endpoints directly to junction centroids to eliminate all line gaps.
7. Traces continuous stroke chains between junction clusters or closed loops.
8. Automatically detects True Circles (rendered as 2 semi-circular Revit DB.Arcs),
   smooth open DB.Arcs for petal curves, and straight DB.Lines for sharp geometry.
9. Creates a new Detail Item family containing native detail curves matching the exact size.
10. Saves the .rfa family file, loads it into the project, and places an instance at the exact same location.
"""

__title__ = 'Image Trace to\nDetail Item'
__author__ = 'Jesto Joy'
__doc__ = 'Pick a linked or imported image in the view to trace it into a Detail Item family with detail lines and arcs.'

import os
import re
import math
import tempfile

import clr
clr.AddReference('System.Drawing')
from System.Drawing import Bitmap, Rectangle, Graphics
from System.Drawing.Drawing2D import InterpolationMode
from System.Drawing.Imaging import PixelFormat, ImageLockMode, ImageFormat
from System import Array, Byte
from System.Runtime.InteropServices import Marshal
from System.Collections.Generic import List

from pyrevit import revit, DB, UI, forms, script

doc = revit.doc
uidoc = revit.uidoc
app = doc.Application
logger = script.get_logger()

MAX_DIM = 600      # Grid resolution for crisp line work
MAX_CURVES = 1500  # Safety cap on maximum detail curves


class FamilyLoadOption(DB.IFamilyLoadOptions):
    """Custom family load options handler to automatically overwrite existing families."""
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        try:
            overwriteParameterValues.Value = True
        except Exception:
            pass
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        try:
            source.Value = DB.FamilySource.Family
            overwriteParameterValues.Value = True
        except Exception:
            pass
        return True


def safe_get_element_name(element):
    """Safely get element or type name without throwing AttributeError in IronPython."""
    if not element:
        return "Image"
    try:
        if hasattr(element, "Name"):
            name = getattr(element, "Name", None)
            if name:
                return str(name)
    except Exception:
        pass

    try:
        p = element.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
        if p and p.HasValue and p.AsString():
            return p.AsString()
    except Exception:
        pass

    try:
        p = element.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p and p.HasValue and p.AsString():
            return p.AsString()
    except Exception:
        pass

    return "Image"


def pick_target_image(view):
    """Prompt user to pick a linked or imported image element in the view."""
    sel_ids = uidoc.Selection.GetElementIds()
    if sel_ids and len(sel_ids) == 1:
        el = doc.GetElement(sel_ids[0])
        if el and (isinstance(el, DB.ImageInstance) or (el.Category and el.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_RasterImages))):
            return el

    try:
        ref = uidoc.Selection.PickObject(
            UI.Selection.ObjectType.Element,
            'Click to pick an image (linked or imported) in the current view'
        )
        if ref:
            el = doc.GetElement(ref.ElementId)
            if isinstance(el, DB.ImageInstance):
                return el
            try:
                if el and el.Category and el.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_RasterImages):
                    return el
            except Exception:
                pass
            forms.alert('The selected element is not a linked or imported image.', exitscript=True)
    except Exception:
        script.exit()

    return None


def extract_image_file(image_inst):
    """Extract local image file path or save bitmap from ImageType."""
    image_type_id = image_inst.GetTypeId()
    image_type = doc.GetElement(image_type_id)
    if not image_type:
        return None, "DetailItem_Image"

    type_name = safe_get_element_name(image_type)
    img_path = None

    try:
        p = getattr(image_type, "Path", None)
        if p and os.path.exists(p):
            img_path = p
    except Exception:
        pass

    if not img_path:
        for method_name in ["GetPath", "GetSourcePath"]:
            if hasattr(image_type, method_name):
                try:
                    p = getattr(image_type, method_name)()
                    if p and os.path.exists(str(p)):
                        img_path = str(p)
                        break
                except Exception:
                    pass

    if not img_path:
        try:
            param = image_type.get_Parameter(DB.BuiltInParameter.RASTER_SYMBOL_FILENAME)
            if param and param.HasValue and param.AsString():
                p = param.AsString()
                if os.path.exists(p):
                    img_path = p
        except Exception:
            pass

    if not img_path and hasattr(image_type, "GetImage"):
        try:
            bmp = image_type.GetImage()
            if bmp:
                temp_dir = tempfile.gettempdir()
                eid_val = image_inst.Id.Value if hasattr(image_inst.Id, "Value") else image_inst.Id.IntegerValue
                temp_file = os.path.join(temp_dir, "revx_img_{}.png".format(eid_val))
                bmp.Save(temp_file, ImageFormat.Png)
                img_path = temp_file
        except Exception as ex:
            logger.debug("Could not export bitmap via GetImage: {}".format(ex))

    return img_path, type_name


def get_image_size_and_location(image_inst, view):
    """Extract width (ft), height (ft), and center position (XYZ) of image instance."""
    p_w = image_inst.get_Parameter(DB.BuiltInParameter.RASTER_SHEETWIDTH)
    p_h = image_inst.get_Parameter(DB.BuiltInParameter.RASTER_SHEETHEIGHT)

    width_ft = p_w.AsDouble() if (p_w and p_w.HasValue) else getattr(image_inst, "Width", 0.0)
    height_ft = p_h.AsDouble() if (p_h and p_h.HasValue) else getattr(image_inst, "Height", 0.0)

    bbox = image_inst.get_BoundingBox(view)
    if bbox:
        pt = (bbox.Min + bbox.Max) * 0.5
    else:
        loc = image_inst.Location
        pt = loc.Point if isinstance(loc, DB.LocationPoint) else DB.XYZ.Zero

    return width_ft, height_ft, pt


# --------------------------------------------------------------------------
# Smooth Single-Line Vectorization Engine
# --------------------------------------------------------------------------
def load_grayscale_grid(image_path):
    """Load image into downsampled 2D grayscale grid."""
    src = Bitmap(image_path)
    try:
        w, h = src.Width, src.Height
        scale = min(1.0, float(MAX_DIM) / float(max(w, h)))
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))

        if new_w != w or new_h != h:
            resized = Bitmap(new_w, new_h)
            g = Graphics.FromImage(resized)
            try:
                g.InterpolationMode = InterpolationMode.HighQualityBicubic
                g.DrawImage(src, Rectangle(0, 0, new_w, new_h))
            finally:
                g.Dispose()
            bmp = resized
        else:
            bmp = src

        needs_dispose_32 = False
        if bmp.PixelFormat != PixelFormat.Format32bppArgb:
            bmp32 = bmp.Clone(Rectangle(0, 0, new_w, new_h), PixelFormat.Format32bppArgb)
            needs_dispose_32 = True
        else:
            bmp32 = bmp

        rect = Rectangle(0, 0, new_w, new_h)
        bmp_data = bmp32.LockBits(rect, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb)
        try:
            stride = bmp_data.Stride
            byte_count = stride * new_h
            buffer = Array.CreateInstance(Byte, byte_count)
            Marshal.Copy(bmp_data.Scan0, buffer, 0, byte_count)
        finally:
            bmp32.UnlockBits(bmp_data)

        gray = [[0.0] * new_w for _ in range(new_h)]
        for y in range(new_h):
            row_offset = y * stride
            row = gray[y]
            for x in range(new_w):
                idx = row_offset + x * 4
                b = buffer[idx]
                gg = buffer[idx + 1]
                r = buffer[idx + 2]
                row[x] = 0.299 * r + 0.587 * gg + 0.114 * b

        if needs_dispose_32:
            bmp32.Dispose()
        if bmp is not src:
            bmp.Dispose()

        return gray, new_w, new_h
    finally:
        src.Dispose()


def otsu_threshold(gray, w, h):
    """Compute optimal Otsu threshold for binarization."""
    hist = [0] * 256
    total = w * h
    for row in gray:
        for v in row:
            hist[int(v)] += 1

    sum_all = sum(i * hist[i] for i in range(256))
    sum_b = 0.0
    w_b = 0
    max_var = -1.0
    threshold = 127
    for i in range(256):
        w_b += hist[i]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += i * hist[i]
        m_b = sum_b / w_b
        m_f = (sum_all - sum_b) / w_f
        var_between = w_b * w_f * (m_b - m_f) ** 2
        if var_between > max_var:
            max_var = var_between
            threshold = i
    return threshold


def binarize_smart(gray, w, h):
    """Binarize preserving thin lines without creating gaps."""
    thresh = otsu_threshold(gray, w, h)
    boosted_thresh = min(245, int(thresh * 1.12) + 8)
    return [[gray[y][x] <= boosted_thresh for x in range(w)] for y in range(h)]


def zhang_suen_thinning(mask, w, h):
    """Thin binary mask to 1-pixel wide centerlines using Zhang-Suen Thinning."""
    grid = [[1 if mask[y][x] else 0 for x in range(w)] for y in range(h)]

    def get_nbrs(x, y):
        return [
            grid[y-1][x] if y > 0 else 0,
            grid[y-1][x+1] if y > 0 and x < w-1 else 0,
            grid[y][x+1] if x < w-1 else 0,
            grid[y+1][x+1] if y < h-1 and x < w-1 else 0,
            grid[y+1][x] if y < h-1 else 0,
            grid[y+1][x-1] if y < h-1 and x > 0 else 0,
            grid[y][x-1] if x > 0 else 0,
            grid[y-1][x-1] if y > 0 and x > 0 else 0
        ]

    def transitions(nbrs):
        n = nbrs + [nbrs[0]]
        return sum(1 for i in range(8) if n[i] == 0 and n[i+1] == 1)

    changing = True
    passes = 0
    while changing and passes < 16:
        passes += 1
        changing = False

        to_remove = []
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                if grid[y][x] == 1:
                    nbrs = get_nbrs(x, y)
                    b = sum(nbrs)
                    if 2 <= b <= 6:
                        if transitions(nbrs) == 1:
                            p2, p3, p4, p5, p6, p7, p8, p9 = nbrs
                            if (p2 * p4 * p6 == 0) and (p4 * p6 * p8 == 0):
                                to_remove.append((x, y))
        if to_remove:
            changing = True
            for x, y in to_remove:
                grid[y][x] = 0

        to_remove = []
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                if grid[y][x] == 1:
                    nbrs = get_nbrs(x, y)
                    b = sum(nbrs)
                    if 2 <= b <= 6:
                        if transitions(nbrs) == 1:
                            p2, p3, p4, p5, p6, p7, p8, p9 = nbrs
                            if (p2 * p4 * p8 == 0) and (p2 * p6 * p8 == 0):
                                to_remove.append((x, y))
        if to_remove:
            changing = True
            for x, y in to_remove:
                grid[y][x] = 0

    return grid


def find_and_cluster_junctions(skeleton, w, h):
    """
    Find junction pixels (>= 3 neighbors) and group adjacent raw junction pixels into clusters.
    Prevents junction clusters from fragmenting long continuous stroke chains.
    """
    raw_junc = set()
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            if skeleton[y][x] == 1:
                cnt = 0
                for dx in [-1, 0, 1]:
                    for dy in [-1, 0, 1]:
                        if (dx != 0 or dy != 0) and skeleton[y+dy][x+dx] == 1:
                            cnt += 1
                if cnt >= 3:
                    raw_junc.add((x, y))

    junc_map = [[-1] * w for _ in range(h)]
    cluster_centers = {}
    visited = set()
    cluster_id = 0

    for pt in raw_junc:
        if pt in visited:
            continue
        queue = [pt]
        visited.add(pt)
        cluster_pts = []
        while queue:
            curr = queue.pop(0)
            cluster_pts.append(curr)
            cx, cy = curr
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    if dx == 0 and dy == 0:
                        continue
                    nbr = (cx + dx, cy + dy)
                    if nbr in raw_junc and nbr not in visited:
                        visited.add(nbr)
                        queue.append(nbr)

        avg_x = sum(p[0] for p in cluster_pts) / float(len(cluster_pts))
        avg_y = sum(p[1] for p in cluster_pts) / float(len(cluster_pts))
        cluster_centers[cluster_id] = (avg_x, avg_y)

        for p in cluster_pts:
            junc_map[p[1]][p[0]] = cluster_id

        cluster_id += 1

    return junc_map, cluster_centers


def extract_clean_chains(skeleton, junc_map, cluster_centers, w, h):
    """
    Trace continuous stroke chains between junction clusters, endpoints, or closed loops.
    Attach exact junction cluster centroids to chain start and end points to eliminate line gaps.
    """
    visited_edges = set()
    visited_path_pixels = set()
    chains = []

    def get_skel_nbrs(x, y):
        nbrs = []
        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and skeleton[ny][nx] == 1:
                    nbrs.append((nx, ny))
        return nbrs

    start_point_info = []
    for y in range(h):
        for x in range(w):
            if skeleton[y][x] == 1 and junc_map[y][x] == -1:
                nbrs = get_skel_nbrs(x, y)
                adj_junc_ids = [junc_map[ny][nx] for nx, ny in nbrs if junc_map[ny][nx] != -1]
                if len(nbrs) == 1 or adj_junc_ids:
                    start_point_info.append(((x, y), adj_junc_ids[0] if adj_junc_ids else None))

    for sp, start_junc_id in start_point_info:
        nbrs = get_skel_nbrs(sp[0], sp[1])
        for nx, ny in nbrs:
            if junc_map[ny][nx] != -1:
                continue

            edge = (min(sp, (nx, ny)), max(sp, (nx, ny)))
            if edge in visited_edges:
                continue

            chain_pixels = [sp, (nx, ny)]
            visited_edges.add(edge)
            visited_path_pixels.add(sp)
            visited_path_pixels.add((nx, ny))

            curr = (nx, ny)
            prev = sp
            end_junc_id = None

            while True:
                curr_nbrs = get_skel_nbrs(curr[0], curr[1])
                adj_juncs = [junc_map[my][mx] for mx, my in curr_nbrs if junc_map[my][mx] != -1]
                if adj_juncs:
                    end_junc_id = adj_juncs[0]
                    break

                next_pts = [p for p in curr_nbrs if p != prev and junc_map[p[1]][p[0]] == -1]
                if not next_pts:
                    break
                nxt = next_pts[0]
                nxt_edge = (min(curr, nxt), max(curr, nxt))
                if nxt_edge in visited_edges:
                    break
                visited_edges.add(nxt_edge)
                visited_path_pixels.add(nxt)
                chain_pixels.append(nxt)
                prev = curr
                curr = nxt

            chain_coords = [(float(p[0]), float(p[1])) for p in chain_pixels]

            if start_junc_id is not None and start_junc_id in cluster_centers:
                chain_coords.insert(0, cluster_centers[start_junc_id])

            if end_junc_id is not None and end_junc_id in cluster_centers:
                chain_coords.append(cluster_centers[end_junc_id])

            if len(chain_coords) >= 3:
                chains.append(chain_coords)

    # Isolated closed loops (no junctions nearby, e.g. standalone circle)
    for y in range(h):
        for x in range(w):
            if skeleton[y][x] == 1 and junc_map[y][x] == -1 and (x, y) not in visited_path_pixels:
                sp = (x, y)
                nbrs = get_skel_nbrs(x, y)
                if not nbrs:
                    continue
                nx, ny = nbrs[0]
                chain_pixels = [sp, (nx, ny)]
                visited_path_pixels.add(sp)
                visited_path_pixels.add((nx, ny))
                curr = (nx, ny)
                prev = sp
                while True:
                    curr_nbrs = get_skel_nbrs(curr[0], curr[1])
                    next_pts = [p for p in curr_nbrs if p != prev and p not in visited_path_pixels and junc_map[p[1]][p[0]] == -1]
                    if not next_pts:
                        break
                    nxt = next_pts[0]
                    visited_path_pixels.add(nxt)
                    chain_pixels.append(nxt)
                    prev = curr
                    curr = nxt
                if len(chain_pixels) >= 6:
                    chain_coords = [(float(p[0]), float(p[1])) for p in chain_pixels]
                    chains.append(chain_coords)

    return chains


# --------------------------------------------------------------------------
# Geometric Curve Fitting & Smoothing Engine
# --------------------------------------------------------------------------
def smooth_point_chain(pts, iterations=3):
    """Apply moving average smoothing to eliminate pixel stair-stepping while preserving exact endpoints."""
    if len(pts) <= 3:
        return pts

    curr = list(pts)
    for _ in range(iterations):
        smoothed = [curr[0]]
        for i in range(1, len(curr) - 1):
            p0 = curr[i - 1]
            p1 = curr[i]
            p2 = curr[i + 1]
            sx = 0.25 * p0.X + 0.5 * p1.X + 0.25 * p2.X
            sy = 0.25 * p0.Y + 0.5 * p1.Y + 0.25 * p2.Y
            smoothed.append(DB.XYZ(sx, sy, 0.0))
        smoothed.append(curr[-1])
        curr = smoothed
    return curr


def point_to_line_distance(pt, line_start, line_end):
    """Distance from 3D point pt to line segment (line_start -> line_end)."""
    vec = line_end - line_start
    length = vec.GetLength()
    if length < 1e-6:
        return pt.DistanceTo(line_start)
    u = ((pt.X - line_start.X) * vec.X + (pt.Y - line_start.Y) * vec.Y) / (length * length)
    u = max(0.0, min(1.0, u))
    proj = line_start + vec * u
    return pt.DistanceTo(proj)


def try_create_arc(p0, p1, p2):
    """Attempt to create a Revit DB.Arc passing through p0 (start), p1 (end), and p2 (mid point)."""
    try:
        v01 = p1 - p0
        v02 = p2 - p0
        cross = v01.CrossProduct(v02)
        if cross.GetLength() > 1e-4:
            return DB.Arc.Create(p0, p1, p2)
    except Exception:
        pass
    return None


def process_open_stroke(pts, max_tol):
    """Fit open point sequence into DB.Line, DB.Arc, or split recursively at maximum deviation."""
    if len(pts) < 2:
        return []

    p_start = pts[0]
    p_end = pts[-1]
    dist_chord = p_start.DistanceTo(p_end)

    if dist_chord < 1e-4:
        return []

    if len(pts) == 2:
        try:
            return [DB.Line.CreateBound(p_start, p_end)]
        except Exception:
            return []

    # 1. Straight Line Check
    max_dev = 0.0
    max_idx = 0
    for i in range(1, len(pts) - 1):
        dev = point_to_line_distance(pts[i], p_start, p_end)
        if dev > max_dev:
            max_dev = dev
            max_idx = i

    if max_dev <= max_tol:
        try:
            return [DB.Line.CreateBound(p_start, p_end)]
        except Exception:
            pass

    # 2. Single Open Arc Check
    mid_idx = len(pts) // 2
    p_mid = pts[mid_idx]
    
    v1 = p_end - p_start
    v2 = p_mid - p_start
    cross_len = v1.CrossProduct(v2).GetLength()

    if cross_len > 1e-4:
        arc = try_create_arc(p_start, p_end, p_mid)
        if arc:
            rad = arc.Radius
            center = arc.Center
            if 0.005 < rad < 50.0 * dist_chord:
                arc_fits = True
                for i in range(1, len(pts) - 1):
                    dist_to_center = pts[i].DistanceTo(center)
                    if abs(dist_to_center - rad) > (max_tol * 1.8):
                        arc_fits = False
                        break
                if arc_fits:
                    return [arc]

    # 3. Recursive Split at Maximum Deviation Point
    if max_idx > 0 and max_idx < len(pts) - 1:
        left_curves = process_open_stroke(pts[:max_idx + 1], max_tol)
        right_curves = process_open_stroke(pts[max_idx:], max_tol)
        return left_curves + right_curves
    else:
        try:
            return [DB.Line.CreateBound(p_start, p_end)]
        except Exception:
            return []


def process_chain_to_curves(pts, max_tol):
    """
    Process point chain (XYZ in feet) and return Revit DB.Curves.
    Automatically detects True Circles (2 semi-circular DB.Arcs), open DB.Arcs, or DB.Lines.
    """
    if len(pts) < 2:
        return []

    p_start = pts[0]
    p_end = pts[-1]
    dist_se = p_start.DistanceTo(p_end)

    total_len = 0.0
    for i in range(len(pts) - 1):
        total_len += pts[i].DistanceTo(pts[i+1])

    if total_len < 1e-4:
        return []

    is_closed_loop = (dist_se < max_tol * 3.0) or (dist_se / total_len < 0.15)

    if is_closed_loop and len(pts) >= 6:
        cx = sum(p.X for p in pts) / float(len(pts))
        cy = sum(p.Y for p in pts) / float(len(pts))
        centroid = DB.XYZ(cx, cy, 0.0)

        radii = [p.DistanceTo(centroid) for p in pts]
        mean_r = sum(radii) / float(len(radii))
        variance = sum((r - mean_r) ** 2 for r in radii) / float(len(radii))
        std_r = math.sqrt(variance)

        # Check if true circle
        if mean_r > 1e-3 and (std_r / mean_r) < 0.15:
            try:
                p_right = centroid + DB.XYZ(mean_r, 0.0, 0.0)
                p_left  = centroid + DB.XYZ(-mean_r, 0.0, 0.0)
                p_top   = centroid + DB.XYZ(0.0, mean_r, 0.0)
                p_bot   = centroid + DB.XYZ(0.0, -mean_r, 0.0)

                arc1 = DB.Arc.Create(p_right, p_left, p_top)
                arc2 = DB.Arc.Create(p_left, p_right, p_bot)
                return [arc1, arc2]
            except Exception:
                pass

        # Closed loop but non-circular: split into two open halves
        max_d = -1.0
        furthest_idx = len(pts) // 2
        for i in range(1, len(pts)):
            d = pts[i].DistanceTo(p_start)
            if d > max_d:
                max_d = d
                furthest_idx = i

        half1 = process_open_stroke(pts[:furthest_idx + 1], max_tol)
        half2 = process_open_stroke(pts[furthest_idx:], max_tol)
        return half1 + half2

    return process_open_stroke(pts, max_tol)


def fit_smooth_curves_from_chains(chains, w, h, width_ft, height_ft):
    """Convert raster chains into smooth single DB.Arc and DB.Line curves in model space."""
    all_curves = []
    scale_x = width_ft / float(w)
    scale_y = height_ft / float(h)
    half_w = w / 2.0
    half_h = h / 2.0

    max_tol = max(0.008, width_ft * 0.006)

    for chain in chains:
        pts = []
        for x, y in chain:
            rx = (x - half_w) * scale_x
            ry = (half_h - y) * scale_y
            pts.append(DB.XYZ(rx, ry, 0.0))

        smoothed_pts = smooth_point_chain(pts, iterations=3)

        if len(smoothed_pts) >= 2:
            curves = process_chain_to_curves(smoothed_pts, max_tol)
            for c in curves:
                if c and c.Length > (1.0 / 304.8):
                    all_curves.append(c)
                    if len(all_curves) >= MAX_CURVES:
                        break
        if len(all_curves) >= MAX_CURVES:
            break

    return all_curves


# --------------------------------------------------------------------------
# Family Creation & Project Integration
# --------------------------------------------------------------------------
def find_detail_item_template():
    """Locate a Detail Item.rft template."""
    version = app.VersionNumber
    candidates = [
        r'C:\ProgramData\Autodesk\RVT {0}\Family Templates\English\Detail Item.rft'.format(version),
        r'C:\ProgramData\Autodesk\RVT {0}\Family Templates\English-Imperial\Detail Item.rft'.format(version),
        r'C:\ProgramData\Autodesk\RVT {0}\Family Templates\English_I\Detail Item.rft'.format(version),
        r'C:\ProgramData\Autodesk\RVT {0}\Family Templates\Metric\Detail Item.rft'.format(version),
        r'C:\ProgramData\Autodesk\RVT {0}\Family Templates\English-Metric\Detail Item.rft'.format(version),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path

    base_dir = r'C:\ProgramData\Autodesk'
    if os.path.exists(base_dir):
        for root, dirs, files in os.walk(base_dir):
            if 'Detail Item.rft' in files:
                return os.path.join(root, 'Detail Item.rft')

    return None


def build_detail_item_family(template_path, curves, save_path):
    """Create a new Detail Item family document, add detail lines and arcs, and save to save_path."""
    fam_doc = app.NewFamilyDocument(template_path)

    fam_view = None
    col = DB.FilteredElementCollector(fam_doc).OfClass(DB.View)
    for v in col:
        if not v.IsTemplate and v.ViewType == DB.ViewType.FloorPlan:
            fam_view = v
            break
    if not fam_view:
        for v in col:
            if not v.IsTemplate:
                fam_view = v
                break

    if not fam_view:
        fam_doc.Close(False)
        return False

    created_count = 0
    with DB.Transaction(fam_doc, 'Create Detail Curves') as t:
        t.Start()
        for curve in curves:
            try:
                fam_doc.FamilyCreate.NewDetailCurve(fam_view, curve)
                created_count += 1
            except Exception as ex:
                logger.debug("Failed to create detail curve: {}".format(ex))
        t.Commit()

    if created_count == 0:
        fam_doc.Close(False)
        return False

    save_opts = DB.SaveAsOptions()
    save_opts.OverwriteExistingFile = True
    fam_doc.SaveAs(save_path, save_opts)
    fam_doc.Close(False)

    return True


def load_family_to_doc_by_path(save_path, fam_name):
    """Safely load .rfa file into current document inside a transaction."""
    if not os.path.exists(save_path):
        return None

    load_opt = FamilyLoadOption()

    with DB.Transaction(doc, 'Load Family into Project') as t:
        t.Start()
        try:
            doc.LoadFamily(save_path, load_opt)
        except Exception as ex:
            logger.debug("LoadFamily with load_opt failed: {}".format(ex))
            try:
                doc.LoadFamily(save_path)
            except Exception as ex2:
                logger.debug("LoadFamily simple failed: {}".format(ex2))
        t.Commit()

    target_clean = re.sub(r'[^a-zA-Z0-9]', '', fam_name).lower()
    for fam in DB.FilteredElementCollector(doc).OfClass(DB.Family):
        try:
            fam_clean = re.sub(r'[^a-zA-Z0-9]', '', fam.Name).lower()
            if fam_clean == target_clean:
                return fam
        except Exception:
            pass

    return None


def place_family_instance(family, location_pt, view):
    """Place instance of newly loaded Detail Item family at specified location."""
    if not family:
        return None

    symbol = None
    try:
        symbol_ids = list(family.GetFamilySymbolIds())
        if symbol_ids:
            symbol = doc.GetElement(symbol_ids[0])
    except Exception as ex:
        logger.debug("Failed to get symbol from family: {}".format(ex))

    if not symbol:
        return None

    inst = None
    with DB.Transaction(doc, 'Place Detail Item Instance') as t:
        t.Start()
        if not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()
        try:
            inst = doc.Create.NewFamilyInstance(location_pt, symbol, view)
        except Exception as ex:
            logger.error("NewFamilyInstance failed: {}".format(ex))
        t.Commit()

    return inst


def main():
    view = uidoc.ActiveView
    if not view:
        return

    # 1. Pick the linked or imported image in the view
    image_inst = pick_target_image(view)
    if not image_inst:
        return

    # 2. Extract image file & dimensions
    image_path, type_name = extract_image_file(image_inst)
    if not image_path or not os.path.exists(image_path):
        forms.alert('Could not extract image source file from the selected element.', exitscript=True)
        return

    width_ft, height_ft, loc_pt = get_image_size_and_location(image_inst, view)

    # 3. Locate Detail Item template
    template_path = find_detail_item_template()
    if not template_path:
        template_path = forms.pick_file(file_ext='rft', title='Select Detail Item.rft template')
        if not template_path:
            script.exit()

    # 4. Name the family
    clean_default = re.sub(r'[^a-zA-Z0-9_\- ]', '', type_name).strip()
    if not clean_default:
        clean_default = "Image_DetailItem"
    else:
        clean_default = "DetailItem_" + clean_default

    fam_name = forms.ask_for_string(
        default=clean_default,
        prompt='Enter a name for the new Detail Item family:',
        title='Image to Detail Item'
    )
    if not fam_name:
        script.exit()
    fam_name = fam_name.strip()

    # 5. Select save folder
    save_folder = forms.pick_folder(title='Select folder to save the Detail Item family')
    if not save_folder:
        script.exit()

    save_path = os.path.join(save_folder, "{}.rfa".format(fam_name))

    # 6. Smooth single-line tracing engine
    try:
        gray, w, h = load_grayscale_grid(image_path)
        mask = binarize_smart(gray, w, h)

        # Skeletonize to 1-pixel centerlines
        skeleton = zhang_suen_thinning(mask, w, h)

        # Cluster junctions to prevent line fragmentation
        junc_map, cluster_centers = find_and_cluster_junctions(skeleton, w, h)

        # Extract continuous stroke chains between junction clusters or closed loops
        chains = extract_clean_chains(skeleton, junc_map, cluster_centers, w, h)

        if not chains:
            forms.alert('No traceable single line work was found in the selected image.', exitscript=True)
            return

        curves = fit_smooth_curves_from_chains(chains, w, h, width_ft, height_ft)
        if not curves:
            forms.alert('Tracing produced no usable single line or arc geometry.', exitscript=True)
            return
    except Exception as ex:
        forms.alert('Failed to trace image into smooth single lines and arcs: {}'.format(ex), exitscript=True)
        return

    # 7. Build Detail Item family with smooth single lines & arcs & place instance
    try:
        success = build_detail_item_family(template_path, curves, save_path)
        if not success:
            forms.alert('Failed to build Detail Item family document.', exitscript=True)
            return

        loaded_fam = load_family_to_doc_by_path(save_path, fam_name)
        if not loaded_fam:
            forms.alert('Family file was saved to disk, but loading into Revit project failed.', exitscript=True)
            return

        new_inst = place_family_instance(loaded_fam, loc_pt, view)

        if new_inst:
            try:
                sel = List[DB.ElementId]()
                sel.Add(new_inst.Id)
                uidoc.Selection.SetElementIds(sel)
            except Exception:
                pass
        else:
            forms.alert('Family was saved and loaded successfully, but placing the instance failed.', title='Image to Detail Item')
    except Exception as ex:
        logger.error('Error creating Detail Item family: {}'.format(ex))
        forms.alert('Failed to create Detail Item family:\n\n{}'.format(ex))


if __name__ == '__main__':
    main()