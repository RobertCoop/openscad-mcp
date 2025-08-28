# Contributing to OpenSCAD MCP Server

Thank you for your interest in contributing to the OpenSCAD MCP Server! We welcome contributions from the community and are excited to work with you.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [How to Contribute](#how-to-contribute)
- [Pull Request Process](#pull-request-process)
- [Coding Standards](#coding-standards)
- [Testing](#testing)
- [Documentation](#documentation)
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
   git clone https://github.com/yourusername/openscad-mcp-server.git
   cd openscad-mcp-server
   ```
3. **Add the upstream remote**:
   ```bash
   git remote add upstream https://github.com/original/openscad-mcp-server.git
   ```

## Development Setup

### Prerequisites

- Python 3.8 or higher
- OpenSCAD installed on your system
- Git
- uv (recommended) or pip

### Installation

1. **Install uv** (recommended):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Create a virtual environment**:
   ```bash
   uv venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install the package in development mode**:
   ```bash
   uv pip install -e ".[dev,test,docs]"
   ```

4. **Install pre-commit hooks**:
   ```bash
   pre-commit install
   ```

5. **Copy the environment configuration**:
   ```bash
   cp .env.example .env
   # Edit .env with your local settings
   ```

## How to Contribute

### Types of Contributions

#### 1. Bug Reports
- Use the GitHub Issues page
- Include detailed steps to reproduce
- Provide system information (OS, Python version, OpenSCAD version)
- Include error messages and stack traces

#### 2. Feature Requests
- Open a GitHub Issue with the "enhancement" label
- Describe the use case and benefits
- Provide examples if possible
- Be open to discussion and alternatives

#### 3. Code Contributions
- Bug fixes
- New features
- Performance improvements
- Test coverage improvements
- Documentation updates

#### 4. Documentation
- README improvements
- API documentation
- Usage examples
- Tutorial content

### Development Workflow

1. **Create a branch** for your feature or fix:
   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/issue-description
   ```

2. **Make your changes**:
   - Write clean, readable code
   - Follow the coding standards
   - Add tests for new functionality
   - Update documentation as needed

3. **Test your changes**:
   ```bash
   # Run all tests
   pytest
   
   # Run with coverage
   pytest --cov=openscad_mcp
   
   # Run specific test file
   pytest tests/test_server.py
   ```

4. **Format and lint your code**:
   ```bash
   # Format with black
   black src/ tests/
   
   # Lint with ruff
   ruff check src/ tests/
   
   # Type check with mypy
   mypy src/
   ```

5. **Commit your changes**:
   ```bash
   git add .
   git commit -m "feat: add new rendering feature"
   ```
   
   Use conventional commit messages:
   - `feat:` for new features
   - `fix:` for bug fixes
   - `docs:` for documentation
   - `test:` for test additions/changes
   - `refactor:` for code refactoring
   - `style:` for formatting changes
   - `perf:` for performance improvements
   - `chore:` for maintenance tasks

6. **Push to your fork**:
   ```bash
   git push origin feature/your-feature-name
   ```

7. **Create a Pull Request** on GitHub

## Pull Request Process

1. **Before submitting**:
   - Ensure all tests pass
   - Update documentation if needed
   - Add an entry to CHANGELOG.md (in the Unreleased section)
   - Ensure your branch is up-to-date with main

2. **PR Description**:
   - Clearly describe what the PR does
   - Reference any related issues
   - Include screenshots for UI changes
   - List any breaking changes

3. **Review Process**:
   - A maintainer will review your PR
   - Address any requested changes
   - Be patient - reviews may take a few days
   - Once approved, a maintainer will merge your PR

## Coding Standards

### Python Style Guide

- Follow PEP 8
- Use Black for formatting (line length: 100)
- Use type hints where appropriate
- Write descriptive variable and function names
- Keep functions small and focused

### Project Structure

```
src/openscad_mcp/
├── __init__.py       # Package initialization
├── server.py         # Main server implementation
├── types.py          # Pydantic models
├── core/            # Core logic
├── tools/           # MCP tools
├── resources/       # MCP resources
└── utils/           # Utilities
```

### Example Code Style

```python
from typing import Optional, List
from pydantic import BaseModel

class RenderParams(BaseModel):
    """Parameters for rendering OpenSCAD models."""
    
    scad_content: Optional[str] = None
    camera_position: List[float] = [70, 70, 70]
    image_size: List[int] = [800, 600]
    
    def validate_params(self) -> bool:
        """Validate rendering parameters.
        
        Returns:
            bool: True if parameters are valid
        """
        if not self.scad_content and not self.scad_file:
            return False
        return True
```

## Testing

### Writing Tests

- Place tests in `tests/` directory
- Mirror the source structure
- Use pytest fixtures for common setup
- Aim for >80% code coverage

### Test Example

```python
import pytest
from openscad_mcp.server import mcp

@pytest.mark.asyncio
async def test_render_single():
    """Test single view rendering."""
    result = await mcp.call_tool("render_single", {
        "scad_content": "cube([10, 10, 10]);",
        "image_size": [400, 400]
    })
    
    assert result["success"] is True
    assert "data" in result or "path" in result
```

### Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run with coverage
pytest --cov=openscad_mcp --cov-report=html

# Run specific test
pytest tests/test_server.py::test_render_single

# Run tests matching pattern
pytest -k "render"
```

## Documentation

### Docstring Format

Use Google-style docstrings:

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
        
    Examples:
        >>> image = render_model("cube([10, 10, 10]);")
        >>> print(f"Rendered image: {len(image)} bytes")
    """
```

### Building Documentation

```bash
# Install documentation dependencies
uv pip install -e ".[docs]"

# Build documentation
mkdocs build

# Serve documentation locally
mkdocs serve
```

## Reporting Issues

### Bug Reports Should Include

1. **Environment Information**:
   - OS and version
   - Python version
   - OpenSCAD version
   - Package version

2. **Steps to Reproduce**:
   - Minimal code example
   - Expected behavior
   - Actual behavior

3. **Error Information**:
   - Full error message
   - Stack trace
   - Any relevant logs

### Issue Template

```markdown
## Description
Brief description of the issue

## Environment
- OS: [e.g., Ubuntu 22.04]
- Python: [e.g., 3.11.5]
- OpenSCAD: [e.g., 2021.01]
- Package Version: [e.g., 0.1.0]

## Steps to Reproduce
1. Step one
2. Step two
3. ...

## Expected Behavior
What should happen

## Actual Behavior
What actually happens

## Error Output
```
Paste error messages here
```

## Additional Context
Any other relevant information
```

## Security

### Reporting Security Issues

**DO NOT** report security vulnerabilities through GitHub issues.

Instead, please email security@example.com with:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will respond within 48 hours and work with you to address the issue.

## Getting Help

- **Documentation**: Check the README and docs/
- **Issues**: Search existing issues before creating new ones
- **Discussions**: Use GitHub Discussions for questions
- **Chat**: Join our community chat (if available)

## Recognition

Contributors will be recognized in:
- The CHANGELOG.md file
- The AUTHORS file (for significant contributions)
- Release notes

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Thank you for contributing to OpenSCAD MCP Server! Your efforts help make this project better for everyone.