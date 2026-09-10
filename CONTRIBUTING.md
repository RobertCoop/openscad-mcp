# Contributing to OpenSCAD MCP Server

Thank you for your interest in contributing to the OpenSCAD MCP Server! We welcome contributions from the community and are excited to work with you.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Everyday Commands](#everyday-commands)
- [How the Code Is Laid Out](#how-the-code-is-laid-out)
- [Testing](#testing)
- [Design Rules That Should Not Be Undone](#design-rules-that-should-not-be-undone)
- [Adding a Parts-Catalog Entry](#adding-a-parts-catalog-entry)
- [Adding a Check Rule](#adding-a-check-rule)
- [How to Contribute](#how-to-contribute)
- [Pull Request Process](#pull-request-process)
- [Coding Standards](#coding-standards)
- [Documentation](#documentation)
- [Releasing](#releasing)
- [Reporting Issues](#reporting-issues)
- [Security](#security)

## Code of Conduct

By participating in this project, you agree to abide by our Code of Conduct:
- Be respectful and inclusive
- Welcome newcomers and help them get started
- Focus on constructive criticism
- Accept feedback gracefully
- Prioritize the community's best interests

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/yourusername/openscad-mcp.git
   cd openscad-mcp
   ```
3. **Add the upstream remote**:
   ```bash
   git remote add upstream https://github.com/robertcoop/openscad-mcp.git
   ```

## Development Setup

### Prerequisites

- Python 3.10 or higher (CI tests 3.10 and 3.12)
- [uv](https://docs.astral.sh/uv/) — the project's package manager, with a committed `uv.lock`
- Git
- OpenSCAD, for the tests and evals that use the real binary. Version 2021.01 is
  what CI runs and what the diagnostics parser is calibrated against. BOSL2 is
  needed for the purchased-parts catalog and the anchor probe; install it into
  `~/.local/share/OpenSCAD/libraries/BOSL2`.

Most of the test suite mocks the OpenSCAD subprocess and runs without it, so you
can get started before installing anything else.

### Installation

1. **Install uv**:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Sync the environment**:
   ```bash
   uv sync --extra dev
   ```

   Use `--extra dev`, not `--dev`. The dev tools are declared under
   `[project.optional-dependencies]`; there is no `[dependency-groups]` table, so
   `uv sync --dev` resolves to an empty group and *removes* pytest, ruff, black
   and mypy from the environment.

3. **Copy the environment configuration** (optional; every variable has a
   default):
   ```bash
   cp .env.example .env
   ```

## Everyday Commands

```bash
# Run the server over stdio
uv run openscad-mcp

# Run the assembly checker on a check file (exit code 0 pass / 1 fail / 2 error)
uv run openscad-mcp check examples/checks/turntable.yaml

# The whole suite, with coverage
uv run pytest

# One file, one class, one test — and skip the coverage gate while iterating
uv run pytest tests/test_check.py --no-cov
uv run pytest tests/test_openscad_mcp.py::TestParameterParsers --no-cov
uv run pytest -k "clearance" --no-cov

# Markers that select something: unit, config, integration, slow, performance,
# edge, render. --strict-markers is on, so a new marker has to be declared in
# the root pytest.ini or in pytest_configure in tests/conftest.py.
uv run pytest -m unit
uv run pytest -m "not slow"

# The deterministic geometry evals (needs OpenSCAD; must stay at 100%)
uv run python evals/run.py reference

# Lint, format, type check
uv run ruff check src/ tests/
uv run black --check src/ tests/
uv run mypy src/
```

### About the lint output

`ruff check src/ tests/` currently reports around 1,280 findings, `black --check`
wants to reformat 18 files, and `mypy src/` reports 77 errors. This is
pre-existing debt, not something your patch broke.

CI gates on a narrower rule set: `ruff check --select F,E9,B src/openscad_mcp/`.
Write new and modified code so it is clean under the full configuration, but do
**not** reformat the tree wholesale inside a functional change. A 4,000-line
whitespace diff buries the two lines that matter, and it is the single hardest
kind of PR to review. If you want to pay down the debt, that is welcome as its
own PR, one tool and one directory at a time.

There is no `.pre-commit-config.yaml` in the repo today, so `pre-commit install`
does nothing useful. `pre-commit` is still in the dev extra; adding a config is a
reasonable contribution.

## How the Code Is Laid Out

Everything lives in `src/openscad_mcp/`. There is no `core/`, `tools/` or
`resources/` package; the tool surface is one module and the machinery it calls
is split by subject.

| Module | Responsibility |
| --- | --- |
| `server.py` | The FastMCP instance, all 12 tools, the OpenSCAD subprocess wrappers (`_run_openscad`, `render_scad_to_png`, `_evaluate_scad`), the render cache, the parameter parsers, and the `openscad-mcp check` CLI |
| `assembly.py` | `Part`/`Frame`/`Assembly`, the YAML/JSON check-file grammar, `KNOWN_RULES`, frame composition as SCAD prefix text |
| `checks.py` | `RuleEngine`: one `rule_<name>` method per rule, all producing the same row shape, plus the aggregate `exit_code` |
| `geom.py` | The mesh kernel. BVH, exact triangle-triangle distance, generalized winding number, Möller-Trumbore ray casting, contact area, penetration depth, sweeps |
| `csgfeatures.py` | Parses OpenSCAD's CSG dump into cylinders with world transforms and polarity: holes, bosses, pattern grouping, cross-part alignment |
| `massprops.py` | Volume, centre of mass and inertia by tetrahedra; composition with per-part mass overrides |
| `printability.py` | Overhang patches, wall-thickness distribution by rays, islands by slicing, support estimate, orientation candidates |
| `analysis.py` | Static analysis over source text: BOSL2 `$var` shadowing lint with rewrite plans, constant dependency tracing, section-expression validation |
| `parts_catalog.py` + `parts/*.scad` | The purchased-parts catalog: the data, the generated BOSL2 files, and `self_check()` |
| `threemf.py` | The multi-object 3MF writer |
| `wrappers.py` | Source-level wrapping: hoisting `include`/`use` to file scope, inlining the model in `module __model()`, injecting caller variables, mapping wrapper line numbers back to the model |
| `diagnostics.py` | `parse_openscad_output()` turns stderr into a `Diagnostics` record with repair hints; `parse_deps_file()` reads the `-d` output; `extract_source_dependencies()` scans source statically |
| `camera.py` | The orthographic camera model, `fit_camera`, Pillow annotation, the spatial digest that precedes every image |
| `mesh.py` | Stdlib STL and SVG analysis: vertex welding, union-find components, signed volumes, edge census |
| `reference.py` | The shipped engineering data (fits, fasteners, inserts, bearings, joints, DFM, materials) with a confidence label on every number |
| `types.py` | Pydantic v2 models and enums |
| `utils/config.py` | Configuration from env vars, `.env` and YAML, with `get_config()`/`set_config()` |

`tests/` mirrors this roughly one file per module. `evals/` is a separate,
self-contained harness that imports nothing from the package.

## Testing

### How the tests stand in for OpenSCAD

Most tests patch `subprocess.run` and emulate what OpenSCAD would have done. A
mock that stands in for a render or an export has to write **both** files:

- the `-o` target, or the result is read as "OpenSCAD produced nothing"
- the `-d` dependency file, because the render cache builds its manifest from it
  and the security layer validates the dependency closure against it

`_write_outputs` in `tests/test_correctness_fixes.py` is the reference
implementation; reuse it rather than writing a new one.

```python
def _write_outputs(cmd, deps=(), png=PNG, stl=None):
    """Emulate OpenSCAD's side effects for a command line."""
    if "-o" in cmd:
        ...  # write the -o target
    if "-d" in cmd:
        ...  # write "<target>: \" followed by one tab-indented path per dep
```

Two more things that bite:

- **The exit code is not the signal.** OpenSCAD 2021.01 exits 0 on a failed
  `assert()`, an unknown module, a missing include and a non-closed polyhedron.
  A mock that returns `returncode=1` to simulate an error is testing a case that
  does not happen. Put the message on stderr instead.
- **Disable the cache when you are asserting on the command line.** A cache hit
  skips the subprocess entirely, so the `subprocess.run` mock is never called and
  the assertion fails for a reason that has nothing to do with the change.

### The `FunctionTool` pattern

On fastmcp 2.x, `@mcp.tool()` replaces the function with a `FunctionTool`
object, which is not callable, and the coroutine sits behind `.fn`. On
fastmcp 4.x the decorator returns the function itself. Tests use the
`hasattr` form; code inside the server uses `_tool_fn`, which handles both:

```python
from openscad_mcp.server import _tool_fn, render

render_fn = render.fn if hasattr(render, "fn") else render   # in tests
result = await render_fn(scad_content="cube(10);", image_size=[400, 400])

facts = await _tool_fn(measure)(scad_content=..., mode="printability")  # in server.py
```

Never write `tool.fn(...)` directly in `server.py`; a test guards against it,
and the `check` CLI once broke on every fresh install because of it.

### The tool-surface budget

`tests/test_correctness_fixes.py::TestToolSurfaceBudget` measures the serialized
JSON schema of every registered tool and fails if the total exceeds 21,000
characters or any single tool exceeds 3,600. That schema is paid on every
request, and tool-selection accuracy degrades as the surface grows.

The rule that follows from it: **a feature is a mode of an existing tool until it
proves it needs a tool of its own.** `render`, `measure`, `validate` and `check`
each absorbed what would otherwise have been four or five tools. `check` earned a
slot of its own because its subject is a relation between two parts rather than a
property of one, and because `mode="rules"` is a genuinely different verb. If you
are adding a tool, expect to be asked why it is not a mode.

If a schema grows past the per-tool cap, shorten the parameter descriptions
before raising the cap.

### Writing tests

- Put tests in `tests/`, in the file that matches the module.
- `asyncio_mode = auto`, so async tests need no marker.
- `tests/conftest.py` has an autouse `reset_environment` fixture that clears env
  vars, temp dirs and the memoised OpenSCAD discovery between tests. If you add
  module-level caching, clear it there too.
- Tests that need the real binary should skip when it is absent. Both
  `tests/test_check.py` and `tests/test_parts_catalog.py` define a
  `needs_openscad = pytest.mark.skipif(...)` for exactly this; follow the
  pattern.
- The coverage floor is 80% (`--cov-fail-under=80` in the root `pytest.ini`).

## Design Rules That Should Not Be Undone

Each of these looks like it could be simplified, and each is load-bearing. If
one of them is in your way, say so in the PR rather than quietly removing it.

- **Never union an assembly.** Each part is exported separately. CGAL's union
  destroys part identity non-uniformly, so a unioned assembly cannot answer
  "which part is this surface".
- **Flush contact is contact, never interference.** Two faces that touch at zero
  distance are a correct design, not a defect. The relation ladder is
  clear / contact / interference and it is driven by minimum distance.
- **`intersection()` volume is an opt-in cross-check, not the oracle.** It is
  available through `volume=true`. The geometric classification decides.
- **Every geometric row carries its `quality.fn`.** A distance inside the
  inscribed-polygon error bound `r * (1 - cos(180 / $fn))` is reported as
  `UNRESOLVED`, not as a number. Reporting a tessellation artefact as a
  measurement is worse than reporting nothing.
- **Variables are injected at file scope *and* module scope.** In the wrapped
  modes the model text lives inside `module __model()`, where `-D` does not
  reach. Assignments are appended to the module body, and repeated at file scope
  so a constant derived inside a hoisted include also sees them.
- **`include`/`use` are hoisted out of the wrapped module.** A library's
  `use <>` is a syntax error inside a module body, which is what broke BOSL2
  models before `hoist_source()` existed.
- **Wrapper files go in the server temp directory, never in the user's project.**
  Relative `import()` and `surface()` paths are rewritten absolute so the model
  still resolves them from there.
- **`--hardwarnings` stays off.** It aborts evaluation at the first warning while
  still exiting 0, which blanks renders and truncates echo output. Warnings
  surface through the parsed diagnostics instead. Never add it to a path that
  reads echo output.
- **The exit code never sets `success`.** Every tool computes `success` from the
  parsed diagnostics, and a render returns its image *together with* the errors.
- **`Volumes:` in the CGAL banner is not a body count.** A hollow shell and two
  disjoint cubes both report 3. It is reported as `nef_volumes`; manifoldness is
  gated on `Simple:` only.
- **Never cache a render without recording what it read.** Each cache entry has a
  manifest of every file from the `-d` output with size, mtime and sha256, and a
  hit requires all of them unchanged.
- **Flexible parameter parsing is intentional.** The parsers accept strings,
  lists, dicts, JSON and CSV because assistants send parameters in unpredictable
  shapes. Do not tighten them into a single accepted form.
- **Correctness outranks preserving the user's formatting.** When a toolchain
  limitation blocks a correct answer, the server may evaluate a patched private
  copy, or rewrite the project file when there are no name collisions, as
  `validate(mode="includes", autofix=true)` does.

## Adding a Parts-Catalog Entry

A catalogued part is three things that have to agree: the data, the generated
OpenSCAD file, and the self-check that compares them.

1. **Add the entry to `PARTS` in `parts_catalog.py`.** Every field in
   `test_required_fields_present` is mandatory: `id`, `aliases`, `name`,
   `category`, `scad_file`, `envelope_mm`, `body`, `mount`, `interface`,
   `mass_g`, `electrical`, `modules`, `anchors`, `confidence`, `sources`,
   `license_note` and `verify`. Every source carries a URL, and the entry's
   confidence is one of `standard`, `consensus` or `calibrate`.
2. **`verify` is required and must not be empty.** Numbers nobody publishes are
   not invented; they go in `verify` as "measure this", and the OpenSCAD file
   exposes them as an overridable parameter. Vendor disagreements go there too,
   naming both figures and both sources.
3. **Write `parts/<id>.scad`** following the conventions in
   [src/openscad_mcp/parts/README.md](./src/openscad_mcp/parts/README.md): origin
   at the centre of the bounding box, `include <BOSL2/std.scad>` unconditionally,
   `$fn` pinned on round features, masks authored for plain `difference()` and
   never wrapped in `tag()` or `diff()`.
4. **Declare all four modules** and record their names in the entry's `modules`
   dict (`solid`, `mask`, `mount_holes_mask`, `info`). The names are data, not a
   formula derived from the id: `28byj-48` gives `part_28byj48`,
   `part_28byj48_mask`, `part_28byj48_mount_holes_mask` and `part_28byj48_info`,
   while `lazy-susan-4in` gives `part_lazy_susan_4in`.
5. **Run the self-check against the real binary.** `self_check(part_id)` verifies
   the exported bounding box against `envelope_mm` to `SELF_CHECK_TOL_MM`, probes
   every named anchor, and requires that
   `difference() { solid(); mask(clr=0, bore_clr=0); }` come out **empty**. A new
   part is not finished until all three pass.
6. **Update `tests/test_parts_catalog.py`.** It is parametrized over the catalog,
   so most checks pick the new entry up for free, but the count assertion in
   `test_five_parts_with_unique_ids` needs bumping.

## Adding a Check Rule

1. Add the name to `KNOWN_RULES` in `assembly.py`. Anything not listed there is
   rejected by the check-file validator with the list of known rules.
2. Add a `rule_<name>` method to `RuleEngine` in `checks.py`. It takes the rule
   dict and returns a list of rows. Dispatch is by `getattr`, so the method name
   is the contract.
3. Emit the same row shape as the existing rules: `rule`, `subject`, `status`
   (`PASS` / `FAIL` / `UNRESOLVED`), the measured numbers, `why`, and the
   `quality` block. Use `UNRESOLVED` rather than guessing when the measurement is
   inside the tessellation error bound. `run()` already catches exceptions from a
   handler and turns them into an `UNRESOLVED` row, so one bad rule cannot kill
   the report.
4. Add tests in `tests/test_check.py`, including a case that fails, and extend
   `examples/checks/turntable.yaml` if the rule is broadly useful.
5. If the rule should also be reachable as a `check` mode, wire it in
   `server.py` and check the schema still fits the tool-surface budget.

## How to Contribute

### Types of Contributions

- **Bug reports** — GitHub Issues, with steps to reproduce, your OS, Python and
  OpenSCAD versions, and the full error output.
- **Feature requests** — an Issue with the "enhancement" label. Describe the use
  case. If it is a new tool, say why it cannot be a mode.
- **Code** — fixes, features, performance work, test coverage.
- **Documentation** — README, API.md, the skill, worked examples.

### Development Workflow

1. **Create a branch**:
   ```bash
   git checkout -b feature/your-feature-name   # or fix/issue-description
   ```

2. **Make your changes.** Add tests for new behaviour. Update the documentation
   that names what you changed — the tool tables in `README.md`, `AGENTS.md` and
   `skills/openscad-design/SKILL.md` all list tools and modes by name, and
   `CLAUDE.md` is loaded into every Claude Code session in this repo.

3. **Test**:
   ```bash
   uv run pytest
   ```

4. **Lint the code you touched**:
   ```bash
   uv run ruff check src/openscad_mcp/<file>.py
   uv run black src/openscad_mcp/<file>.py
   ```

5. **Commit**, using conventional commit messages:
   ```bash
   git commit -m "feat: add sweep certificate for linear motion"
   ```

   - `feat:` new features
   - `fix:` bug fixes
   - `docs:` documentation
   - `test:` test additions and changes
   - `refactor:` restructuring with no behaviour change
   - `perf:` performance
   - `chore:` maintenance
   - `release:` a version bump

6. **Push and open a Pull Request.**

## Pull Request Process

1. **Before submitting**:
   - All tests pass
   - Documentation updated where it names what you changed
   - An entry added to `CHANGELOG.md` under `## [Unreleased]`
   - Your branch is up to date with `main`

2. **PR description**: what it does, which issue it closes, any breaking
   changes. For anything geometric, include the numbers: the measurement before
   and after, or the check row that used to fail.

3. **Review**: a maintainer will review. Reviews may take a few days.

## Coding Standards

- PEP 8, with Black at line length 100 and Ruff's `E, W, F, I, B, C4, UP, ARG,
  SIM` rule set (see `pyproject.toml` for the ignores).
- Type hints on new functions. Mypy runs with `ignore_missing_imports` and
  `disallow_untyped_defs = false`, so annotations are encouraged rather than
  enforced.
- Python 3.10 is the floor and CI tests 3.10 and 3.12, so 3.11-only and
  3.12-only syntax is out.
- Small, single-purpose functions, and names that say what the value is rather
  than what type it has.

### Docstrings

Google style. For MCP tools, the docstring is not just for humans: FastMCP puts
it in the tool schema that the model reads, so keep it accurate and keep it
short. The tool-surface budget test will tell you when it is too long.

```python
def render_model(scad_content: str, **kwargs) -> str:
    """Render an OpenSCAD model to PNG.

    Args:
        scad_content: OpenSCAD code to render
        **kwargs: Additional rendering options

    Returns:
        Base64-encoded PNG image

    Raises:
        RuntimeError: If OpenSCAD rendering fails
        ValueError: If parameters are invalid
    """
```

## Documentation

| File | What it is for |
| --- | --- |
| `README.md` | Install, configure, the tool list, security model, troubleshooting |
| `API.md` | Every tool, every parameter, every response field |
| `DEPLOYMENT.md` | Running the server outside a local editor |
| `CLAUDE.md` | The architecture brief loaded into every Claude Code session here. Keep it tight |
| `AGENTS.md` | The short agent brief for non-Claude tooling |
| `skills/openscad-design/SKILL.md` | The design loop taught to the model |
| `src/openscad_mcp/parts/README.md` | The data policy for the purchased-parts catalog |
| `evals/README.md` | The eval harness and its honest limits |

There is no site generator configured. `mkdocs` is present in the `docs` extra
but there is no `mkdocs.yml`, so the Markdown files above are the documentation.
If you want to add a site, that is a fine PR; adding a build step that nobody
runs is not.

## Releasing

Maintainers only.

1. Bump the version in **both** `pyproject.toml` and `.claude-plugin/plugin.json`.
   They must match.
2. Move the `## [Unreleased]` entries in `CHANGELOG.md` into a new dated version
   heading.
3. Commit as `release: vX.Y.Z` and push to `main`.
4. Tag and push:
   ```bash
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```

`publish.yml` takes it from there: it checks the tag against the package version
and fails if they disagree, builds the sdist and wheel, smoke-tests the wheel in
a clean environment, uploads to PyPI via trusted publishing, and then creates
the GitHub release for the tag. The release notes are the `## [X.Y.Z]` section
of `CHANGELOG.md` (`.github/scripts/release_notes.sh` extracts it; GitHub's
generated notes are the fallback when the section is missing), the sdist and
wheel are attached, and the release is marked latest. Re-running the workflow
on an existing release re-uploads the assets rather than failing. There are no
secrets to configure.

## Reporting Issues

### Bug reports should include

1. **Environment**: OS and version, Python version, OpenSCAD version
   (`openscad --version`), package version.
2. **Steps to reproduce**: the smallest `.scad` file and the tool call that shows
   the problem.
3. **Output**: the full response, including `errors`, `warnings` and `hints`, and
   the stack trace if there is one.

For a wrong number rather than a crash, say what you measured on the real part
or what you expected and why.

### Issue template

```markdown
## Description
Brief description of the issue

## Environment
- OS: [e.g., Ubuntu 24.04]
- Python: [e.g., 3.12.9]
- OpenSCAD: [e.g., 2021.01]
- Package version: [e.g., 0.6.1]

## Steps to Reproduce
1. Step one
2. Step two

## Expected Behavior
What should happen

## Actual Behavior
What actually happens

## Error Output
Paste the response or traceback here
```

## Security

Do not report security vulnerabilities through public GitHub issues.

Use GitHub's private vulnerability reporting on the
[repository's Security tab](https://github.com/robertcoop/openscad-mcp/security),
which opens a private advisory visible only to the maintainers.

Note that path validation is off unless `MCP_ALLOWED_PATHS` is set; the README's
[Threat model](./README.md#threat-model) section describes what the server does
and does not defend against by default. A report that assumes validation is on
by default is describing configuration, not a vulnerability.

## Getting Help

- **Documentation**: the table above, starting with `README.md` and `API.md`
- **Issues**: search existing issues before opening a new one
- **Discussions**: GitHub Discussions for questions

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Thank you for contributing to OpenSCAD MCP Server! Your efforts help make this project better for everyone.
