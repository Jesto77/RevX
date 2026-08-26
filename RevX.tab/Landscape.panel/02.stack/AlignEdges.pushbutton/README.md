# Align Edges (pyRevit)

## Workflow
1. **Pick Slab** - choose the Floor/Toposolid you want to re-grade.
2. **Align Method** - pick what kind of neighboring element to match:
   Slabs, Walls, Curbs, or Stairs.
3. **Pick Element(s)** - pick one or more of that kind (Finish on the
   options bar when done).
4. **Points Offset** - optional constant offset (feet) added on top of the
   matched elevation. 0 = exact match.
5. **Run** - in one transaction/undo step, every shape-edit point on the
   target slab that sits at the same XY location as a picked neighbor's
   relevant edge is snapped to that neighbor's elevation/slope at that
   spot (not just a flat height - it follows slope along a sloped
   reference too).

The **Keep dialog open** checkbox lets you re-pick adjacent elements and
run again against the same target slab without reopening the tool.

## Which edge counts as "the reference" per method
This is the one part I had to make judgment calls on, since I don't have
FOREground's internal logic - only the described end result ("coincident
in the xy dimension"). Current choices, all in `get_reference_curves()`:

- **Slabs**: top-facing faces' boundary edges (a slab's finished surface).
- **Stairs**: top-facing faces' boundary edges (tread nosings + landings).
- **Curbs**: the railing's actual `TopRail` path curve when available
  (falls back to top-facing face edges if that API call fails).
- **Walls**: bottom-facing faces' boundary edges (wall base) - the
  assumption being paving meets a wall at its base, not its top. If your
  use case is actually "meet the top of a low wall," flip `direction=-1`
  to `direction=1` in the `Walls` branch.

If any of these don't match what you see in FOREground, tell me which one
and I'll adjust the direction/edge-selection logic for that method.

## Installing
Drop the `AlignEdges.pushbutton` folder into a panel inside your
`RevX.extension`, same as your other tools. Reload pyRevit.

## Version notes (2023-2027)
- **Toposolid** only exists from Revit 2024 on - on 2023 it's simply not
  offered as pickable (`TOPOSOLID_CLASS` is `None` there); Floors still
  work.
- `get_shape_editor()` tries both `GetSlabShapeEditor()` and the older
  `SlabShapeEditor` property, since that surface has moved across
  versions.
- I don't have verified API documentation for 2026/2027 past my knowledge
  cutoff - test on a non-production file first. If something changed
  again, `get_shape_editor()` and the Railing `TopRail`/`GetPath()` calls
  are the most likely spots to need updating.

## Known limitations
- Curved reference edges (arcs/splines) are matched via a tessellated
  approximation, not exact analytic projection - fine for grading
  tolerances, not for tight-radius precision work.
- "Coincident in XY" uses a 0.05 ft (~0.6") tolerance (`EDGE_TOLERANCE`
  near the top of `script.py`) - tune it if your geometry is coarser or
  finer than that.
- This is a fresh implementation of the described behavior, not a copy of
  FOREground's code - exact dialog wording/edge cases may differ from the
  original.
