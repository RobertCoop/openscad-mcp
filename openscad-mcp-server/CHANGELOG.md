# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Placeholder for upcoming features

### Changed
- Placeholder for upcoming changes

### Deprecated
- Placeholder for deprecated features

### Removed
- Placeholder for removed features

### Fixed
- Placeholder for bug fixes

### Security
- Placeholder for security updates

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

[Unreleased]: https://github.com/yourusername/openscad-mcp-server/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/yourusername/openscad-mcp-server/releases/tag/v0.1.0
[0.0.1-alpha]: https://github.com/yourusername/openscad-mcp-server/releases/tag/v0.0.1-alpha