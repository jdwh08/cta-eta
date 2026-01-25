"""Unit tests for configuration loader."""

from __future__ import annotations

import importlib
import tomllib
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

import cta_eta.data_collection.config as cta_config
from cta_eta.data_collection.config import (
    _load_config_from_path,
    _sanitize_config_for_logging,
    get_config_section,
    load_config,
)
from cta_eta.data_collection.config import (
    validate_config_secrets as validate_config,
)

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


class TestSanitizeConfigForLogging:
    """Test cases for _sanitize_config_for_logging (internal, redacts secrets)."""

    def test_sanitize_redacts_sensitive_keys(self) -> None:
        # Arrange
        config = {
            "secrets": {
                "cta_api_key": "sk-abc",
                "chidata_app_token": "tok",
                "chidata_app_secret": "sec",
                "openweathermap_api_key": "owm",
                "other": "keep",
            },
        }

        # Act
        out = _sanitize_config_for_logging(config)

        # Assert
        assert out["secrets"]["cta_api_key"] == "<REDACTED>"
        assert out["secrets"]["chidata_app_token"] == "<REDACTED>"  # noqa: S105
        assert out["secrets"]["chidata_app_secret"] == "<REDACTED>"  # noqa: S105
        assert out["secrets"]["openweathermap_api_key"] == "<REDACTED>"
        assert out["secrets"]["other"] == "keep"

    def test_sanitize_redacts_key_in_name(self) -> None:
        # Arrange
        config = {"sect": {"my_custom_key": "v", "api_key_extra": "v2"}}

        # Act
        out = _sanitize_config_for_logging(config)

        # Assert
        assert out["sect"]["my_custom_key"] == "<REDACTED>"
        assert out["sect"]["api_key_extra"] == "<REDACTED>"

    def test_sanitize_redacts_secret_in_name(self) -> None:
        # Arrange
        config = {"sect": {"my_secret": "x", "client_secret": "y"}}

        # Act
        out = _sanitize_config_for_logging(config)

        # Assert
        assert out["sect"]["my_secret"] == "<REDACTED>"  # noqa: S105
        assert out["sect"]["client_secret"] == "<REDACTED>"  # noqa: S105

    def test_sanitize_redacts_token_in_name(self) -> None:
        # Arrange
        config = {"sect": {"auth_token": "t", "refresh_token": "r"}}

        # Act
        out = _sanitize_config_for_logging(config)

        # Assert
        assert out["sect"]["auth_token"] == "<REDACTED>"  # noqa: S105
        assert out["sect"]["refresh_token"] == "<REDACTED>"  # noqa: S105

    def test_sanitize_preserves_non_sensitive(self) -> None:
        # Arrange
        config = {"collection": {"train_interval_seconds": 15, "foo": "bar"}}

        # Act
        out = _sanitize_config_for_logging(config)

        # Assert
        assert out["collection"]["train_interval_seconds"] == 15
        assert out["collection"]["foo"] == "bar"

    def test_sanitize_section_not_dict_copied_as_is(self) -> None:
        # Arrange
        config = {"str_section": "string_val", "int_section": 42}

        # Act
        out = _sanitize_config_for_logging(config)

        # Assert
        assert out["str_section"] == "string_val"
        assert out["int_section"] == 42

    def test_sanitize_empty_config(self) -> None:
        # Arrange
        config: dict[str, dict[str, str | int | float | bool]] = {}

        # Act
        out = _sanitize_config_for_logging(config)

        # Assert
        assert out == {}


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
        assert config["collection"]["train_interval_seconds"] == 15
        assert config["collection"]["weather_interval_minutes"] == 30
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
        assert config["numbers"]["integer"] == 42
        assert config["numbers"]["float"] == 3.14
        assert config["numbers"]["negative"] == -10
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

    def test_load_config_openweathermap_in_secrets(
        self, temp_config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setenv("OPENWEATHERMAP_API_KEY", "owm_key_123")
        monkeypatch.delenv("CTA_API_KEY", raising=False)
        monkeypatch.delenv("CHIDATA_APP_TOK", raising=False)
        monkeypatch.delenv("CHIDATA_APP_SECRET", raising=False)
        with patch("cta_eta.data_collection.config.load_dotenv"):
            # Act
            config = _load_config_from_path(temp_config_file)

            # Assert
            assert config["secrets"]["openweathermap_api_key"] == "owm_key_123"

    def test_load_config_strips_whitespace_from_env(
        self, temp_config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setenv("CTA_API_KEY", "  key_with_spaces  ")
        monkeypatch.delenv("CHIDATA_APP_TOK", raising=False)
        monkeypatch.delenv("CHIDATA_APP_SECRET", raising=False)
        with patch("cta_eta.data_collection.config.load_dotenv"):
            # Act
            config = _load_config_from_path(temp_config_file)

            # Assert
            assert config["secrets"]["cta_api_key"] == "key_with_spaces"


class TestValidateConfig:
    """Test cases for validate_config (credentials by feature)."""

    def test_validate_config_no_features_enabled(self) -> None:
        # Arrange
        config = {
            "secrets": {},
            "features": {
                "train_positions": False,
                "weather_collection": False,
                "station_data": False,
            },
        }

        # Act & Assert
        validate_config(config, required_features=None)

    def test_validate_config_train_positions_requires_cta_key(self) -> None:
        # Arrange
        config = {"secrets": {"cta_api_key": ""}, "features": {}}

        # Act & Assert
        with pytest.raises(
            ValueError, match="CTA_API_KEY \\(required for train_positions\\)"
        ):
            validate_config(config, required_features=["train_positions"])

    def test_validate_config_train_positions_ok(self) -> None:
        # Arrange
        config = {"secrets": {"cta_api_key": "key123"}, "features": {}}

        # Act & Assert
        validate_config(config, required_features=["train_positions"])

    def test_validate_config_weather_collection_missing_nws_app_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.delenv("NWS_APP_NAME", raising=False)
        monkeypatch.delenv("NWS_EMAIL", raising=False)
        config = {"secrets": {}, "features": {}}

        # Act & Assert
        with pytest.raises(
            ValueError, match="NWS_APP_NAME \\(required for weather_collection\\)"
        ):
            validate_config(config, required_features=["weather_collection"])

    def test_validate_config_weather_collection_missing_nws_email(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setenv("NWS_APP_NAME", "app")
        monkeypatch.delenv("NWS_EMAIL", raising=False)
        config = {"secrets": {}, "features": {}}

        # Act & Assert
        with pytest.raises(
            ValueError, match="NWS_EMAIL \\(required for weather_collection\\)"
        ):
            validate_config(config, required_features=["weather_collection"])

    def test_validate_config_weather_collection_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setenv("NWS_APP_NAME", "app")
        monkeypatch.setenv("NWS_EMAIL", "e@x.com")
        config = {"secrets": {}, "features": {}}

        # Act & Assert
        validate_config(config, required_features=["weather_collection"])

    def test_validate_config_station_data_missing_tokens(self) -> None:
        # Arrange
        config = {
            "secrets": {"chidata_app_token": "", "chidata_app_secret": ""},
            "features": {},
        }

        # Act & Assert
        with pytest.raises(ValueError, match="CHIDATA_APP_TOK"):
            validate_config(config, required_features=["station_data"])

    def test_validate_config_station_data_ok(self) -> None:
        # Arrange
        config = {
            "secrets": {"chidata_app_token": "t", "chidata_app_secret": "s"},
            "features": {},
        }

        # Act & Assert
        validate_config(config, required_features=["station_data"])

    def test_validate_config_required_features_from_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange: required_features=None, weather_collection enabled, NWS missing
        monkeypatch.delenv("NWS_APP_NAME", raising=False)
        monkeypatch.delenv("NWS_EMAIL", raising=False)
        config = {
            "secrets": {},
            "features": {
                "weather_collection": True,
                "train_positions": False,
                "station_data": False,
            },
        }

        # Act & Assert
        with pytest.raises(ValueError, match="NWS_APP_NAME"):
            validate_config(config, required_features=None)

    def test_validate_config_required_features_from_enabled_train_positions(
        self,
    ) -> None:
        # Arrange: required_features=None, train_positions enabled, cta_api_key missing
        config = {
            "secrets": {"cta_api_key": ""},
            "features": {
                "train_positions": True,
                "weather_collection": False,
                "station_data": False,
            },
        }

        # Act & Assert
        with pytest.raises(ValueError, match="CTA_API_KEY"):
            validate_config(config, required_features=None)

    def test_validate_config_required_features_from_enabled_station_data(self) -> None:
        # Arrange: required_features=None, station_data enabled, chidata missing
        config = {
            "secrets": {"chidata_app_token": "", "chidata_app_secret": ""},
            "features": {
                "train_positions": False,
                "weather_collection": False,
                "station_data": True,
            },
        }

        # Act & Assert
        with pytest.raises(ValueError, match="CHIDATA_APP_TOK"):
            validate_config(config, required_features=None)

    def test_validate_config_multiple_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.delenv("NWS_APP_NAME", raising=False)
        monkeypatch.delenv("NWS_EMAIL", raising=False)
        config = {
            "secrets": {
                "cta_api_key": "",
                "chidata_app_token": "",
                "chidata_app_secret": "",
            },
            "features": {},
        }

        # Act & Assert
        with pytest.raises(ValueError, match="CTA_API_KEY") as exc_info:
            validate_config(
                config,
                required_features=[
                    "train_positions",
                    "weather_collection",
                    "station_data",
                ],
            )
        msg = str(exc_info.value)
        assert "NWS_APP_NAME" in msg
        assert "NWS_EMAIL" in msg
        assert "CHIDATA_APP_TOK" in msg
        assert "CHIDATA_APP_SECRET" in msg
        assert "Set these environment variables in your .env file" in msg

    def test_validate_config_config_missing_secrets_and_features(self) -> None:
        # Arrange: no "secrets" or "features" keys
        config: dict[str, dict[str, str | int | float | bool]] = {}

        # Act & Assert: required_features=None → derived from features.get(...)=False → []
        validate_config(config, required_features=None)


class TestGetConfigSection:
    """Test cases for get_config_section."""

    def test_get_config_section_uses_load_config_when_config_none(self) -> None:
        # Arrange
        with patch("cta_eta.data_collection.config.load_config") as mock_load:
            mock_load.return_value = {"retry": {"max_retry_attempts": 5}}

            # Act
            result = get_config_section("retry", config=None)

            # Assert
            mock_load.assert_called_once()
            assert result == {"max_retry_attempts": 5}

    def test_get_config_section_with_config_section_exists_dict(self) -> None:
        # Arrange
        config = {"x": {"a": 1, "b": 2}}

        # Act
        result = get_config_section("x", config=config)

        # Assert
        assert result == {"a": 1, "b": 2}

    def test_get_config_section_section_missing_raises(self) -> None:
        # Arrange
        config = {"other": {"k": "v"}}

        # Act & Assert
        with pytest.raises(
            ValueError,
            match=r"Configuration path 'missing': section 'missing' is missing\.",
        ):
            get_config_section("missing", config=config)

    def test_get_config_section_section_not_dict_raises(self) -> None:
        # Arrange
        config = {"x": "not a dict"}

        # Act & Assert
        with pytest.raises(
            TypeError,
            match=r"Configuration path 'x': section 'x' must be a dict, got str\.",
        ):
            get_config_section("x", config=config)

    def test_get_config_section_subsection_two_levels(self) -> None:
        # Arrange
        config = {"rate_limits": {"nws": {"calls_per_minute": 10}}}

        # Act
        result = get_config_section("rate_limits.nws", config=config)

        # Assert
        assert result == {"calls_per_minute": 10}

    def test_get_config_section_subsection_three_levels(self) -> None:
        # Arrange
        config = {"a": {"b": {"c": {"k": 1}}}}

        # Act
        result = get_config_section("a.b.c", config=config)

        # Assert
        assert result == {"k": 1}

    def test_get_config_section_subsection_parent_missing_raises(self) -> None:
        # Arrange
        config: dict[str, dict[str, str | int | float | bool]] = {"other": {}}

        # Act & Assert
        with pytest.raises(
            ValueError,
            match=r"Configuration path 'missing\.child': section 'missing' is missing\.",
        ):
            get_config_section("missing.child", config=config)

    def test_get_config_section_subsection_parent_not_dict_raises(self) -> None:
        # Arrange
        config = {"a": "string"}

        # Act & Assert
        with pytest.raises(
            TypeError,
            match=r"Configuration path 'a\.b': section 'a' must be a dict, got str\.",
        ):
            get_config_section("a.b", config=config)

    def test_get_config_section_subsection_child_missing_raises(self) -> None:
        # Arrange
        config = {"a": {"x": 1}}

        # Act & Assert
        with pytest.raises(
            ValueError,
            match=r"Configuration path 'a\.b': section 'b' is missing\.",
        ):
            get_config_section("a.b", config=config)

    def test_get_config_section_subsection_child_not_dict_raises(self) -> None:
        # Arrange
        config = {"a": {"b": "string"}}

        # Act & Assert
        with pytest.raises(
            TypeError,
            match=r"Configuration path 'a\.b': section 'b' must be a dict, got str\.",
        ):
            get_config_section("a.b", config=config)

    def test_get_config_section_subsection_leading_dot_missing_raises(
        self,
    ) -> None:
        # Arrange: ".a" has no period at index > 0, treated as leaf key ".a"
        config = {"other": {}}

        # Act & Assert
        with pytest.raises(
            ValueError,
            match=r"Configuration path '\.a': section '\.a' is missing\.",
        ):
            get_config_section(".a", config=config)

    def test_get_config_section_subsection_trailing_dot_missing_raises(
        self,
    ) -> None:
        # Arrange: "a." recurses to segment "" in the dict at "a"
        config = {"a": {"x": 1}}

        # Act & Assert
        with pytest.raises(
            ValueError,
            match=r"Configuration path 'a\.': section '' is missing\.",
        ):
            get_config_section("a.", config=config)

    def test_get_config_section_subsection_calls_load_config_when_config_none(
        self,
    ) -> None:
        # Arrange
        with patch("cta_eta.data_collection.config.load_config") as mock_load:
            mock_load.return_value = {"a": {"b": {"v": 1}}}

            # Act
            result = get_config_section("a.b", config=None)

            # Assert
            mock_load.assert_called_once()
            assert result == {"v": 1}

    def test_get_config_section_subsection_middle_not_dict_raises(
        self,
    ) -> None:
        # Arrange
        config = {"a": {"b": "not a dict"}}

        # Act & Assert
        with pytest.raises(
            TypeError,
            match=r"Configuration path 'a\.b\.c': section 'b' must be a dict, got str\.",
        ):
            get_config_section("a.b.c", config=config)

    def test_get_config_section_section_empty_string_missing_raises(
        self,
    ) -> None:
        # Arrange
        config = {"a": 1}

        # Act & Assert
        with pytest.raises(
            ValueError,
            match=r"Configuration path '': section '' is missing\.",
        ):
            get_config_section("", config=config)

    def test_get_config_section_section_empty_string_exists_returns_section(
        self,
    ) -> None:
        # Arrange
        config = {"": {"x": 1}}

        # Act
        result = get_config_section("", config=config)

        # Assert
        assert result == {"x": 1}
