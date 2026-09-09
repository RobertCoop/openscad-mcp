# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Structured diagnostics on every tool response: `errors`, `warnings`,
  `deprecated`, `echo_output`, and `hints` (repair advice keyed to the
  message OpenSCAD printed), with file/line locations and TRACE call stacks
  folded into the record they belong to. Temp paths for inline content are
  shown as `<inline>`. (`src/openscad_mcp/diagnostics.py`)
- `mesh_health` on `export_model` (mesh formats) and `analyze_model`, parsed
  from the CGAL statistics OpenSCAD already prints: `manifold`
  true/false/null, vertex/edge/facet counts and `nef_volumes`.
- `export_model` accepts `csg`, `nef3` and `pdf`; `amf` is refused on
  binaries that removed it.
- `check_openscad` returns a cached capability record (version, snapshot
  flag, feature gates, supported formats, library paths) and an upgrade
  hint on 2021.01.
- `image_tokens` and `cached` in render metadata; `image_size` requests are
  clamped to `rendering.max_image_width/height` (default 1568, the vision
  long-edge limit) preserving aspect ratio.
- Config: `security.max_memory_mb` (default 4096), `MCP_ALLOWED_PATHS`,
  `MCP_MAX_MEMORY_MB`, `rendering.hard_warnings` / `MCP_HARD_WARNINGS`.
- A tool-surface budget test (`tests/test_correctness_fixes.py`) so schema
  growth is caught in CI.

### Changed
- `render_scad_to_png` returns a `RenderResult` (image + diagnostics +
  dependency and cache info) instead of a bare base64 string. Tools still
  accept a bare string from mocks.
- `render_perspectives` renders three views by default (front, top,
  isometric) instead of seven; every view is still available on request.
- `render_single` auto-frames (`--autocenter --viewall`) when called with
  no `view` and no explicit camera. A 2x3x1 mm part used to fill 0.2% of
  the frame.
- `--hardwarnings` is no longer passed by default. It stops evaluation at
  the first warning while still exiting 0, which produced blank renders and
  silently truncated `echo_output`. Warnings now arrive through diagnostics.
  Set `rendering.hard_warnings: true` to restore it.
- OpenSCAD discovery is memoised, checks `openscad-nightly` names and
  paths, reads the version from stdout or stderr, and prefers the newest
  binary. Previously every render re-executed `openscad --version`, even
  on cache hits.
- `rendering.max_concurrent` is now enforced: all OpenSCAD subprocesses go
  through the render semaphore, which was defined but never used.
- Startup messages go to the logger, never stdout (the stdio JSON-RPC
  channel).

### Fixed
- Renders reported `success: true` with a blank image when OpenSCAD exited
  0 after a failed `assert()`, an unknown module, or a missing include.
  stderr was only read on non-zero exit. Success is now derived from the
  parsed diagnostics and the image is returned alongside the errors.
- The render cache ignored files pulled in via `include <>`, `use <>`,
  `import()` and `surface()`: editing `params.scad` returned the previous
  PNG for up to 24 hours. Each cache entry now carries a manifest of every
  dependency (from `openscad -d`) with size, mtime and sha256, checked on
  lookup; includes that were missing at render time invalidate the entry
  when they appear; renders whose inputs changed mid-run are not cached.
  Entries without a manifest are treated as misses. The cache key also
  covers the OpenSCAD binary identity and uses length-prefixed fields.
- `resource://server/info` raised `TypeError: 'FunctionTool' object is not
  callable` on every read.
- `clear_cache` left manifest files behind and eviction ignored them.
- `get_project_files` dependency extraction missed `include` lines with
  trailing comments, several statements on one line, `import()` and
  `surface()`.
- `export_model` and `analyze_model` discarded stderr on success, losing
  the manifold warnings OpenSCAD printed.
- Timeouts discarded the partial stderr OpenSCAD had produced.
- The rendering semaphore was bound to the first event loop it saw.

### Security
- `allowed_paths` was enforced only on the `scad_file` argument (and on
  `include_paths` in the render path alone). Inline `scad_content` could
  read any readable numeric file via `include <...>` and return it through
  `echo_output`, or via `surface(file=...)` as geometry. Every OpenSCAD run
  now records its dependency closure with `-d` and withholds all output if
  any file lies outside `allowed_paths`, the library directories, or the
  temp dir. `include_paths` is validated in all four tools and export
  `output_path` must also be inside `allowed_paths`.
- OpenSCAD subprocesses run under an address-space limit
  (`security.max_memory_mb`, default 4 GB) applied through an exec wrapper
  on POSIX hosts, and in a new session.
- `echo_output` is capped (200 lines, 2000 chars per line).
- A startup warning is logged when `allowed_paths` is unset, and the
  README now documents the threat model.

## [0.3.0] - 2026-08-05

### Changed
- **Breaking:** `render_single`, `render_perspectives`, and `compare_renders` now
  return MCP `ImageContent` blocks (image + metadata list) instead of dicts with
  base64 strings, so MCP clients such as Claude Desktop display rendered images
  directly. ([#2], thanks [@frankhommers])
- **Breaking:** the `output_format` parameter was removed from `render_single`
  and `render_perspectives`; FastMCP now handles transport automatically. ([#2])
- `include_paths` is passed to OpenSCAD via the `OPENSCADPATH` environment
  variable. The previously emitted `-I` flag does not exist in OpenSCAD, so
  every call using `include_paths` failed; the feature now works. ([#10],
  thanks [@dawkacz])

### Fixed
- `validate_scad` reported `valid: false` with no errors for every input, because
  `-o /dev/null` was passed without an export format. Validation now uses
  `--export-format=csg`, which also avoids full CGAL evaluation and accepts
  2D-only models. ([#6], fixes [#5], thanks [@dawkacz])
- OpenSCAD special variables (`$fn`, `$fa`, `$fs`, `$t`, `$vpr`, `$vpt`, `$vpd`)
  were rejected by the variable-name validator, which made the `draft` and
  `high` quality presets unusable. ([#9], fixes [#3], thanks [@dawkacz])
- OpenSCAD failures with no recognized `ERROR:` line were reported as
  `valid: false` with empty `errors`, indistinguishable from a clean result.
  Unexplained non-zero exits now surface the raw output. ([#10], thanks
  [@dawkacz])

### Security
- `allowed_paths` containment was checked by string prefix on resolved paths,
  so allowing `/srv/project` also allowed sibling paths like
  `/srv/project-secrets` and `/srv/projects`. Containment is now decided with
  `Path.is_relative_to` after resolving both sides, which also blocks `..`
  traversal and symlink escapes. ([#8], fixes [#7], thanks [@dawkacz])

## [0.2.0] - 2026-02-15

### Added
- Comprehensive test coverage (300 tests, >80% line coverage)
- `--hardwarnings` flag on OpenSCAD invocations so warnings are surfaced
  (thanks [@NodeGuy])

### Changed
- Package version is read dynamically from `importlib.metadata`
- Updated fastmcp to 2.14.5 (mcp SDK 1.26.0)

## [0.1.0] - 2024-01-26

### Added
- Initial release of OpenSCAD MCP Server
- Full Model Context Protocol (MCP) implementation
- `render_single` tool for single view rendering
- `render_perspectives` tool for multiple standard views
- `check_openscad` tool for installation verification
- Support for OpenSCAD code strings and .scad files
- Customizable camera positions and targets
- Variable passing to OpenSCAD scripts
- Multiple color scheme support
- Smart response size management with automatic optimization
- Base64, file path, and compressed output formats
- Comprehensive test suite with 100+ tests
- Docker support for containerized deployment
- GitHub Actions CI/CD pipeline
- Support for Python 3.8 through 3.12
- Cross-platform compatibility (Linux, macOS, Windows)
- Environment-based configuration system
- Async/await support for non-blocking operations
- Resource caching capabilities
- Comprehensive error handling and validation
- Security features including path restrictions and dangerous function blocking
- Full documentation with examples
- MIT License

### Technical Details
- Built with FastMCP framework v2.11.3+
- Uses Pydantic for data validation
- Pillow for image processing
- PyYAML for configuration
- python-dotenv for environment management
- Async subprocess execution for OpenSCAD rendering
- Smart response compression for large images
- Configurable worker pool for parallel rendering

### Known Issues
- Animation rendering not yet supported
- STL export functionality pending implementation
- WebAssembly fallback not available in this version

## [0.0.1-alpha] - 2024-01-20

### Added
- Initial proof of concept
- Basic rendering functionality
- MCP protocol skeleton

---

## Version History

- **0.1.0** - First stable release with full MCP compliance
- **0.0.1-alpha** - Initial proof of concept

## Upgrade Guide

### From 0.0.x to 0.1.0
1. Update dependencies: `uv pip install --upgrade openscad-mcp`
2. Review new configuration options in `.env.example`
3. Update any custom integrations to use new response formats
4. Test rendering with new optimization features

## Compatibility Matrix

| OpenSCAD MCP | Python | OpenSCAD | FastMCP |
|--------------|--------|----------|---------|
| 0.1.0        | 3.8-3.12 | 2019.05+ | 2.11.3+ |
| 0.0.1-alpha  | 3.8+   | 2019.05+ | 2.0.0+  |

## Support

For questions and support, please use:
- GitHub Issues: https://github.com/yourusername/openscad-mcp-server/issues
- Discussions: https://github.com/yourusername/openscad-mcp-server/discussions

[Unreleased]: https://github.com/quellant/openscad-mcp/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/quellant/openscad-mcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/quellant/openscad-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/quellant/openscad-mcp/releases/tag/v0.1.0
[0.0.1-alpha]: https://github.com/quellant/openscad-mcp/releases/tag/v0.0.1-alpha

[#2]: https://github.com/quellant/openscad-mcp/pull/2
[#3]: https://github.com/quellant/openscad-mcp/issues/3
[#5]: https://github.com/quellant/openscad-mcp/issues/5
[#6]: https://github.com/quellant/openscad-mcp/pull/6
[#7]: https://github.com/quellant/openscad-mcp/issues/7
[#8]: https://github.com/quellant/openscad-mcp/pull/8
[#9]: https://github.com/quellant/openscad-mcp/pull/9
[#10]: https://github.com/quellant/openscad-mcp/pull/10
[@dawkacz]: https://github.com/dawkacz
[@frankhommers]: https://github.com/frankhommers
[@NodeGuy]: https://github.com/NodeGuy