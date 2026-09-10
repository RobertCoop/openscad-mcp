# OpenSCAD eval harness

A deterministic way to ask: **does a change to this server actually make a model's
OpenSCAD geometrically more correct?**

The harness never calls a language model. It scores `.scad` files that you produced
some other way, so it runs with no API key, and the same candidate files can be
re-scored later after the checks change. All it needs is OpenSCAD on `PATH` (or
`OPENSCAD_PATH`) and the standard library.

```
evals/
  README.md
  scorers.py            # STL/SVG measurement + the individual checks
  run.py                # CLI: score, compare, reference, measure
  fixtures/
    <task_id>/
      task.json         # prompt, checks, expected numbers, tags
      reference.scad    # a known-good solution, used as the harness self-test
```

## Quick start

```bash
# self-test: score the reference solutions, must be 100%
python evals/run.py reference

# score a directory of candidate files (one <task_id>.scad per task)
python evals/run.py score --candidates runs/baseline --label baseline --out baseline.json
python evals/run.py score --candidates runs/with-feature --label feature --out feature.json

# paired comparison with a sign test
python evals/run.py compare baseline.json feature.json
```

`--verbose` prints every check instead of only the failures. `--jobs N` scores N tasks
in parallel (default 4). `--timeout` is the per-export limit in seconds (default 120).

## Producing candidates

A candidate set is just a directory of files named after the task ids:

```
runs/baseline/
  bracket_l.scad
  box_with_lid.scad
  ...
```

The workflow for an A/B run:

1. Print the prompts.

   ```bash
   python -c "
   import json, pathlib
   for f in sorted(pathlib.Path('evals/fixtures').glob('*/task.json')):
       t = json.loads(f.read_text())
       print(t['id']); print(t['prompt']); print()
   "
   ```

2. Run your agent once per task under condition **A** (say, the MCP server with
   `render` disabled) and save its final OpenSCAD answer as
   `runs/a/<task_id>.scad`. Save only the code, no markdown fences.

3. Repeat under condition **B** with the feature enabled, into `runs/b/`.

4. Score both directories and compare them.

Keep everything else fixed between A and B: same model, same temperature, same number
of turns, same system prompt. The comparison is paired per task, so anything that
varies with the task itself cancels out, but anything that varies between the two runs
does not.

A missing `<task_id>.scad` is scored as a total failure for that task, which is usually
what you want: an agent that produced no usable code did not solve the task.

## What gets checked

Every check is computed from geometry that OpenSCAD itself exported, never from the
text of the candidate. Mesh measurements come from a small self-contained STL reader in
`scorers.py`: bounding box, signed volume by the divergence theorem, shells by
union-find over welded triangle edges, and watertightness from edge use counts.

| type | meaning | fields |
| --- | --- | --- |
| `bbox` | bounding box in mm | `expected: [w, d, h]`, `tol_mm` |
| `volume` | material volume in mm^3 | `expected`, `tol_pct` or `tol_mm3` |
| `watertight` | every edge shared by exactly two faces | `expected` (default `true`) |
| `solid_count` | outward-facing shells | `expected` |
| `cavity_count` | inward-facing shells, i.e. sealed voids | `expected` |
| `interference` | overlap volume between two parts | `parts: ["a();", "b();"]`, `max_overlap_mm3` |
| `predicate` | value of an echoed variable or expression | `scad`, `expected`, `tol_mm` or `tol_pct` |
| `bbox_2d` | bounding box of a 2D profile, via SVG | `expected: [w, h]`, `tol_mm` |
| `area_2d` | enclosed area of a 2D profile in mm^2 | `expected`, `tol_pct` or `tol_mm2` |

Two of these deserve a note.

**`interference`** writes a temporary file that does `use <candidate.scad>` and then
`intersection() { partA partB }`, exports it, and measures the volume that comes back.
Genuinely disjoint parts make OpenSCAD print *"Current top level object is empty."* and
exit 1, which the scorer reads as zero overlap. This is how the fit tasks tell a peg
that slides into its hole from a peg that is welded into it. Because it uses `use`, the
parts must be **modules** the candidate defined, and the task prompt says so.

**`predicate`** appends `echo("__EVAL__", i, <expr>);` after `include <candidate.scad>`
and reads the value back from OpenSCAD's echo output. It catches design intent that
survives in a variable but is invisible in the mesh, such as a clearance. It only works
if the candidate names the variable as asked, so every task with a predicate check says
the name in its prompt.

## Adding a task

1. `mkdir evals/fixtures/my_task`
2. Write `reference.scad`: a correct solution, with explicit `$fn` everywhere so the
   numbers are reproducible.
3. Measure it. Do not hand-compute the expectations.

   ```bash
   python evals/run.py measure evals/fixtures/my_task/reference.scad
   python evals/run.py measure evals/fixtures/my_task/reference.scad --2d   # 2D tasks
   ```

4. Write `task.json` with the measured numbers, rounded sensibly, and tolerances wide
   enough that a different but equally valid solution still passes. Round dimensions to
   the nominal value the prompt asked for when the mesh is within tolerance of it; that
   is fairer to a candidate that picked a different `$fn`.
5. Write the prompt the way a user would type it, with every dimension stated, plus the
   module and variable names any `interference` or `predicate` check depends on.
6. Re-run `python evals/run.py reference`. It must stay at 100%.
7. Sanity-check that the task can fail: break the reference on purpose (shift a
   dimension, remove a clearance) and confirm the check you care about goes red. A check
   that no plausible mistake can fail is measuring nothing.

## Honest limits

**15 tasks is a small sample.** The comparison is a paired sign test, so only tasks that
*flip* between A and B carry information. With 15 tasks you need at least 6 flips all in
the same direction, and none against, before the two-sided test reaches p < 0.05. That
is a minimum detectable difference of roughly 40 percentage points in task pass rate.
A feature that moves one or two tasks is invisible here, and a run where B wins 4 and
loses 1 is not evidence of anything. To resolve smaller effects you need more tasks,
several samples per task per condition, or both.

**Pass/fail per task is a blunt instrument.** A candidate that misses one dimension by
0.3 mm scores the same as one that emitted nothing. The per-check counts in the JSON
report are the finer-grained signal; the sign test deliberately ignores them because
checks within a task are not independent.

**These tasks are not a benchmark of OpenSCAD skill in general.** They are mechanical
parts with stated dimensions, chosen so that correctness is measurable. They say nothing
about aesthetics, parametric style, or how a model handles an underspecified request.

**The tool-selection question is a separate experiment.** The server currently exposes
12 tools. Whether an agent does better with 12, 20, or 44 of them is about tool choice
and context budget, not about geometry, and it needs its own harness that records which
tools were called. It is not what this measures.
