# -*- coding: utf-8 -*-
"""
Create water/liquid volume(s) from selected floor(s) - FIXED VERSION 2
- Bottom  = EXACT triangulation of the floor's own top face (point-to-point match, no resampling)
- Top     = flat cap, at (floor top + offset), reusing the SAME triangle connectivity
- Sides   = built from the real boundary edges of the face mesh (handles holes automatically)
- Builder targets a real Solid (Fallback = Abort) so bad geometry throws a clear error
  instead of silently producing a fragmented Mesh.
"""

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import (
    XYZ, Options, Floor, ElementId, DirectShape, BuiltInCategory,
    UnitUtils, TessellatedShapeBuilder, TessellatedFace,
    TessellatedShapeBuilderTarget, TessellatedShapeBuilderFallback
)
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

# ---------- units ----------
try:
    from Autodesk.Revit.DB import UnitTypeId
    def m2i(v): return UnitUtils.ConvertToInternalUnits(float(v), UnitTypeId.Meters)
except:
    from Autodesk.Revit.DB import DisplayUnitType
    def m2i(v): return UnitUtils.ConvertToInternalUnits(float(v), DisplayUnitType.DUT_METERS)

# ---------- selection ----------
class FloorFilter(ISelectionFilter):
    def AllowElement(self, e): return isinstance(e, Floor)
    def AllowReference(self, r, p): return False

try:
    refs = uidoc.Selection.PickObjects(ObjectType.Element, FloorFilter(), "Select floor(s)")
except:
    script.exit()

floors = [doc.GetElement(r.ElementId) for r in refs]
if not floors:
    script.exit()

# ---------- ask OFFSET above HIGHEST point of floor top ----------
val = forms.ask_for_string(
    default="0.30",
    prompt="Water depth above HIGHEST point of floor (meters):\n(e.g. 0.30 = 30cm of water above the highest peak)",
    title="Water depth")
if val is None:
    script.exit()
try:
    OFFSET = m2i(float(val))
except:
    forms.alert("Invalid number", exitscript=True)

# ---------- helpers ----------
def make_face(pts_list):
    from System.Collections.Generic import List
    lst = List[XYZ]()
    for p in pts_list:
        lst.Add(p)
    return TessellatedFace(lst, ElementId.InvalidElementId)

def get_top_faces(floor, normal_z_min=0.5):
    """Collect ALL upward-facing faces on the floor, not just the single highest one.
    Complex/sloped floors are often represented as several separate planar facets - using
    only the highest face silently drops every other facet, leaving gaps in the water."""
    opts = Options()
    opts.ComputeReferences = False
    geo = floor.get_Geometry(opts)
    faces = []
    for obj in geo:
        if not hasattr(obj, 'Faces'):
            continue
        for face in obj.Faces:
            try:
                bb = face.GetBoundingBox()
                from Autodesk.Revit.DB import UV
                mid = UV((bb.Min.U + bb.Max.U) * 0.5, (bb.Min.V + bb.Max.V) * 0.5)
                n = face.ComputeNormal(mid)
                if n.Z < normal_z_min:
                    continue
                faces.append(face)
            except:
                pass
    return faces

def get_faces_mesh(faces):
    """Triangulate multiple faces and merge into one combined vertex/triangle list,
    normalizing each triangle's winding to face +Z."""
    all_verts = []
    all_tris = []
    for face in faces:
        mesh = face.Triangulate(1.0)
        base = len(all_verts)
        for p in mesh.Vertices:
            all_verts.append(XYZ(p.X, p.Y, p.Z))
        for i in range(mesh.NumTriangles):
            t = mesh.get_Triangle(i)
            a, b, c = t.get_Index(0), t.get_Index(1), t.get_Index(2)
            v1, v2, v3 = all_verts[base + a], all_verts[base + b], all_verts[base + c]
            nx = (v2.Y - v1.Y) * (v3.Z - v1.Z) - (v2.Z - v1.Z) * (v3.Y - v1.Y)
            ny = (v2.Z - v1.Z) * (v3.X - v1.X) - (v2.X - v1.X) * (v3.Z - v1.Z)
            nz = (v2.X - v1.X) * (v3.Y - v1.Y) - (v2.Y - v1.Y) * (v3.X - v1.X)
            ia, ib, ic = base + a, base + b, base + c
            if nz < 0:
                ia, ib, ic = ic, ib, ia
            all_tris.append((ia, ib, ic))
    return all_verts, all_tris

def dedupe_verts(verts, tris, tol=5):
    """Face.Triangulate() does NOT guarantee shared vertex indices across triangles that
    touch along the same physical edge - two triangles can reference separate vertex
    entries at the same coordinate. Merge duplicates by rounded coordinate so shared
    edges are actually detected as shared (count == 2), not as false boundary edges."""
    key_to_idx = {}
    new_verts = []
    remap = []
    for v in verts:
        k = (round(v.X, tol), round(v.Y, tol), round(v.Z, tol))
        if k in key_to_idx:
            remap.append(key_to_idx[k])
        else:
            idx = len(new_verts)
            key_to_idx[k] = idx
            new_verts.append(v)
            remap.append(idx)
    new_tris = []
    for (a, b, c) in tris:
        ra, rb, rc = remap[a], remap[b], remap[c]
        if ra == rb or rb == rc or rc == ra:
            continue  # degenerate after merge, drop it
        new_tris.append((ra, rb, rc))
    return new_verts, new_tris

def find_boundary_edges(tris):
    """Edges used by exactly one triangle are boundary edges (outer loop AND any holes)."""
    edge_count = {}
    edge_dir = {}
    for (a, b, c) in tris:
        for (p, q) in [(a, b), (b, c), (c, a)]:
            key = (p, q) if p < q else (q, p)
            edge_count[key] = edge_count.get(key, 0) + 1
            edge_dir[key] = (p, q)
    return [edge_dir[k] for k, cnt in edge_count.items() if cnt == 1]

def chain_edges(edges):
    """Chain directional boundary edges into closed loops (works for outer + hole loops)."""
    nxt = {}
    for (a, b) in edges:
        nxt[a] = b
    loops = []
    visited = set()
    for (a, b) in edges:
        if a in visited:
            continue
        loop = [a]
        visited.add(a)
        cur = b
        while cur != a and cur not in visited:
            loop.append(cur)
            visited.add(cur)
            cur = nxt.get(cur)
            if cur is None:
                break
        loops.append(loop)
    return loops

# ---------- main ----------
created = 0
skipped = []
total = len(floors)

with forms.ProgressBar(title='Creating water volumes', cancellable=True) as pb:
    with revit.Transaction("Create Water Volumes"):
        for idx, floor in enumerate(floors):
            if pb.cancelled:
                break
            pb.update_progress(idx + 1, total)

            try:
                top_faces = get_top_faces(floor)
                if not top_faces:
                    skipped.append((floor.Id, "no top-facing faces found"))
                    continue

                verts, tris = get_faces_mesh(top_faces)
                if not tris:
                    skipped.append((floor.Id, "empty triangulation"))
                    continue
                verts, tris = dedupe_verts(verts, tris)
                if not tris:
                    skipped.append((floor.Id, "all triangles degenerate after merge"))
                    continue

                max_z = max(v.Z for v in verts)
                TOP_Z = max_z + OFFSET

                boundary_edges = find_boundary_edges(tris)
                loops = chain_edges(boundary_edges)
                if not loops:
                    skipped.append((floor.Id, "no boundary loop found"))
                    continue

                builder = TessellatedShapeBuilder()
                builder.OpenConnectedFaceSet(True)

                # BOTTOM: exact floor face triangulation, reversed to face down
                for (a, b, c) in tris:
                    v1, v2, v3 = verts[a], verts[b], verts[c]
                    builder.AddFace(make_face([v3, v2, v1]))

                # TOP: same connectivity, flattened to TOP_Z, facing up
                for (a, b, c) in tris:
                    v1, v2, v3 = verts[a], verts[b], verts[c]
                    t1 = XYZ(v1.X, v1.Y, TOP_Z)
                    t2 = XYZ(v2.X, v2.Y, TOP_Z)
                    t3 = XYZ(v3.X, v3.Y, TOP_Z)
                    builder.AddFace(make_face([t1, t2, t3]))

                # SIDES: one quad per boundary edge, bottom (real) -> top (flat)
                for loop in loops:
                    n = len(loop)
                    for i in range(n):
                        ia = loop[i]
                        ib = loop[(i + 1) % n]
                        b1 = verts[ia]
                        b2 = verts[ib]
                        t1 = XYZ(b1.X, b1.Y, TOP_Z)
                        t2 = XYZ(b2.X, b2.Y, TOP_Z)
                        builder.AddFace(make_face([b1, b2, t2, t1]))

                builder.CloseConnectedFaceSet()
                builder.Target = TessellatedShapeBuilderTarget.Solid
                builder.Fallback = TessellatedShapeBuilderFallback.Abort

                builder.Build()
                res = builder.GetBuildResult()
                geom = res.GetGeometricalObjects()
                if not geom or geom.Count == 0:
                    skipped.append((floor.Id, "builder produced no solid (non-manifold geometry)"))
                    continue

                ds = DirectShape.CreateElement(doc, ElementId(BuiltInCategory.OST_GenericModel))
                ds.SetShape(geom)
                ds.SetName("Water Volume")
                created += 1

            except Exception as ex:
                skipped.append((floor.Id, "error: {}".format(ex)))
                continue

msg = "Done.\nCreated: {}\nSkipped: {}".format(created, len(skipped))
if skipped:
    output.print_md("### Skipped")
    for fid, why in skipped:
        output.print_md("- `{}` : {}".format(fid, why))
forms.alert(msg)