"""
Tests for model CRUD operations in openscad_mcp.server.

Covers:
- _validate_model_name() -- name validation and normalisation
- _resolve_workspace() -- workspace directory resolution
- create_model -- creating new .scad files
- get_model -- reading .scad file contents
- update_model -- updating existing .scad files
- list_models -- listing .scad files in a workspace
- delete_model -- removing .scad files
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from openscad_mcp.server import (
    _validate_model_name,
    _resolve_workspace,
    create_model,
    get_model,
    update_model,
    list_models,
    delete_model,
)
from openscad_mcp.utils.config import Config, CacheConfig, SecurityConfig, set_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unwrap(tool):
    """Return the underlying async function from a FastMCP tool wrapper."""
    return tool.fn if hasattr(tool, "fn") else tool


# Unwrap MCP tool wrappers once at module level
_create_model = _unwrap(create_model)
_get_model = _unwrap(get_model)
_update_model = _unwrap(update_model)
_list_models = _unwrap(list_models)
_delete_model = _unwrap(delete_model)


# ============================================================================
# TestValidateModelName
# ============================================================================


class TestValidateModelName:
    """Tests for model name validation and normalisation."""

    def test_valid_name(self):
        """Simple hyphenated name gets .scad extension appended."""
        assert _validate_model_name("my-model") == "my-model.scad"

    def test_valid_name_with_extension(self):
        """Name already ending in .scad is returned as-is."""
        assert _validate_model_name("my-model.scad") == "my-model.scad"

    def test_valid_name_underscores(self):
        """Underscores are allowed in model names."""
        assert _validate_model_name("my_model_v2") == "my_model_v2.scad"

    def test_valid_name_digits(self):
        """Purely numeric suffixes are allowed."""
        assert _validate_model_name("model123") == "model123.scad"

    def test_path_traversal_dotdot(self):
        """Double-dot path traversal is rejected."""
        with pytest.raises(ValueError, match="must not contain"):
            _validate_model_name("../evil")

    def test_path_traversal_slash(self):
        """Forward slash is rejected."""
        with pytest.raises(ValueError, match="must not contain"):
            _validate_model_name("sub/dir")

    def test_path_traversal_backslash(self):
        """Backslash is rejected."""
        with pytest.raises(ValueError, match="must not contain"):
            _validate_model_name("sub\\dir")

    def test_special_chars(self):
        """Names starting with non-alphanumeric characters are rejected."""
        with pytest.raises(ValueError, match="must start with alphanumeric"):
            _validate_model_name("@model")

    def test_starts_with_hyphen(self):
        """Names starting with a hyphen are rejected."""
        with pytest.raises(ValueError, match="must start with alphanumeric"):
            _validate_model_name("-model")


# ============================================================================
# TestResolveWorkspace
# ============================================================================


class TestResolveWorkspace:
    """Tests for workspace directory resolution."""

    def test_default_workspace(self, tmp_path):
        """Without a workspace arg, defaults to config.temp_dir / 'models'."""
        cfg = Config(
            temp_dir=tmp_path,
            cache=CacheConfig(enabled=False, directory=tmp_path / "cache"),
        )
        set_config(cfg)

        ws = _resolve_workspace()
        assert ws == tmp_path / "models"
        assert ws.is_dir()

    def test_custom_workspace(self, tmp_path):
        """An explicit workspace path is resolved and created."""
        cfg = Config(
            temp_dir=tmp_path,
            cache=CacheConfig(enabled=False, directory=tmp_path / "cache"),
        )
        set_config(cfg)

        custom = tmp_path / "custom_ws"
        ws = _resolve_workspace(str(custom))
        assert ws == custom.resolve()
        assert ws.is_dir()

    def test_path_traversal_rejection(self, tmp_path):
        """Workspace paths with '..' are rejected."""
        cfg = Config(
            temp_dir=tmp_path,
            cache=CacheConfig(enabled=False, directory=tmp_path / "cache"),
        )
        set_config(cfg)

        with pytest.raises(ValueError, match="must not contain"):
            _resolve_workspace(str(tmp_path / ".." / "escape"))

    def test_directory_creation(self, tmp_path):
        """A non-existent workspace directory is automatically created."""
        cfg = Config(
            temp_dir=tmp_path,
            cache=CacheConfig(enabled=False, directory=tmp_path / "cache"),
        )
        set_config(cfg)

        deep = tmp_path / "a" / "b" / "c"
        assert not deep.exists()

        ws = _resolve_workspace(str(deep))
        assert ws.is_dir()


# ============================================================================
# TestCreateModel
# ============================================================================


class TestCreateModel:
    """Tests for the create_model MCP tool."""

    async def test_create_success(self, configured_env):
        """Successfully create a new model file."""
        tmp_path, _cfg = configured_env
        result = await _create_model(name="box", content="cube(10);")

        assert result["success"] is True
        assert result["name"] == "box.scad"

        created = Path(result["path"])
        assert created.exists()
        assert created.read_text() == "cube(10);"

    async def test_create_duplicate_rejection(self, configured_env):
        """Creating a model that already exists returns an error."""
        tmp_path, _cfg = configured_env
        await _create_model(name="dup", content="cube(1);")
        result = await _create_model(name="dup", content="sphere(2);")

        assert result["success"] is False
        assert "already exists" in result["error"]

    async def test_create_invalid_name(self, configured_env):
        """An invalid name returns an error without crashing."""
        result = await _create_model(name="@invalid", content="cube(1);")
        assert result["success"] is False
        assert "error" in result

    async def test_create_adds_extension(self, configured_env):
        """A name without .scad automatically gets the extension."""
        result = await _create_model(name="mymodel", content="cube(5);")
        assert result["success"] is True
        assert result["name"] == "mymodel.scad"

    async def test_create_custom_workspace(self, configured_env):
        """Models can be created in a custom workspace directory."""
        tmp_path, _cfg = configured_env
        custom_ws = tmp_path / "custom"

        result = await _create_model(
            name="ws-test",
            content="cube(3);",
            workspace=str(custom_ws),
        )
        assert result["success"] is True
        assert str(custom_ws) in result["path"]
        assert Path(result["path"]).exists()


# ============================================================================
# TestGetModel
# ============================================================================


class TestGetModel:
    """Tests for the get_model MCP tool."""

    async def test_get_success(self, configured_env):
        """Retrieve a previously created model."""
        await _create_model(name="readable", content="sphere(8);")
        result = await _get_model(name="readable")

        assert result["success"] is True
        assert result["content"] == "sphere(8);"
        assert result["name"] == "readable.scad"
        assert "size_bytes" in result

    async def test_get_not_found(self, configured_env):
        """Getting a model that does not exist returns an error."""
        result = await _get_model(name="ghost")
        assert result["success"] is False
        assert "not found" in result["error"]

    async def test_get_invalid_name(self, configured_env):
        """Getting a model with an invalid name returns an error."""
        result = await _get_model(name="@bad")
        assert result["success"] is False
        assert "error" in result


# ============================================================================
# TestUpdateModel
# ============================================================================


class TestUpdateModel:
    """Tests for the update_model MCP tool."""

    async def test_update_success(self, configured_env):
        """Update an existing model with new content."""
        await _create_model(name="mutable", content="cube(1);")
        result = await _update_model(name="mutable", content="sphere(99);")

        assert result["success"] is True

        # Verify content on disk
        get_result = await _get_model(name="mutable")
        assert get_result["content"] == "sphere(99);"

    async def test_update_not_found(self, configured_env):
        """Updating a model that does not exist returns an error."""
        result = await _update_model(name="missing", content="cube(1);")
        assert result["success"] is False
        assert "not found" in result["error"]

    async def test_update_invalid_name(self, configured_env):
        """Updating with an invalid name returns an error."""
        result = await _update_model(name="@nope", content="cube(1);")
        assert result["success"] is False
        assert "error" in result


# ============================================================================
# TestListModels
# ============================================================================


class TestListModels:
    """Tests for the list_models MCP tool."""

    async def test_list_empty(self, configured_env):
        """Empty workspace returns count=0 and models=[]."""
        result = await _list_models()
        assert result["success"] is True
        assert result["count"] == 0
        assert result["models"] == []

    async def test_list_scad_only(self, configured_env):
        """Only .scad files appear in the listing."""
        tmp_path, _cfg = configured_env
        ws = tmp_path / "models"
        ws.mkdir(exist_ok=True)

        (ws / "real.scad").write_text("cube(1);")
        (ws / "notes.txt").write_text("not a model")

        result = await _list_models()
        assert result["count"] == 1
        assert result["models"][0]["name"] == "real.scad"

    async def test_list_sorted(self, configured_env):
        """Models are returned in alphabetical order."""
        await _create_model(name="beta", content="cube(2);")
        await _create_model(name="alpha", content="cube(1);")

        result = await _list_models()
        names = [m["name"] for m in result["models"]]
        assert names == ["alpha.scad", "beta.scad"]

    async def test_list_metadata_fields(self, configured_env):
        """Each model entry includes name, path, size_bytes, and modified."""
        await _create_model(name="meta", content="cube(7);")
        result = await _list_models()

        entry = result["models"][0]
        assert "name" in entry
        assert "path" in entry
        assert "size_bytes" in entry
        assert "modified" in entry
        assert entry["size_bytes"] > 0


# ============================================================================
# TestDeleteModel
# ============================================================================


class TestDeleteModel:
    """Tests for the delete_model MCP tool."""

    async def test_delete_success(self, configured_env):
        """Successfully delete an existing model."""
        create_result = await _create_model(name="doomed", content="cube(1);")
        file_path = Path(create_result["path"])
        assert file_path.exists()

        result = await _delete_model(name="doomed")
        assert result["success"] is True
        assert not file_path.exists()

    async def test_delete_not_found(self, configured_env):
        """Deleting a model that does not exist returns an error."""
        result = await _delete_model(name="phantom")
        assert result["success"] is False
        assert "not found" in result["error"]

    async def test_delete_invalid_name(self, configured_env):
        """Deleting with an invalid name returns an error."""
        result = await _delete_model(name="@bad")
        assert result["success"] is False
        assert "error" in result
