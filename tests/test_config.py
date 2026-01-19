"""Unit tests for configuration loader."""

from __future__ import annotations

import importlib
import tomllib
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

import cta_eta.data_collection.config as cta_config
from cta_eta.data_collection.config import _load_config_from_path, load_config

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def temp_config_file(tmp_path: Path) -> Path:
    """Create a temporary config.toml file for testing."""
    config_file = tmp_path / "config.toml"
    config_content = """
[collection]
train_interval_seconds = 15
weather_interval_minutes = 30

[retry]
max_retry_attempts = 10
initial_backoff_seconds = 1

[storage]
data_path = "data"
partition_by = "daily"
"""
    config_file.write_text(config_content)
    return config_file


class TestLoadConfig:
    """Test cases for load_config function (public API)."""

    def test_load_config_calls_internal_function(self) -> None:
        """Test that load_config() calls _load_config_from_path with correct path."""
        # Arrange
        with patch(
            "cta_eta.data_collection.config._load_config_from_path"
        ) as mock_load:
            mock_load.return_value = {"test": {"key": "value"}}

            # Act
            result = load_config()

            # Assert
            mock_load.assert_called_once()
            call_path = mock_load.call_args[0][0]
            assert call_path.name == "config.toml"
            assert result == {"test": {"key": "value"}}


class TestLoadConfigFromPath:
    """Test cases for _load_config_from_path function (internal, testable API)."""

    def test_load_config_success(
        self, temp_config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test successful config loading with valid TOML and env vars."""
        # Arrange
        monkeypatch.setenv("CTA_API_KEY", "test_api_key_123")
        monkeypatch.setenv("CHIDATA_APP_TOK", "test_token")
        monkeypatch.setenv("CHIDATA_APP_SECRET", "test_secret")

        # Act
        config = _load_config_from_path(temp_config_file)

        # Assert
        assert "collection" in config
        assert config["collection"]["train_interval_seconds"] == 15  # noqa: PLR2004
        assert config["collection"]["weather_interval_minutes"] == 30  # noqa: PLR2004
        assert "secrets" in config
        assert config["secrets"]["cta_api_key"] == "test_api_key_123"
        assert config["secrets"]["chidata_app_token"] == "test_token"  # noqa: S105
        assert config["secrets"]["chidata_app_secret"] == "test_secret"  # noqa: S105

    def test_load_config_missing_env_vars(
        self, temp_config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test config loading when environment variables are missing."""
        # Arrange
        monkeypatch.delenv("CTA_API_KEY", raising=False)
        monkeypatch.delenv("CHIDATA_APP_TOK", raising=False)
        monkeypatch.delenv("CHIDATA_APP_SECRET", raising=False)
        with patch("cta_eta.data_collection.config.load_dotenv"):
            # Act
            config = _load_config_from_path(temp_config_file)

            # Assert
            assert config["secrets"]["cta_api_key"] == ""
            assert config["secrets"]["chidata_app_token"] == ""
            assert config["secrets"]["chidata_app_secret"] == ""

    def test_load_config_partial_env_vars(
        self, temp_config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test config loading with some env vars present and others missing."""
        # Arrange
        monkeypatch.setenv("CTA_API_KEY", "present_key")
        monkeypatch.delenv("CHIDATA_APP_TOK", raising=False)
        monkeypatch.delenv("CHIDATA_APP_SECRET", raising=False)
        with patch("cta_eta.data_collection.config.load_dotenv"):
            # Act
            config = _load_config_from_path(temp_config_file)

            # Assert
            assert config["secrets"]["cta_api_key"] == "present_key"
            assert config["secrets"]["chidata_app_token"] == ""
            assert config["secrets"]["chidata_app_secret"] == ""

    def test_load_config_missing_file(self, tmp_path: Path) -> None:
        """Test config loading when config.toml file doesn't exist."""
        # Arrange
        non_existent_path = tmp_path / "nonexistent.toml"

        # Act & Assert
        with pytest.raises(FileNotFoundError):
            _load_config_from_path(non_existent_path)

    def test_load_config_invalid_toml(self, tmp_path: Path) -> None:
        """Test config loading with invalid TOML syntax."""
        # Arrange
        invalid_config = tmp_path / "config.toml"
        invalid_config.write_text("[invalid toml syntax {")

        # Act & Assert
        with pytest.raises(tomllib.TOMLDecodeError):
            _load_config_from_path(invalid_config)

    def test_load_config_complex_types(self, tmp_path: Path) -> None:
        """Test config loading with various TOML data types."""
        # Arrange
        complex_config = tmp_path / "config.toml"
        complex_config.write_text("""
[numbers]
integer = 42
float = 3.14
negative = -10

[booleans]
true_val = true
false_val = false

[strings]
simple = "hello"
quoted = 'world'
""")

        # Act
        config = _load_config_from_path(complex_config)

        # Assert
        assert config["numbers"]["integer"] == 42  # noqa: PLR2004
        assert config["numbers"]["float"] == 3.14  # noqa: PLR2004
        assert config["numbers"]["negative"] == -10  # noqa: PLR2004
        assert config["booleans"]["true_val"] is True
        assert config["booleans"]["false_val"] is False
        assert config["strings"]["simple"] == "hello"
        assert config["strings"]["quoted"] == "world"

    def test_load_config_dotenv_loading(self, temp_config_file: Path) -> None:
        """Test that load_dotenv is called to load .env file."""
        # Arrange
        with patch("cta_eta.data_collection.config.load_dotenv") as mock_load_dotenv:
            # Act
            _load_config_from_path(temp_config_file)

            # Assert
            mock_load_dotenv.assert_called_once()

    def test_load_config_load_dotenv_executed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that load_dotenv() is actually called (line 28)."""
        # Arrange
        config_toml = tmp_path / "config.toml"
        config_toml.write_text("[test]\nvalue = 1")

        src_dir = tmp_path / "src" / "cta_eta" / "data_collection"
        src_dir.mkdir(parents=True)
        config_py = src_dir / "config.py"
        config_py.write_text("# Mock")

        original_file = cta_config.__file__
        monkeypatch.setattr(cta_config, "__file__", str(config_py))

        try:
            importlib.reload(cta_config)

            with patch("cta_eta.data_collection.config.load_dotenv") as mock_dotenv:
                # Act
                cta_config.load_config()

                # Assert - verify load_dotenv was called
                mock_dotenv.assert_called_once()
        finally:
            monkeypatch.setattr(cta_config, "__file__", original_file)
            importlib.reload(cta_config)

    def test_load_config_empty_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test config loading with empty TOML file."""
        # Arrange
        empty_config = tmp_path / "config.toml"
        empty_config.write_text("")
        monkeypatch.delenv("CTA_API_KEY", raising=False)
        monkeypatch.delenv("CHIDATA_APP_TOK", raising=False)
        monkeypatch.delenv("CHIDATA_APP_SECRET", raising=False)
        with patch("cta_eta.data_collection.config.load_dotenv"):
            # Act
            config = _load_config_from_path(empty_config)

            # Assert
            assert isinstance(config, dict)
            assert "secrets" in config
            assert config["secrets"]["cta_api_key"] == ""

    def test_load_config_secrets_always_present(
        self, temp_config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that secrets section is always added even if not in TOML."""
        # Arrange
        monkeypatch.delenv("CTA_API_KEY", raising=False)
        monkeypatch.delenv("CHIDATA_APP_TOK", raising=False)
        monkeypatch.delenv("CHIDATA_APP_SECRET", raising=False)
        with patch("cta_eta.data_collection.config.load_dotenv"):
            # Act
            config = _load_config_from_path(temp_config_file)

            # Assert
            assert "secrets" in config
            assert isinstance(config["secrets"], dict)
            assert "cta_api_key" in config["secrets"]
            assert "chidata_app_token" in config["secrets"]
            assert "chidata_app_secret" in config["secrets"]
