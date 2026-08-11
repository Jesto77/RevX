# -*- coding: utf-8 -*-
"""
Image to Floors & Toposolids  (v14)
- Auto-detects CAD vs rendered
- Auto-excludes background (white/paper) and shadows (dark greys)
- Priority subtraction: Water > Planting > Pavers (zero cross-bucket overlap)
- Intra-bucket separation: same-bucket different-color = separate floors touching
- Equipment/furniture on top -> absorbed into base floor (no holes for them)
- Shape rectification:
    * Detects if a shape is orthogonal (Padel Court) -> snaps to 90 degrees
    * Detects if a shape is curved (Yoga Deck) -> keeps smooth boundary
- 1-px safety erosion so nothing overlaps in Revit
"""

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitServices')
clr.AddReference('System.Drawing')
clr.AddReference('System.Windows.Forms')

import System
import System.IO
import math
from System import Array, Byte
from System.Collections.Generic import List
from System.Drawing import (
    Bitmap, Color, Graphics, Rectangle, Point, Size, Font, FontStyle,
    SolidBrush, Pen, ContentAlignment
)
from System.Drawing.Drawing2D import InterpolationMode, SmoothingMode
from System.Drawing.Imaging import PixelFormat, ImageLockMode
from System.Runtime.InteropServices import Marshal
from System.Windows.Forms import (
    Form, Panel, Button, Label, ComboBox, CheckBox, PictureBox,
    PictureBoxSizeMode, DockStyle, DialogResult, FormStartPosition,
    FormBorderStyle, AnchorStyles, ComboBoxStyle, Cursors, Padding,
    FlatStyle
)

from Autodesk.Revit.DB import (
    Floor, FloorType, Level, CurveLoop, Line, XYZ,
    ImageInstance, ImageType, FilteredElementCollector, Transaction,
    Element, BuiltInParameter
)
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter

try:
    from Autodesk.Revit.DB import Toposolid, ToposolidType
    HAS_TOPO = True
except ImportError:
    HAS_TOPO = False

from pyrevit import forms, script

# ============== TUNABLE CONSTANTS ==============
DOWNSAMPLE_MAX_DIM_CAD   = 400   # higher for CAD precision
DOWNSAMPLE_MAX_DIM_REND  = 220

KMEANS_SAMPLE            = 3500
KMEANS_ITERATIONS        = 10

K_CLUSTERS_CAD           = 12
K_CLUSTERS_REND          = 16

# Small morph radius; enough to swallow small objects but preserve edges
MERGE_RADIUS_CAD         = 2
MERGE_RADIUS_REND        = 4

MIN_BLOB_PIXELS          = 80
SIMPLIFY_TOLERANCE_PX    = 2.0   # stronger simplification for cleaner corners
INWARD_SMOOTH_PASSES     = 1
MIN_EDGE_LENGTH_FT       = 0.05
MIN_HOLE_AREA_FT2        = 8.0
MIN_OTHER_COVERAGE_FRAC  = 0.35

BACKGROUND_BORDER_FRAC   = 0.35
SAFETY_EROSION_PX        = 1

# Shape rectification
ORTHOGONAL_ANGLE_TOL_DEG = 12.0  # segments within this of H/V snap to H/V
ORTHOGONAL_SHAPE_MIN_FRAC = 0.55 # >= this fraction of edge length must already
                                 # be near 0/90 for the shape to be considered
                                 # "orthogonal" (get snapped to true 90 corners)
RECTIFY_SNAP_DIST_PX     = 3.0   # points within this distance snap to shared axis
# =================================================


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView
logger = script.get_logger()


def get_name(elem):
    return Element.Name.__get__(elem)


class ImageOnlyFilter(ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, ImageInstance)
    def AllowReference(self, ref, point):
        return True


# ---------- Color helpers ----------

def rgb_to_hsv(r, g, b):
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(r, g, b), min(r, g, b)
    diff = mx - mn
    if diff == 0:
        h = 0.0
    elif mx == r:
        h = (60 * ((g - b) / diff) + 360) % 360
    elif mx == g:
        h = (60 * ((b - r) / diff) + 120) % 360
    else:
        h = (60 * ((r - g) / diff) + 240) % 360
    s = 0.0 if mx == 0 else diff / mx
    v = mx
    return h, s, v


def classify_bucket(r, g, b):
    h, s, v = rgb_to_hsv(r, g, b)

    # dark = shadow/outline/text/equipment
    if v < 0.30:
        return 'exclude'
    if s < 0.15 and v < 0.45:
        return 'exclude'
    # near-white background
    if s < 0.10 and v > 0.90:
        return 'exclude'

    # water
    if 165 <= h <= 260 and b > r and s >= 0.20:
        return 'water'
    if 150 <= h <= 210 and b > r * 1.15 and v < 0.7:
        return 'water'

    # planting
    if 60 <= h <= 170 and s >= 0.18:
        return 'planting'

    # pavers (warm colors)
    if 0 <= h <= 55 or h >= 330:
        return 'pavers'

    # pavers (light greys / stone / concrete)
    if s < 0.18 and v >= 0.50:
        return 'pavers'

    return 'pavers'


# ---------- Image type auto-detection ----------

def detect_image_type(pixels_flat):
    counts = {}
    for (r, g, b) in pixels_flat:
        key = (r >> 3, g >> 3, b >> 3)
        counts[key] = counts.get(key, 0) + 1
    total = float(len(pixels_flat))
    top_frac = max(counts.values()) / total
    n_unique = len(counts)
    if n_unique < 900 and top_frac > 0.12:
        return 'cad'
    if n_unique > 1500:
        return 'rendered'
    if top_frac > 0.20:
        return 'cad'
    return 'rendered'


# ---------- K-means ----------

def kmeans(pixels, k, iterations):
    import random
    random.seed(0)
    if len(pixels) <= k:
        return [tuple(p) for p in pixels]
    centers = [tuple(c) for c in random.sample(pixels, k)]
    for _ in range(iterations):
        buckets = [[] for _ in range(k)]
        for p in pixels:
            best_i, best_d = 0, None
            for i, c in enumerate(centers):
                d = (p[0]-c[0])**2 + (p[1]-c[1])**2 + (p[2]-c[2])**2
                if best_d is None or d < best_d:
                    best_d, best_i = d, i
            buckets[best_i].append(p)
        new_centers = []
        for i, b in enumerate(buckets):
            if b:
                n = float(len(b))
                new_centers.append((sum(x[0] for x in b)/n,
                                    sum(x[1] for x in b)/n,
                                    sum(x[2] for x in b)/n))
            else:
                new_centers.append(centers[i])
        centers = new_centers
    return centers


def nearest_center(p, centers):
    best_i, best_d = 0, None
    for i, c in enumerate(centers):
        d = (p[0]-c[0])**2 + (p[1]-c[1])**2 + (p[2]-c[2])**2
        if best_d is None or d < best_d:
            best_d, best_i = d, i
    return best_i


# ---------- Morphological operations ----------

def dilate_mask(mask, w, h, radius):
    if radius <= 0:
        return [row[:] for row in mask]
    tmp = [[False]*w for _ in range(h)]
    for y in range(h):
        row_in = mask[y]
        row_out = tmp[y]
        count = 0
        for xi in range(0, min(radius+1, w)):
            if row_in[xi]:
                count += 1
        row_out[0] = count > 0
        for x in range(1, w):
            x_add = x + radius
            x_rem = x - radius - 1
            if x_add < w and row_in[x_add]:
                count += 1
            if x_rem >= 0 and row_in[x_rem]:
                count -= 1
            row_out[x] = count > 0

    out = [[False]*w for _ in range(h)]
    for x in range(w):
        count = 0
        for yi in range(0, min(radius+1, h)):
            if tmp[yi][x]:
                count += 1
        out[0][x] = count > 0
        for y in range(1, h):
            y_add = y + radius
            y_rem = y - radius - 1
            if y_add < h and tmp[y_add][x]:
                count += 1
            if y_rem >= 0 and tmp[y_rem][x]:
                count -= 1
            out[y][x] = count > 0
    return out


def erode_mask(mask, w, h, radius):
    if radius <= 0:
        return [row[:] for row in mask]
    inv = [[not mask[y][x] for x in range(w)] for y in range(h)]
    inv_dil = dilate_mask(inv, w, h, radius)
    return [[not inv_dil[y][x] for x in range(w)] for y in range(h)]


def close_mask(mask, w, h, radius):
    if radius <= 0:
        return [row[:] for row in mask]
    d = dilate_mask(mask, w, h, radius)
    e = erode_mask(d, w, h, radius)
    return e


def open_mask(mask, w, h, radius):
    if radius <= 0:
        return [row[:] for row in mask]
    e = erode_mask(mask, w, h, radius)
    d = dilate_mask(e, w, h, radius)
    return d


# ---------- Blob operations ----------

def find_blobs_from_mask(mask, w, h, min_pixels):
    visited = [[False]*w for _ in range(h)]
    blobs = []
    for y0 in range(h):
        row_v = visited[y0]
        row_m = mask[y0]
        for x0 in range(w):
            if row_v[x0] or not row_m[x0]:
                continue
            stack = [(x0, y0)]
            row_v[x0] = True
            pixels = []
            minx = maxx = x0
            miny = maxy = y0
            while stack:
                cx, cy = stack.pop()
                pixels.append((cx, cy))
                if cx < minx: minx = cx
                if cx > maxx: maxx = cx
                if cy < miny: miny = cy
                if cy > maxy: maxy = cy
                if cx+1 < w and not visited[cy][cx+1] and mask[cy][cx+1]:
                    visited[cy][cx+1] = True; stack.append((cx+1, cy))
                if cx-1 >= 0 and not visited[cy][cx-1] and mask[cy][cx-1]:
                    visited[cy][cx-1] = True; stack.append((cx-1, cy))
                if cy+1 < h and not visited[cy+1][cx] and mask[cy+1][cx]:
                    visited[cy+1][cx] = True; stack.append((cx, cy+1))
                if cy-1 >= 0 and not visited[cy-1][cx] and mask[cy-1][cx]:
                    visited[cy-1][cx] = True; stack.append((cx, cy-1))
            if len(pixels) >= min_pixels:
                blobs.append({'pixels': pixels,
                              'minx': minx, 'maxx': maxx,
                              'miny': miny, 'maxy': maxy})
    return blobs


def find_holes_in_blob(blob, w, h):
    pixset = set(blob['pixels'])
    minx, maxx = blob['minx'], blob['maxx']
    miny, maxy = blob['miny'], blob['maxy']
    pminx = max(minx - 1, 0); pmaxx = min(maxx + 1, w - 1)
    pminy = max(miny - 1, 0); pmaxy = min(maxy + 1, h - 1)

    exterior = set()
    stack = []
    for x in range(pminx, pmaxx + 1):
        if (x, pminy) not in pixset: stack.append((x, pminy))
        if (x, pmaxy) not in pixset: stack.append((x, pmaxy))
    for y in range(pminy, pmaxy + 1):
        if (pminx, y) not in pixset: stack.append((pminx, y))
        if (pmaxx, y) not in pixset: stack.append((pmaxx, y))

    while stack:
        cx, cy = stack.pop()
        if (cx, cy) in exterior: continue
        if (cx, cy) in pixset: continue
        if cx < pminx or cx > pmaxx or cy < pminy or cy > pmaxy: continue
        exterior.add((cx, cy))
        stack.append((cx+1, cy)); stack.append((cx-1, cy))
        stack.append((cx, cy+1)); stack.append((cx, cy-1))

    holes = []
    hole_visited = set()
    for y in range(pminy, pmaxy + 1):
        for x in range(pminx, pmaxx + 1):
            if (x, y) in pixset: continue
            if (x, y) in exterior: continue
            if (x, y) in hole_visited: continue
            hpixels = []
            hstack = [(x, y)]
            while hstack:
                cx, cy = hstack.pop()
                if (cx, cy) in hole_visited: continue
                if (cx, cy) in pixset: continue
                if (cx, cy) in exterior: continue
                if cx < pminx or cx > pmaxx or cy < pminy or cy > pmaxy: continue
                hole_visited.add((cx, cy))
                hpixels.append((cx, cy))
                hstack.append((cx+1, cy)); hstack.append((cx-1, cy))
                hstack.append((cx, cy+1)); hstack.append((cx, cy-1))
            if hpixels:
                hxs = [p[0] for p in hpixels]; hys = [p[1] for p in hpixels]
                holes.append({
                    'pixels': hpixels,
                    'minx': min(hxs), 'maxx': max(hxs),
                    'miny': min(hys), 'maxy': max(hys),
                })
    return holes


# ---------- Boundary tracing ----------

def blob_boundary_edges(pixset):
    edges = []
    for (x, y) in pixset:
        if (x, y-1) not in pixset:
            edges.append(((x,   y),   (x+1, y)))
        if (x+1, y) not in pixset:
            edges.append(((x+1, y),   (x+1, y+1)))
        if (x, y+1) not in pixset:
            edges.append(((x+1, y+1), (x,   y+1)))
        if (x-1, y) not in pixset:
            edges.append(((x,   y+1), (x,   y)))
    return edges


def chain_edges_to_loops(edges):
    out = {}
    for a, b in edges:
        out.setdefault(a, []).append(b)
    loops = []
    while out:
        start = None
        for k in out:
            start = k
            break
        if start is None:
            break
        loop = [start]
        cur = start
        prev = None
        safety = 0
        while True:
            safety += 1
            if safety > 500000:
                break
            nexts = out.get(cur)
            if not nexts:
                break
            if len(nexts) == 1:
                nxt = nexts.pop()
            else:
                if prev is None:
                    nxt = nexts.pop()
                else:
                    dx0 = cur[0]-prev[0]; dy0 = cur[1]-prev[1]
                    best_j, best_score = 0, -1e9
                    for j, cand in enumerate(nexts):
                        dx1 = cand[0]-cur[0]; dy1 = cand[1]-cur[1]
                        cross = dx0*dy1 - dy0*dx1
                        dot   = dx0*dx1 + dy0*dy1
                        score = cross*10 - dot
                        if score > best_score:
                            best_score = score; best_j = j
                    nxt = nexts.pop(best_j)
            if not nexts:
                del out[cur]
            if nxt == start:
                break
            loop.append(nxt)
            prev = cur
            cur = nxt
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def signed_area(pts):
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i+1) % n]
        s += x1*y2 - x2*y1
    return s / 2.0


def simplify_collinear(points):
    n = len(points)
    if n < 3:
        return points
    result = []
    for i in range(n):
        px, py = points[i-1]
        cx, cy = points[i]
        nx, ny = points[(i+1) % n]
        if (cx-px)*(ny-cy) - (cy-py)*(nx-cx) != 0:
            result.append((cx, cy))
    return result if len(result) >= 3 else points


def simplify_visvalingam(points, tolerance):
    if len(points) < 4:
        return points
    tol_area = tolerance * tolerance
    pts = list(points)
    changed = True
    while changed and len(pts) > 3:
        changed = False
        min_area = None
        min_idx = -1
        n = len(pts)
        for i in range(n):
            a = pts[(i-1) % n]
            b = pts[i]
            c = pts[(i+1) % n]
            area = abs((b[0]-a[0])*(c[1]-a[1]) - (c[0]-a[0])*(b[1]-a[1])) * 0.5
            if min_area is None or area < min_area:
                min_area = area
                min_idx = i
        if min_area is not None and min_area < tol_area:
            del pts[min_idx]
            changed = True
    return pts


def polygon_self_intersects(pts):
    n = len(pts)
    if n < 4:
        return False
    def ccw(A, B, C):
        return (C[1]-A[1])*(B[0]-A[0]) > (B[1]-A[1])*(C[0]-A[0])
    def seg_intersect(A, B, C, D):
        return ccw(A,C,D) != ccw(B,C,D) and ccw(A,B,C) != ccw(A,B,D)
    for i in range(n):
        a1 = pts[i]; a2 = pts[(i+1) % n]
        for j in range(i+2, n):
            if i == 0 and j == n-1:
                continue
            b1 = pts[j]; b2 = pts[(j+1) % n]
            if seg_intersect(a1, a2, b1, b2):
                return True
    return False


def inward_smooth(points, pixset, passes):
    if passes <= 0 or len(points) < 3:
        return points

    def in_mask(x, y):
        ix = int(x); iy = int(y)
        for dx in (-1, 0):
            for dy in (-1, 0):
                if (ix + dx, iy + dy) in pixset:
                    return True
        return False

    pts = list(points)
    for _ in range(passes):
        new_pts = []
        n = len(pts)
        for i in range(n):
            p0 = pts[i]
            p1 = pts[(i + 1) % n]
            q = (0.75 * p0[0] + 0.25 * p1[0], 0.75 * p0[1] + 0.25 * p1[1])
            r = (0.25 * p0[0] + 0.75 * p1[0], 0.25 * p0[1] + 0.75 * p1[1])
            if in_mask(q[0], q[1]):
                new_pts.append(q)
            else:
                new_pts.append(p0)
            if in_mask(r[0], r[1]):
                new_pts.append(r)
            else:
                new_pts.append(p1)
        pts = new_pts
    return pts


# ---------- Shape rectification ----------

def _seg_angle_deg(p1, p2):
    """Angle of segment in [0, 180)."""
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    if dx == 0 and dy == 0:
        return 0.0
    a = math.degrees(math.atan2(dy, dx))
    if a < 0:
        a += 180.0
    if a >= 180.0:
        a -= 180.0
    return a


def _seg_len(p1, p2):
    dx = p2[0] - p1[0]; dy = p2[1] - p1[1]
    return math.sqrt(dx*dx + dy*dy)


def is_shape_orthogonal(points):
    """Returns True if a large fraction of the loop's edge length is
    already near horizontal or vertical."""
    n = len(points)
    if n < 4:
        return False
    total_len = 0.0
    ortho_len = 0.0
    for i in range(n):
        p1 = points[i]; p2 = points[(i+1) % n]
        L = _seg_len(p1, p2)
        if L < 1e-6:
            continue
        total_len += L
        a = _seg_angle_deg(p1, p2)
        # distance to nearest 0 or 90
        d = min(a, abs(a - 90.0), abs(a - 180.0))
        if d <= ORTHOGONAL_ANGLE_TOL_DEG:
            ortho_len += L
    if total_len < 1e-6:
        return False
    return (ortho_len / total_len) >= ORTHOGONAL_SHAPE_MIN_FRAC


def rectify_orthogonal(points):
    """Snap each segment to horizontal or vertical based on its dominant
    direction, then re-connect by moving vertices to shared axes."""
    n = len(points)
    if n < 4:
        return points

    # 1) Classify each segment as H or V based on absolute dx/dy
    seg_types = []  # 'H' or 'V'
    for i in range(n):
        p1 = points[i]; p2 = points[(i+1) % n]
        dx = abs(p2[0] - p1[0])
        dy = abs(p2[1] - p1[1])
        # short segments follow the type of the longer neighbor later, but
        # for now use dominant axis
        seg_types.append('H' if dx >= dy else 'V')

    # 2) Force each segment to be exactly H or V by moving one endpoint
    # We do this by defining, for each vertex, whether its X or Y is fixed
    # by the incoming/outgoing segment types.
    # Approach: iterate to find corner coordinates that satisfy all
    # segment constraints. Simple algorithm: for each vertex, average the
    # perpendicular coordinate of its two neighbors.
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    for _pass in range(3):
        new_xs = xs[:]
        new_ys = ys[:]
        for i in range(n):
            prev_i = (i - 1) % n
            t_in = seg_types[prev_i]   # segment prev -> i
            t_out = seg_types[i]       # segment i -> next
            # If incoming is H, then y[i] must equal y[prev]
            # If incoming is V, then x[i] must equal x[prev]
            # If outgoing is H, then y[i] must equal y[next]
            # If outgoing is V, then x[i] must equal x[next]
            targ_x = None
            targ_y = None
            if t_in == 'V':
                targ_x = xs[prev_i]
            if t_out == 'V':
                targ_x = xs[(i + 1) % n] if targ_x is None else (targ_x + xs[(i+1)%n]) * 0.5
            if t_in == 'H':
                targ_y = ys[prev_i]
            if t_out == 'H':
                targ_y = ys[(i + 1) % n] if targ_y is None else (targ_y + ys[(i+1)%n]) * 0.5
            if targ_x is not None:
                new_xs[i] = targ_x
            if targ_y is not None:
                new_ys[i] = targ_y
        xs = new_xs
        ys = new_ys

    # 3) Snap near-equal coordinates to shared axes (removes tiny jogs)
    def snap_axis(vals):
        n = len(vals)
        for i in range(n):
            for j in range(i+1, n):
                if abs(vals[i] - vals[j]) <= RECTIFY_SNAP_DIST_PX:
                    m = (vals[i] + vals[j]) * 0.5
                    vals[i] = m
                    vals[j] = m
        return vals

    xs = snap_axis(xs)
    ys = snap_axis(ys)

    # 4) Build final loop and drop duplicate consecutive points
    result = []
    for i in range(n):
        p = (xs[i], ys[i])
        if result and abs(p[0] - result[-1][0]) < 1e-6 and abs(p[1] - result[-1][1]) < 1e-6:
            continue
        result.append(p)
    # remove closing dup
    while len(result) >= 2 and abs(result[0][0] - result[-1][0]) < 1e-6 and abs(result[0][1] - result[-1][1]) < 1e-6:
        result.pop()

    # 5) Remove collinear vertices (all segments now H or V, so easy)
    if len(result) < 3:
        return points
    cleaned = []
    m = len(result)
    for i in range(m):
        a = result[(i - 1) % m]
        b = result[i]
        c = result[(i + 1) % m]
        # skip b if a-b-c are collinear (both same H or both same V)
        if abs(a[0] - b[0]) < 1e-6 and abs(b[0] - c[0]) < 1e-6:
            continue  # all on same vertical line
        if abs(a[1] - b[1]) < 1e-6 and abs(b[1] - c[1]) < 1e-6:
            continue  # all on same horizontal line
        cleaned.append(b)
    if len(cleaned) >= 3:
        return cleaned
    return result


# ================= UI THEME =================

CLR_APP_BG        = Color.FromArgb(245, 247, 250)
CLR_CARD          = Color.White
CLR_ROW           = Color.White
CLR_ROW_ALT       = Color.FromArgb(248, 250, 253)
CLR_BORDER        = Color.FromArgb(220, 225, 232)
CLR_TEXT          = Color.FromArgb(30, 35, 45)
CLR_TEXT_SOFT     = Color.FromArgb(90, 95, 105)
CLR_MUTED         = Color.FromArgb(140, 148, 158)
CLR_TITLE_BAR     = Color.White
CLR_FOOTER        = Color.FromArgb(240, 243, 247)

CLR_HDR_PAVERS    = Color.FromArgb(232, 145, 78)
CLR_HDR_PLANTING  = Color.FromArgb(96, 175, 115)
CLR_HDR_WATER     = Color.FromArgb(88, 155, 210)

CLR_APPLY         = Color.FromArgb(37, 145, 65)
CLR_APPLY_HOVER   = Color.FromArgb(48, 168, 78)
CLR_CANCEL        = Color.FromArgb(230, 232, 236)
CLR_CANCEL_HOVER  = Color.FromArgb(215, 218, 224)
CLR_CANCEL_TEXT   = Color.FromArgb(60, 65, 75)


def make_swatch(rgb, size=32):
    bmp = Bitmap(size, size)
    g = Graphics.FromImage(bmp)
    g.SmoothingMode = SmoothingMode.AntiAlias
    br = SolidBrush(Color.FromArgb(int(rgb[0]), int(rgb[1]), int(rgb[2])))
    g.FillRectangle(br, 0, 0, size, size)
    pen = Pen(Color.FromArgb(90, 0, 0, 0), 1)
    g.DrawRectangle(pen, 0, 0, size-1, size-1)
    br.Dispose(); pen.Dispose(); g.Dispose()
    return bmp


def make_flat_button(text, bg_color, hover_color, fg_color, width=110, height=44):
    b = Button()
    b.Text = text
    b.Width = width
    b.Height = height
    b.FlatStyle = FlatStyle.Flat
    b.FlatAppearance.BorderSize = 0
    b.BackColor = bg_color
    b.FlatAppearance.MouseOverBackColor = hover_color
    b.ForeColor = fg_color
    b.Font = Font("Segoe UI Semibold", 11, FontStyle.Bold)
    b.Cursor = Cursors.Hand
    b.TextAlign = ContentAlignment.MiddleCenter
    return b


def make_light_combo(items, width=240):
    cb = ComboBox()
    cb.Width = width
    cb.DropDownStyle = ComboBoxStyle.DropDownList
    cb.Font = Font("Segoe UI", 9.5)
    cb.BackColor = Color.White
    cb.ForeColor = CLR_TEXT
    cb.FlatStyle = FlatStyle.Flat
    for it in items:
        cb.Items.Add(it)
    if items:
        cb.SelectedIndex = 0
    return cb


class UnifiedMappingDialog(Form):
    def __init__(self, sections, level_names, default_level_name, detected_type_label):
        self.Text = "Image to Revit  -  Assign Types"
        self.ClientSize = Size(1000, 820)
        self.MinimumSize = Size(900, 700)
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.Sizable
        self.MaximizeBox = True
        self.MinimizeBox = False
        self.BackColor = CLR_APP_BG
        self.ForeColor = CLR_TEXT
        self.Font = Font("Segoe UI", 9.5)

        self._rows_by_key = {}
        self._level_combo = None
        self.result_maps = None
        self.result_level = None

        footer = Panel()
        footer.Dock = DockStyle.Bottom
        footer.Height = 130
        footer.BackColor = CLR_FOOTER

        border_top = Panel()
        border_top.Dock = DockStyle.Top
        border_top.Height = 1
        border_top.BackColor = CLR_BORDER
        footer.Controls.Add(border_top)

        button_row = Panel()
        button_row.Dock = DockStyle.Bottom
        button_row.Height = 70
        button_row.BackColor = CLR_FOOTER

        right_btn_panel = Panel()
        right_btn_panel.Width = 350
        right_btn_panel.Height = 60
        right_btn_panel.Dock = DockStyle.Right
        right_btn_panel.BackColor = CLR_FOOTER

        cancel_btn = make_flat_button(
            "Cancel", CLR_CANCEL, CLR_CANCEL_HOVER, CLR_CANCEL_TEXT, 130, 46)
        cancel_btn.Location = Point(10, 8)
        cancel_btn.DialogResult = DialogResult.Cancel

        apply_btn = make_flat_button(
            "APPLY", CLR_APPLY, CLR_APPLY_HOVER, Color.White, 190, 46)
        apply_btn.Location = Point(150, 8)
        apply_btn.DialogResult = DialogResult.OK
        apply_btn.Click += self._on_apply

        right_btn_panel.Controls.Add(cancel_btn)
        right_btn_panel.Controls.Add(apply_btn)

        left_btn_panel = Panel()
        left_btn_panel.Width = 260
        left_btn_panel.Height = 60
        left_btn_panel.Dock = DockStyle.Left
        left_btn_panel.BackColor = CLR_FOOTER

        select_all_btn = make_flat_button(
            "Select All", CLR_CANCEL, CLR_CANCEL_HOVER, CLR_CANCEL_TEXT, 115, 46)
        select_all_btn.Location = Point(20, 8)
        select_all_btn.Click += self._select_all

        clear_btn = make_flat_button(
            "Clear All", CLR_CANCEL, CLR_CANCEL_HOVER, CLR_CANCEL_TEXT, 105, 46)
        clear_btn.Location = Point(140, 8)
        clear_btn.Click += self._select_none

        left_btn_panel.Controls.Add(select_all_btn)
        left_btn_panel.Controls.Add(clear_btn)

        button_row.Controls.Add(right_btn_panel)
        button_row.Controls.Add(left_btn_panel)
        footer.Controls.Add(button_row)

        settings_row = Panel()
        settings_row.Dock = DockStyle.Fill
        settings_row.BackColor = CLR_FOOTER

        lvl_lbl = Label()
        lvl_lbl.Text = "Target Level:"
        lvl_lbl.Font = Font("Segoe UI Semibold", 10, FontStyle.Bold)
        lvl_lbl.ForeColor = CLR_TEXT
        lvl_lbl.Location = Point(24, 22)
        lvl_lbl.AutoSize = True
        settings_row.Controls.Add(lvl_lbl)

        self._level_combo = make_light_combo(level_names, width=260)
        if default_level_name and default_level_name in level_names:
            self._level_combo.SelectedItem = default_level_name
        self._level_combo.Location = Point(130, 19)
        settings_row.Controls.Add(self._level_combo)

        detected_lbl = Label()
        detected_lbl.Text = "Auto-detected image type:  {}".format(detected_type_label)
        detected_lbl.Font = Font("Segoe UI", 9, FontStyle.Italic)
        detected_lbl.ForeColor = CLR_MUTED
        detected_lbl.Location = Point(410, 15)
        detected_lbl.AutoSize = True
        settings_row.Controls.Add(detected_lbl)

        info_lbl = Label()
        info_lbl.Text = ("Rectangular shapes auto-snap to 90 deg; curved shapes preserved. "
                         "Zero overlaps guaranteed.")
        info_lbl.Font = Font("Segoe UI", 9, FontStyle.Italic)
        info_lbl.ForeColor = CLR_MUTED
        info_lbl.Location = Point(410, 32)
        info_lbl.AutoSize = True
        settings_row.Controls.Add(info_lbl)

        footer.Controls.Add(settings_row)

        self.Controls.Add(footer)
        self.AcceptButton = apply_btn
        self.CancelButton = cancel_btn

        header = Panel()
        header.Dock = DockStyle.Top
        header.Height = 84
        header.BackColor = CLR_TITLE_BAR
        header.Padding = Padding(24, 14, 24, 14)

        header_border = Panel()
        header_border.Dock = DockStyle.Bottom
        header_border.Height = 1
        header_border.BackColor = CLR_BORDER
        header.Controls.Add(header_border)

        t = Label()
        t.Text = "Image to Revit Elements"
        t.Font = Font("Segoe UI Semibold", 18, FontStyle.Bold)
        t.ForeColor = CLR_TEXT
        t.Location = Point(24, 14)
        t.AutoSize = True
        header.Controls.Add(t)

        s = Label()
        s.Text = "Review detected colors, assign a Revit type to each row, then click APPLY."
        s.Font = Font("Segoe UI", 10)
        s.ForeColor = CLR_TEXT_SOFT
        s.Location = Point(26, 48)
        s.AutoSize = True
        header.Controls.Add(s)

        self.Controls.Add(header)

        content = Panel()
        content.Dock = DockStyle.Fill
        content.AutoScroll = True
        content.BackColor = CLR_APP_BG
        content.Padding = Padding(20, 16, 20, 16)
        self.Controls.Add(content)

        footer.BringToFront()

        y = 4
        card_width = 930

        for sec in sections:
            key = sec['key']
            self._rows_by_key[key] = []
            accent = sec['accent']

            hdr = Panel()
            hdr.Width = card_width
            hdr.Height = 54
            hdr.Location = Point(4, y)
            hdr.BackColor = accent

            ht = Label()
            ht.Text = "  " + sec['title']
            ht.Font = Font("Segoe UI Semibold", 12, FontStyle.Bold)
            ht.ForeColor = Color.White
            ht.Location = Point(18, 8)
            ht.AutoSize = True
            hdr.Controls.Add(ht)

            hs = Label()
            hs.Text = "  " + sec['subtitle']
            hs.Font = Font("Segoe UI", 9)
            hs.ForeColor = Color.FromArgb(245, 255, 255, 255)
            hs.Location = Point(20, 30)
            hs.AutoSize = True
            hdr.Controls.Add(hs)

            content.Controls.Add(hdr)
            y += 54

            colhdr = Panel()
            colhdr.Width = card_width
            colhdr.Height = 32
            colhdr.Location = Point(4, y)
            colhdr.BackColor = Color.FromArgb(235, 238, 243)

            def hdr_label(text, x, w):
                l = Label()
                l.Text = text.upper()
                l.Font = Font("Segoe UI Semibold", 8.5, FontStyle.Bold)
                l.ForeColor = CLR_TEXT_SOFT
                l.Location = Point(x, 10)
                l.Width = w
                return l

            colhdr.Controls.Add(hdr_label("Use",              18, 40))
            colhdr.Controls.Add(hdr_label("Color",            66, 60))
            colhdr.Controls.Add(hdr_label("Detected Item",   140, 200))
            colhdr.Controls.Add(hdr_label("Area",            360, 100))
            colhdr.Controls.Add(hdr_label("Regions",         470, 70))
            colhdr.Controls.Add(hdr_label("Assign Revit Type", 560, 340))
            content.Controls.Add(colhdr)
            y += 32

            groups = sec['groups']
            type_names = sec['types']

            if not groups:
                empty = Panel()
                empty.Width = card_width
                empty.Height = 48
                empty.Location = Point(4, y)
                empty.BackColor = CLR_CARD
                el = Label()
                el.Text = "   No colors of this type detected in the image."
                el.Font = Font("Segoe UI", 9.5, FontStyle.Italic)
                el.ForeColor = CLR_MUTED
                el.Location = Point(14, 15)
                el.AutoSize = True
                empty.Controls.Add(el)
                content.Controls.Add(empty)
                y += 54
                continue

            if not type_names:
                empty = Panel()
                empty.Width = card_width
                empty.Height = 48
                empty.Location = Point(4, y)
                empty.BackColor = CLR_CARD
                el = Label()
                el.Text = "   No Revit types available for this category."
                el.Font = Font("Segoe UI", 9.5, FontStyle.Italic)
                el.ForeColor = CLR_MUTED
                el.Location = Point(14, 15)
                el.AutoSize = True
                empty.Controls.Add(el)
                content.Controls.Add(empty)
                y += 54
                continue

            for idx, g in enumerate(groups):
                row = Panel()
                row.Width = card_width
                row.Height = 56
                row.Location = Point(4, y)
                row.BackColor = CLR_ROW if idx % 2 == 0 else CLR_ROW_ALT

                sep = Panel()
                sep.Dock = DockStyle.Bottom
                sep.Height = 1
                sep.BackColor = CLR_BORDER
                row.Controls.Add(sep)

                chk = CheckBox()
                chk.Checked = not g.get('default_unchecked', False)
                chk.Location = Point(22, 20)
                chk.Width = 22
                row.Controls.Add(chk)

                pb = PictureBox()
                pb.Image = make_swatch(g['color'], 32)
                pb.SizeMode = PictureBoxSizeMode.AutoSize
                pb.Location = Point(66, 12)
                row.Controls.Add(pb)

                det = Label()
                det.Text = g.get('display_name', '')
                det.Font = Font("Segoe UI Semibold", 10, FontStyle.Bold)
                det.ForeColor = CLR_TEXT
                det.Location = Point(140, 10)
                det.Width = 220
                row.Controls.Add(det)

                hex_lbl = Label()
                hex_lbl.Text = "#{:02X}{:02X}{:02X}".format(
                    int(g['color'][0]), int(g['color'][1]), int(g['color'][2]))
                hex_lbl.Font = Font("Segoe UI", 8)
                hex_lbl.ForeColor = CLR_MUTED
                hex_lbl.Location = Point(140, 30)
                hex_lbl.Width = 220
                row.Controls.Add(hex_lbl)

                area_lbl = Label()
                area_lbl.Text = "{:,.1f} sq ft".format(g['area_ft2'])
                area_lbl.Font = Font("Segoe UI", 10)
                area_lbl.ForeColor = CLR_TEXT
                area_lbl.Location = Point(360, 19)
                area_lbl.Width = 110
                row.Controls.Add(area_lbl)

                reg_lbl = Label()
                reg_lbl.Text = "-"
                reg_lbl.Font = Font("Segoe UI", 10)
                reg_lbl.ForeColor = CLR_TEXT
                reg_lbl.Location = Point(480, 19)
                reg_lbl.Width = 70
                row.Controls.Add(reg_lbl)

                cb = make_light_combo(type_names, width=340)
                cb.Location = Point(560, 16)
                row.Controls.Add(cb)

                content.Controls.Add(row)
                self._rows_by_key[key].append((g['cluster_id'], chk, cb))
                y += 56

            y += 18

    def _select_all(self, s, e):
        for _, rows in self._rows_by_key.items():
            for _, chk, _ in rows:
                chk.Checked = True

    def _select_none(self, s, e):
        for _, rows in self._rows_by_key.items():
            for _, chk, _ in rows:
                chk.Checked = False

    def _on_apply(self, s, e):
        maps = {}
        for key, rows in self._rows_by_key.items():
            m = {}
            for cid, chk, cb in rows:
                if chk.Checked and cb.SelectedItem is not None:
                    m[cid] = cb.SelectedItem
            maps[key] = m
        self.result_maps = maps
        self.result_level = self._level_combo.SelectedItem


# ================= MAIN =================

try:
    pick = uidoc.Selection.PickObject(
        ObjectType.Element, ImageOnlyFilter(),
        "Select the inserted image in this view")
except Exception:
    script.exit()

image = doc.GetElement(pick.ElementId)
image_type = doc.GetElement(image.GetTypeId())

img_path = None
try: img_path = image_type.Path
except: pass
if not img_path:
    try: img_path = image_type.GetPath()
    except: pass

bitmap = None
if img_path and System.IO.File.Exists(img_path):
    try: bitmap = Bitmap(img_path)
    except: bitmap = None
if bitmap is None:
    bitmap = image_type.GetImage()
if bitmap is None:
    forms.alert('Could not read image pixels.', exitscript=True)

real_w_ft = None; real_h_ft = None
try:
    p_w = image_type.get_Parameter(BuiltInParameter.RASTER_SHEETWIDTH)
    p_h = image_type.get_Parameter(BuiltInParameter.RASTER_SHEETHEIGHT)
    if p_w and p_h:
        real_w_ft = p_w.AsDouble()
        real_h_ft = p_h.AsDouble()
except: pass

bbox = image.get_BoundingBox(view)
if bbox is not None:
    minX, maxX = bbox.Min.X, bbox.Max.X
    minY, maxY = bbox.Min.Y, bbox.Max.Y
else:
    if not real_w_ft or not real_h_ft:
        forms.alert('Could not determine image size in view.', exitscript=True)
    loc = image.GetLocation() if hasattr(image, 'GetLocation') else XYZ(0,0,0)
    minX = loc.X - real_w_ft/2.0; maxX = loc.X + real_w_ft/2.0
    minY = loc.Y - real_h_ft/2.0; maxY = loc.Y + real_h_ft/2.0

# Auto-detect image type
src_w, src_h = bitmap.Width, bitmap.Height
test_max = 160
test_scale = float(test_max) / max(src_w, src_h)
test_w = max(1, int(src_w * test_scale))
test_h = max(1, int(src_h * test_scale))
test_bmp = Bitmap(test_w, test_h, PixelFormat.Format24bppRgb)
g_ = Graphics.FromImage(test_bmp)
g_.InterpolationMode = InterpolationMode.HighQualityBilinear
g_.DrawImage(bitmap, 0, 0, test_w, test_h)
g_.Dispose()

test_rect = Rectangle(0, 0, test_w, test_h)
test_data = test_bmp.LockBits(test_rect, ImageLockMode.ReadOnly, PixelFormat.Format24bppRgb)
test_stride = test_data.Stride
test_buf = Array.CreateInstance(Byte, test_stride * test_h)
Marshal.Copy(test_data.Scan0, test_buf, 0, test_stride * test_h)
test_bmp.UnlockBits(test_data)

test_pixels = []
for y in range(test_h):
    base = y * test_stride
    for x in range(test_w):
        i = base + x*3
        test_pixels.append((test_buf[i+2] & 0xFF, test_buf[i+1] & 0xFF, test_buf[i] & 0xFF))

img_type = detect_image_type(test_pixels)
print("=" * 60)
print("AUTO-DETECTED IMAGE TYPE: {}".format(img_type.upper()))
print("=" * 60)

if img_type == 'cad':
    DOWNSAMPLE_MAX_DIM = DOWNSAMPLE_MAX_DIM_CAD
    K_CLUSTERS = K_CLUSTERS_CAD
    MERGE_RADIUS = MERGE_RADIUS_CAD
    detected_label = "CAD-style plan (crisp edges, orthogonal snap enabled)"
else:
    DOWNSAMPLE_MAX_DIM = DOWNSAMPLE_MAX_DIM_REND
    K_CLUSTERS = K_CLUSTERS_REND
    MERGE_RADIUS = MERGE_RADIUS_REND
    detected_label = "Rendered / stylized (soft edges)"

print("Using: K={}, MergeRadius={}, DownsampleMax={}".format(
    K_CLUSTERS, MERGE_RADIUS, DOWNSAMPLE_MAX_DIM))

# Full downsample
scale_factor = float(DOWNSAMPLE_MAX_DIM) / max(src_w, src_h)
proc_w = max(1, int(src_w * scale_factor))
proc_h = max(1, int(src_h * scale_factor))

small = Bitmap(proc_w, proc_h, PixelFormat.Format24bppRgb)
g_ = Graphics.FromImage(small)
g_.InterpolationMode = InterpolationMode.HighQualityBilinear
g_.DrawImage(bitmap, 0, 0, proc_w, proc_h)
g_.Dispose()

scale_x = (maxX - minX) / float(proc_w)
scale_y = (maxY - minY) / float(proc_h)

rect = Rectangle(0, 0, proc_w, proc_h)
data = small.LockBits(rect, ImageLockMode.ReadOnly, PixelFormat.Format24bppRgb)
stride = data.Stride
buf = Array.CreateInstance(Byte, stride * proc_h)
Marshal.Copy(data.Scan0, buf, 0, stride * proc_h)
small.UnlockBits(data)

pixel_grid = [[None]*proc_w for _ in range(proc_h)]
for y in range(proc_h):
    base = y * stride
    row = pixel_grid[y]
    for x in range(proc_w):
        i = base + x*3
        row[x] = (buf[i+2] & 0xFF, buf[i+1] & 0xFF, buf[i] & 0xFF)

import random
random.seed(0)
flat = [pixel_grid[y][x] for y in range(proc_h) for x in range(proc_w)]
sample = random.sample(flat, min(KMEANS_SAMPLE, len(flat)))
centers = kmeans(sample, K_CLUSTERS, KMEANS_ITERATIONS)
center_bucket = [classify_bucket(c[0], c[1], c[2]) for c in centers]

label_grid = [[0]*proc_w for _ in range(proc_h)]
cache = {}
for y in range(proc_h):
    row_p = pixel_grid[y]; row_l = label_grid[y]
    for x in range(proc_w):
        p = row_p[x]
        lbl = cache.get(p)
        if lbl is None:
            lbl = nearest_center(p, centers)
            cache[p] = lbl
        row_l[x] = lbl

# BACKGROUND AUTO-DETECT
border_pixel_count = 2 * proc_w + 2 * (proc_h - 2)
border_cluster_counts = [0] * len(centers)
for x in range(proc_w):
    border_cluster_counts[label_grid[0][x]] += 1
    border_cluster_counts[label_grid[proc_h - 1][x]] += 1
for y in range(1, proc_h - 1):
    border_cluster_counts[label_grid[y][0]] += 1
    border_cluster_counts[label_grid[y][proc_w - 1]] += 1

background_clusters = set()
for cid in range(len(centers)):
    frac = border_cluster_counts[cid] / float(border_pixel_count)
    if frac >= BACKGROUND_BORDER_FRAC:
        background_clusters.add(cid)
        center_bucket[cid] = 'exclude'

print("=" * 60)
print("DETECTED CLUSTER COLORS AND CLASSIFICATIONS:")
print("=" * 60)
for i, c in enumerate(centers):
    r, g, b = int(c[0]), int(c[1]), int(c[2])
    h, s, v = rgb_to_hsv(r, g, b)
    tag = center_bucket[i]
    extra = ""
    if i in background_clusters:
        extra = "  [BACKGROUND]"
    print("  #{:02d}  RGB({:3d},{:3d},{:3d})  #{:02X}{:02X}{:02X}  "
          "HSV(h={:5.1f}, s={:.2f}, v={:.2f})  -> {}{}".format(
        i, r, g, b, r, g, b, h, s, v, tag, extra))
print("=" * 60)

# Build UI groups
BUCKET_LABELS = {'pavers': 'Pavers / Deck',
                 'planting': 'Planting / Grass',
                 'water': 'Water'}

cluster_pixel_count = [0] * len(centers)
for y in range(proc_h):
    for x in range(proc_w):
        cluster_pixel_count[label_grid[y][x]] += 1

clusters_by_bucket = {'pavers': [], 'planting': [], 'water': []}
for cid, center in enumerate(centers):
    bucket = center_bucket[cid]
    if bucket == 'exclude':
        continue
    area_ft2 = cluster_pixel_count[cid] * scale_x * scale_y
    if area_ft2 < 2.0:
        continue
    clusters_by_bucket[bucket].append({
        'cluster_id': cid,
        'color': centers[cid],
        'area_ft2': area_ft2,
    })

for bkey, lst in clusters_by_bucket.items():
    lst.sort(key=lambda x: -x['area_ft2'])
    for n, g in enumerate(lst, 1):
        g['display_name'] = "{} #{}".format(BUCKET_LABELS[bkey], n)
        g['default_unchecked'] = False
        g['bucket'] = bkey

# Revit types
floor_types_all = list(FilteredElementCollector(doc).OfClass(FloorType).ToElements())
floor_type_names = sorted(set(get_name(ft) for ft in floor_types_all))
floor_type_by_name = {get_name(ft): ft for ft in floor_types_all}

topo_type_names = []
topo_type_by_name = {}
if HAS_TOPO:
    topo_types_all = list(FilteredElementCollector(doc).OfClass(ToposolidType).ToElements())
    topo_type_names = sorted(set(get_name(tt) for tt in topo_types_all))
    topo_type_by_name = {get_name(tt): tt for tt in topo_types_all}

if not floor_type_names:
    forms.alert('No Floor Types found in this project.', exitscript=True)

levels = list(FilteredElementCollector(doc).OfClass(Level).ToElements())
level_names_dict = {get_name(l): l for l in levels}
level_name_list = sorted(level_names_dict.keys())
if not level_name_list:
    forms.alert('No Levels found in this project.', exitscript=True)

default_level_name = None
try:
    vlvl = view.GenLevel
    if vlvl is not None:
        default_level_name = get_name(vlvl)
except:
    pass
if default_level_name is None:
    lowest = min(levels, key=lambda l: l.Elevation)
    default_level_name = get_name(lowest)

sections = [
    {'key': 'pavers',
     'title': 'PAVERS & DECKING',
     'subtitle': 'Assign a Floor Type to each detected paver / decking color.',
     'accent': CLR_HDR_PAVERS,
     'groups': clusters_by_bucket['pavers'],
     'types': floor_type_names},
    {'key': 'planting',
     'title': 'PLANTING & GRASS',
     'subtitle': 'Assign a Toposolid Type to each detected planting color.',
     'accent': CLR_HDR_PLANTING,
     'groups': clusters_by_bucket['planting'],
     'types': topo_type_names if HAS_TOPO else []},
    {'key': 'water',
     'title': 'WATER FEATURES',
     'subtitle': 'Assign a Toposolid Type to each detected water color.',
     'accent': CLR_HDR_WATER,
     'groups': clusters_by_bucket['water'],
     'types': topo_type_names if HAS_TOPO else []},
]

dlg = UnifiedMappingDialog(sections, level_name_list, default_level_name, detected_label)
dlg_result = dlg.ShowDialog()
if dlg_result != DialogResult.OK or dlg.result_maps is None:
    forms.alert('Cancelled - no elements created.', exitscript=True)

pavers_map   = dlg.result_maps.get('pavers', {})
planting_map = dlg.result_maps.get('planting', {})
water_map    = dlg.result_maps.get('water', {})
sel_level_name = dlg.result_level

if not sel_level_name:
    forms.alert('No level selected.', exitscript=True)
level = level_names_dict[sel_level_name]

if not pavers_map and not planting_map and not water_map:
    forms.alert('Nothing was checked - no elements created.', exitscript=True)

# ================================================================
# ZERO-OVERLAP PIPELINE WITH INTRA-BUCKET SEPARATION
# ================================================================

all_maps = {'pavers': pavers_map, 'planting': planting_map, 'water': water_map}

selected_clusters_by_bucket = {
    'pavers':   set(pavers_map.keys()),
    'planting': set(planting_map.keys()),
    'water':    set(water_map.keys()),
}
all_selected = set()
for s in selected_clusters_by_bucket.values():
    all_selected |= s

# STEP 1: build a per-CLUSTER raw mask (only pixels originally labeled as
# this cluster - no merging with other clusters in same bucket yet)
cluster_raw_masks = {}
for cid in all_selected:
    m = [[False]*proc_w for _ in range(proc_h)]
    for y in range(proc_h):
        row_l = label_grid[y]
        row_m = m[y]
        for x in range(proc_w):
            if row_l[x] == cid:
                row_m[x] = True
    cluster_raw_masks[cid] = m

# STEP 2: per-cluster morphological cleanup
# - Open removes tiny specks (single-pixel outliers)
# - Close absorbs small holes/patterns/equipment INSIDE this cluster only
print("Applying morphological open+close per cluster (radius={})...".format(MERGE_RADIUS))
cluster_clean_masks = {}
for cid, mask in cluster_raw_masks.items():
    opened = open_mask(mask, proc_w, proc_h, max(1, MERGE_RADIUS // 2))
    closed = close_mask(opened, proc_w, proc_h, MERGE_RADIUS)
    cluster_clean_masks[cid] = closed

# STEP 3: PRIORITY between buckets (water > planting > pavers)
# Build ordered list of clusters: all water first, then planting, then pavers
priority_order = ['water', 'planting', 'pavers']
ordered_clusters = []
for bkey in priority_order:
    cids = list(selected_clusters_by_bucket[bkey])
    # within bucket, larger area first (so bigger shapes claim their territory)
    cids.sort(key=lambda c: -cluster_pixel_count[c])
    for c in cids:
        ordered_clusters.append((bkey, c))

# STEP 4: Claim pixels one cluster at a time (both cross- and intra-bucket
# subtraction). This guarantees NO two clusters share any pixel.
print("Applying priority subtraction across ALL clusters (intra + cross bucket)...")
claimed = [[False]*proc_w for _ in range(proc_h)]
cluster_final_masks = {}
for (bkey, cid) in ordered_clusters:
    src = cluster_clean_masks[cid]
    final = [[False]*proc_w for _ in range(proc_h)]
    for y in range(proc_h):
        row_s = src[y]; row_c = claimed[y]; row_f = final[y]
        for x in range(proc_w):
            if row_s[x] and not row_c[x]:
                row_f[x] = True
                row_c[x] = True
    cluster_final_masks[cid] = final

# STEP 5: safety erosion so nothing touches
if SAFETY_EROSION_PX > 0:
    print("Applying safety erosion of {} px per cluster...".format(SAFETY_EROSION_PX))
    for cid in list(cluster_final_masks.keys()):
        cluster_final_masks[cid] = erode_mask(
            cluster_final_masks[cid], proc_w, proc_h, SAFETY_EROSION_PX)

# For hole classification
bucket_owner_grid = [[None]*proc_w for _ in range(proc_h)]
cluster_owner_grid = [[None]*proc_w for _ in range(proc_h)]
for (bkey, cid) in ordered_clusters:
    m = cluster_final_masks[cid]
    for y in range(proc_h):
        row_m = m[y]
        for x in range(proc_w):
            if row_m[x]:
                bucket_owner_grid[y][x] = bkey
                cluster_owner_grid[y][x] = cid

# ================================================================
# Extract loops per cluster with smart holes + shape rectification
# ================================================================

def pixel_to_model(px, py):
    x_ft = minX + px * scale_x
    y_ft = maxY - py * scale_y
    return XYZ(x_ft, y_ft, level.Elevation)


def points_to_curveloop(pts_px):
    if len(pts_px) < 3:
        return None
    pts = [pixel_to_model(px, py) for (px, py) in pts_px]
    cleaned = [pts[0]]
    for p in pts[1:]:
        if p.DistanceTo(cleaned[-1]) >= MIN_EDGE_LENGTH_FT:
            cleaned.append(p)
    while len(cleaned) > 2 and cleaned[0].DistanceTo(cleaned[-1]) < MIN_EDGE_LENGTH_FT:
        cleaned.pop()
    if len(cleaned) < 3:
        return None
    loop = CurveLoop()
    appended = 0
    for i in range(len(cleaned)):
        p1 = cleaned[i]
        p2 = cleaned[(i+1) % len(cleaned)]
        if p1.DistanceTo(p2) < MIN_EDGE_LENGTH_FT:
            continue
        try:
            loop.Append(Line.CreateBound(p1, p2))
            appended += 1
        except:
            return None
    if appended < 3:
        return None
    return loop


def process_loop_pts(raw_pts, pixset, reverse=False):
    """Simplify + optionally rectify to 90 deg + smooth."""
    if len(raw_pts) < 3:
        return None
    pts = simplify_collinear(raw_pts)
    if len(pts) < 3:
        return None
    simp = simplify_visvalingam(pts, SIMPLIFY_TOLERANCE_PX)
    if len(simp) < 3:
        simp = pts

    # Shape rectification: if the raw shape is dominantly orthogonal,
    # snap it to pure 90-degree corners. Otherwise smooth gently.
    if is_shape_orthogonal(simp):
        rect_pts = rectify_orthogonal(simp)
        if len(rect_pts) >= 3 and not polygon_self_intersects(rect_pts):
            simp = rect_pts
    else:
        # curved shape - apply inward smoothing to soften jagged pixel edges
        if INWARD_SMOOTH_PASSES > 0:
            smoothed = inward_smooth(simp, pixset, INWARD_SMOOTH_PASSES)
            if not polygon_self_intersects(smoothed):
                simp = smoothed

    if polygon_self_intersects(simp):
        return None
    if reverse:
        simp = list(reversed(simp))
    return simp


def loop_from_pixset(pixset, reverse=False):
    edges = blob_boundary_edges(pixset)
    if not edges:
        return None
    loops = chain_edges_to_loops(edges)
    if not loops:
        return None
    best = max(loops, key=lambda L: abs(signed_area(L)))
    processed = process_loop_pts(best, pixset, reverse=reverse)
    if processed is None:
        return None
    return points_to_curveloop(processed)


min_hole_pixels = int(MIN_HOLE_AREA_FT2 / (scale_x * scale_y))
print("Min hole pixel count: {} (= {:.1f} sq ft)".format(min_hole_pixels, MIN_HOLE_AREA_FT2))

cluster_creations = {}

for cid in all_selected:
    mask = cluster_final_masks.get(cid)
    if mask is None:
        continue
    my_bucket = None
    for bkey, cids in selected_clusters_by_bucket.items():
        if cid in cids:
            my_bucket = bkey
            break
    blobs = find_blobs_from_mask(mask, proc_w, proc_h, MIN_BLOB_PIXELS)
    creations = []
    for blob in blobs:
        pixset = set(blob['pixels'])
        outer = loop_from_pixset(pixset, reverse=False)
        if outer is None:
            continue
        holes = find_holes_in_blob(blob, proc_w, proc_h)
        kept_holes = []
        for hole in holes:
            hpixels = hole['pixels']
            if len(hpixels) < min_hole_pixels:
                continue
            # count how many hole pixels are owned by a DIFFERENT bucket
            # (equipment/furniture -> excluded -> owner = None -> ignored)
            other_bucket_count = 0
            for (hx, hy) in hpixels:
                owner_b = bucket_owner_grid[hy][hx]
                if owner_b is not None and owner_b != my_bucket:
                    other_bucket_count += 1
            frac = other_bucket_count / float(len(hpixels))
            if frac < MIN_OTHER_COVERAGE_FRAC:
                continue
            hloop = loop_from_pixset(set(hpixels), reverse=True)
            if hloop is not None:
                kept_holes.append(hloop)
        creations.append((outer, kept_holes))
    cluster_creations[cid] = creations

print("=" * 60)
print("CREATION SUMMARY PER CLUSTER:")
print("=" * 60)
for cid in sorted(cluster_creations.keys()):
    n = len(cluster_creations[cid])
    total_holes = sum(len(h) for _, h in cluster_creations[cid])
    r, g, b = [int(v) for v in centers[cid]]
    print("  Cluster #{:02d}  RGB({:3d},{:3d},{:3d})  Regions: {:2d}  Holes: {}".format(
        cid, r, g, b, n, total_holes))
print("=" * 60)

# ================================================================
# Create Revit elements
# ================================================================

t = Transaction(doc, 'Create Floors/Toposolids from Image')
t.Start()

created_floors = [0]
created_topo = [0]
skipped = [0]


def create_for_bucket(bucket_key, mapping, type_dict, is_topo):
    for cid, type_name in mapping.items():
        et = type_dict.get(type_name)
        if et is None:
            continue
        creations = cluster_creations.get(cid, [])
        for outer, holes in creations:
            all_loops = [outer] + holes
            try:
                if is_topo:
                    Toposolid.Create(doc, List[CurveLoop](all_loops), et.Id, level.Id)
                    created_topo[0] += 1
                else:
                    Floor.Create(doc, List[CurveLoop](all_loops), et.Id, level.Id)
                    created_floors[0] += 1
                continue
            except:
                pass
            try:
                if is_topo:
                    Toposolid.Create(doc, List[CurveLoop]([outer]), et.Id, level.Id)
                    created_topo[0] += 1
                else:
                    Floor.Create(doc, List[CurveLoop]([outer]), et.Id, level.Id)
                    created_floors[0] += 1
            except Exception as ex:
                logger.warning('element skipped: {}'.format(ex))
                skipped[0] += 1


create_for_bucket('pavers', pavers_map, floor_type_by_name, False)
if HAS_TOPO:
    create_for_bucket('planting', planting_map, topo_type_by_name, True)
    create_for_bucket('water',    water_map,    topo_type_by_name, True)
else:
    create_for_bucket('planting', planting_map, floor_type_by_name, False)
    create_for_bucket('water',    water_map,    floor_type_by_name, False)

t.Commit()

forms.alert(
    'Done!\n\n'
    'Image type: {}\n\n'
    'Floors created: {}\n'
    'Toposolids created: {}\n'
    'Regions skipped: {}'.format(
        detected_label, created_floors[0], created_topo[0], skipped[0]))