"""Unit and integration tests for configuration loader.

Tests cover:
- load_config / _load_config_from_path: TOML + .env merge, file missing, invalid TOML.
- _sanitize_config_for_logging: redaction of sensitive keys.
- get_required_credentials: per-feature credential lists, derived from config.
- validate_config_secrets: required credentials for enabled features (uses get_required_credentials).
- validate_config_file_settings: station_data, train_positions, weather_collection, weather_collection_fallback.
- validate_config: full validation (secrets + file settings).
- get_config_section: top-level and dotted paths, missing/wrong types.

Fixtures use tmp_path for atomicity; env and load_config are mocked where needed.
"""

from __future__ import annotations

import importlib
import tomllib
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

import cta_eta.data_collection.config as cta_config
from cta_eta.data_collection.config import (
    _load_config_from_path,
    _sanitize_config_for_logging,
    get_config_section,
    get_required_credentials,
    load_config,
    validate_config,
    validate_config_file_settings,
    validate_config_secrets,
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

[storage.immediate]
data_path = "data/journals"
journal_rotation_minutes = 15
partition_hour = 3

[storage.compaction]
backend = "local"
staging_path = "data/compaction"
upload_prefix = "raw"
archive_path = "data/archive"
journal_retention_days = 7
"""
    config_file.write_text(config_content)
    return config_file


def _make_valid_full_config() -> dict[str, dict[str, Any]]:
    """Build a config dict that passes all file-settings and secrets validations.

    Uses the same shape as load_config() + config.toml so validate_config_file_settings
    and get_required_credentials paths are exercised with real structures.
    """
    return {
        "features": {
            "train_positions": True,
            "weather_collection": True,
            "weather_collection_fallback": True,
            "station_data": True,
        },
        "secrets": {
            "cta_api_key": "cta_key",
            "chidata_app_token": "chi_tok",
            "chidata_app_secret": "chi_sec",
            "openweathermap_api_key": "owm_key",
        },
        "cache": {
            "stations_ttl": 604800,
            "track_geometry_ttl": 2592000,
            "weather_mapping_ttl": 604800,
        },
        "collection": {
            "train_interval_seconds": 15,
            "rate_limits": {
                "cta": {"max_per_second": 0.1, "max_at_once": 3},
            },
        },
        "rate_limits": {
            "nws": {"max_per_second": 5, "max_at_once": 5},
            "openweathermap": {"max_per_second": 60, "max_at_once": 60},
        },
        "storage": {
            "immediate": {
                "data_path": "data/journals",
                "journal_rotation_minutes": 15,
                "partition_hour": 3,
            },
            "compaction": {
                "backend": "local",
                "staging_path": "data/compaction",
                "upload_prefix": "raw",
                "archive_path": "data/archive",
                "journal_retention_days": 7,
            },
        },
    }


@pytest.fixture
def valid_full_config() -> dict[str, dict[str, Any]]:
    """Config dict that passes validate_config for all features enabled."""
    return _make_valid_full_config()


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
        config: dict[str, object] = {
            "str_section": "string_val",
            "int_section": 42,
        }

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

    def test_sanitize_redacts_storage_keys(self) -> None:
        # Arrange: bucket names under storage.compaction are redacted in logs
        config = {
            "storage": {
                "immediate": {"data_path": "data/journals"},
                "compaction": {
                    "s3_bucket": "my-bucket",
                    "gcs_bucket": "other",
                    "backend": "local",
                    "upload_prefix": "raw",
                    "archive_path": "data/archive",
                },
            },
        }

        # Act
        out = _sanitize_config_for_logging(config)

        # Assert
        assert out["storage"]["compaction"]["s3_bucket"] == "<REDACTED>"
        assert out["storage"]["compaction"]["gcs_bucket"] == "<REDACTED>"
        assert out["storage"]["compaction"]["backend"] == "local"
        assert out["storage"]["compaction"]["upload_prefix"] == "raw"
        assert out["storage"]["compaction"]["archive_path"] == "data/archive"


class TestLoadConfigFromPath:
    """Test cases for _load_config_from_path function (internal, testable API)."""

    def test_load_and_validate_full_config_integration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Integration: load TOML with all file-setting sections, merge env, then validate_config."""
        # Arrange: minimal TOML that satisfies all file-setting validators
        toml_content = """
[features]
train_positions = true
weather_collection = true
weather_collection_fallback = true
station_data = true

[cache]
stations_ttl = 604800
track_geometry_ttl = 2592000
weather_mapping_ttl = 604800

[collection]
train_interval_seconds = 15
[collection.rate_limits.cta]
max_per_second = 0.1
max_at_once = 3

[rate_limits.nws]
max_per_second = 5
max_at_once = 5

[rate_limits.openweathermap]
max_per_second = 60
max_at_once = 60

[storage.immediate]
data_path = "data/journals"
journal_rotation_minutes = 15
partition_hour = 3

[storage.compaction]
backend = "local"
staging_path = "data/compaction"
upload_prefix = "raw"
archive_path = "data/archive"
journal_retention_days = 7
"""
        config_file = tmp_path / "config.toml"
        config_file.write_text(toml_content)
        monkeypatch.setenv("CTA_API_KEY", "cta_key")
        monkeypatch.setenv("CHIDATA_APP_TOK", "chi_tok")
        monkeypatch.setenv("CHIDATA_APP_SECRET", "chi_sec")
        monkeypatch.setenv("OPENWEATHERMAP_API_KEY", "owm_key")
        monkeypatch.setenv("NWS_APP_NAME", "nws_app")
        monkeypatch.setenv("NWS_EMAIL", "nws@example.com")

        # Act: use real loader and full validator
        with patch("cta_eta.data_collection.config.load_dotenv"):
            config = _load_config_from_path(config_file)
        validate_config(config, required_features=None)

        # Assert: no exception; config has expected shape used by get_required_credentials
        creds = get_required_credentials(config, required_features=None)
        assert "cta_api_key" in creds
        assert "openweathermap_api_key" in creds
        assert config["cache"]["stations_ttl"] == 604800
        assert config["collection"]["rate_limits"]["cta"]["max_per_second"] == 0.1  # ty:ignore[invalid-argument-type, not-subscriptable]

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

    def test_load_config_injects_storage_from_env(
        self, temp_config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Storage bucket names and S3 endpoint URL are injected from env."""
        monkeypatch.setenv("S3_BUCKET", "my-s3-bucket")
        monkeypatch.setenv("GCS_BUCKET", "my-gcs-bucket")
        monkeypatch.setenv("AZURE_BUCKET", "my-azure-container")
        monkeypatch.setenv("S3_ENDPOINT_URL", "https://minio.example.com")
        with patch("cta_eta.data_collection.config.load_dotenv"):
            config = _load_config_from_path(temp_config_file)

        assert config["storage"]["compaction"]["s3_bucket"] == "my-s3-bucket"
        assert config["storage"]["compaction"]["gcs_bucket"] == "my-gcs-bucket"
        assert config["storage"]["compaction"]["azure_bucket"] == "my-azure-container"
        assert (
            config["storage"]["compaction"]["s3_endpoint_url"]
            == "https://minio.example.com"
        )

    def test_load_config_storage_empty_when_env_unset(
        self, temp_config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When bucket and S3 endpoint env vars are unset, config gets empty strings."""
        monkeypatch.delenv("S3_BUCKET", raising=False)
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        monkeypatch.delenv("AZURE_BUCKET", raising=False)
        monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
        with patch("cta_eta.data_collection.config.load_dotenv"):
            config = _load_config_from_path(temp_config_file)

        assert config["storage"]["compaction"]["s3_bucket"] == ""
        assert config["storage"]["compaction"]["gcs_bucket"] == ""
        assert config["storage"]["compaction"]["azure_bucket"] == ""
        assert config["storage"]["compaction"]["s3_endpoint_url"] == ""


class TestGetRequiredCredentials:
    """Test cases for get_required_credentials (integration: uses config shape)."""

    def test_no_features_returns_empty(self) -> None:
        # Arrange
        config = {"features": {}}

        # Act
        result = get_required_credentials(config, required_features=[])

        # Assert
        assert result == []

    def test_required_features_none_derives_from_enabled(self) -> None:
        # Arrange
        config = {
            "features": {
                "train_positions": True,
                "weather_collection": False,
                "station_data": False,
                "weather_collection_fallback": False,
            },
        }

        # Act
        result = get_required_credentials(config, required_features=None)

        # Assert
        assert "cta_api_key" in result
        assert "nws_app_name" not in result
        assert "chidata_app_token" not in result
        assert "openweathermap_api_key" not in result

    def test_station_data_returns_chidata_creds(self) -> None:
        # Arrange
        config = {"features": {}}

        # Act
        result = get_required_credentials(config, required_features=["station_data"])

        # Assert
        assert "chidata_app_token" in result
        assert "chidata_app_secret" in result

    def test_train_positions_returns_cta_key(self) -> None:
        # Arrange
        config = {"features": {}}

        # Act
        result = get_required_credentials(config, required_features=["train_positions"])

        # Assert
        assert result == ["cta_api_key"]

    def test_weather_collection_returns_nws_creds(self) -> None:
        # Arrange
        config = {"features": {}}

        # Act
        result = get_required_credentials(
            config, required_features=["weather_collection"]
        )

        # Assert
        assert "nws_app_name" in result
        assert "nws_email" in result

    def test_weather_collection_fallback_returns_openweathermap_key(self) -> None:
        # Arrange
        config = {"features": {}}

        # Act
        result = get_required_credentials(
            config, required_features=["weather_collection_fallback"]
        )

        # Assert
        assert result == ["openweathermap_api_key"]

    def test_multiple_features_returns_union(self) -> None:
        # Arrange
        config = {"features": {}}

        # Act
        result = get_required_credentials(
            config,
            required_features=[
                "train_positions",
                "station_data",
                "weather_collection_fallback",
            ],
        )

        # Assert
        assert "cta_api_key" in result
        assert "chidata_app_token" in result
        assert "chidata_app_secret" in result
        assert "openweathermap_api_key" in result


class TestValidateConfigSecrets:
    """Test cases for validate_config_secrets (credentials by feature)."""

    def test_no_features_enabled(self) -> None:
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
        validate_config_secrets(config, required_features=None)

    def test_train_positions_requires_cta_key(self) -> None:
        # Arrange
        config = {"secrets": {"cta_api_key": ""}, "features": {}}

        # Act & Assert
        with pytest.raises(
            ValueError, match="CTA_API_KEY \\(required for train_positions\\)"
        ):
            validate_config_secrets(config, required_features=["train_positions"])

    def test_train_positions_ok(self) -> None:
        # Arrange
        config = {"secrets": {"cta_api_key": "key123"}, "features": {}}

        # Act & Assert
        validate_config_secrets(config, required_features=["train_positions"])

    def test_weather_collection_missing_nws_app_name(
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
            validate_config_secrets(config, required_features=["weather_collection"])

    def test_weather_collection_missing_nws_email(
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
            validate_config_secrets(config, required_features=["weather_collection"])

    def test_weather_collection_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange
        monkeypatch.setenv("NWS_APP_NAME", "app")
        monkeypatch.setenv("NWS_EMAIL", "e@x.com")
        config = {"secrets": {}, "features": {}}

        # Act & Assert
        validate_config_secrets(config, required_features=["weather_collection"])

    def test_weather_collection_fallback_ok(self) -> None:
        # Arrange
        config = {"secrets": {"openweathermap_api_key": "owm_key"}, "features": {}}

        # Act & Assert
        validate_config_secrets(
            config, required_features=["weather_collection_fallback"]
        )

    def test_station_data_missing_tokens(self) -> None:
        # Arrange
        config = {
            "secrets": {"chidata_app_token": "", "chidata_app_secret": ""},
            "features": {},
        }

        # Act & Assert
        with pytest.raises(ValueError, match="CHIDATA_APP_TOK"):
            validate_config_secrets(config, required_features=["station_data"])

    def test_station_data_ok(self) -> None:
        # Arrange
        config = {
            "secrets": {"chidata_app_token": "t", "chidata_app_secret": "s"},
            "features": {},
        }

        # Act & Assert
        validate_config_secrets(config, required_features=["station_data"])

    def test_required_features_from_enabled(
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
            validate_config_secrets(config, required_features=None)

    def test_required_features_from_enabled_train_positions(self) -> None:
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
            validate_config_secrets(config, required_features=None)

    def test_required_features_from_enabled_station_data(self) -> None:
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
            validate_config_secrets(config, required_features=None)

    def test_multiple_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
            validate_config_secrets(
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

    def test_config_missing_secrets_and_features(self) -> None:
        # Arrange: no "secrets" or "features" keys
        config: dict[str, dict[str, str | int | float | bool]] = {}

        # Act & Assert: required_features=None → derived from features.get(...)=False → []
        validate_config_secrets(config, required_features=None)


class TestValidateConfigFileSettings:
    """Test cases for validate_config_file_settings and file-setting validators."""

    def test_valid_full_config_passes_all_features(
        self, valid_full_config: dict[str, dict[str, Any]]
    ) -> None:
        # Arrange
        config = valid_full_config

        # Act & Assert
        validate_config_file_settings(
            config,
            required_features=[
                "station_data",
                "train_positions",
                "weather_collection",
                "weather_collection_fallback",
            ],
        )

    def test_required_features_none_derives_from_config(
        self, valid_full_config: dict[str, dict[str, Any]]
    ) -> None:
        # Arrange: valid_full_config has all features True
        config = valid_full_config

        # Act & Assert
        validate_config_file_settings(config, required_features=None)

    def test_empty_required_features_skips_validation(self) -> None:
        # Arrange: minimal config that would fail station_data/train_positions/etc.
        config = {"features": {}}

        # Act & Assert
        validate_config_file_settings(config, required_features=[])

    def test_station_data_missing_cache_section(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        del config["cache"]

        # Act & Assert
        with pytest.raises(ValueError, match="stations_ttl"):
            validate_config_file_settings(config, required_features=["station_data"])

    def test_station_data_cache_not_dict(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        config["cache"] = "not a dict"  # ty:ignore[invalid-assignment]

        # Act & Assert
        with pytest.raises(TypeError, match=r"cache.*must be a dictionary"):
            validate_config_file_settings(config, required_features=["station_data"])

    def test_station_data_missing_stations_ttl(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        del config["cache"]["stations_ttl"]

        # Act & Assert
        with pytest.raises(ValueError, match="stations_ttl"):
            validate_config_file_settings(config, required_features=["station_data"])

    def test_station_data_stations_ttl_not_int(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        config["cache"]["stations_ttl"] = "3600"

        # Act & Assert
        with pytest.raises(ValueError, match=r"stations_ttl.*integer"):
            validate_config_file_settings(config, required_features=["station_data"])

    def test_station_data_missing_track_geometry_ttl(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        del config["cache"]["track_geometry_ttl"]

        # Act & Assert
        with pytest.raises(ValueError, match=r"track_geometry_ttl"):
            validate_config_file_settings(config, required_features=["station_data"])

    def test_station_data_missing_weather_mapping_ttl(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        del config["cache"]["weather_mapping_ttl"]

        # Act & Assert
        with pytest.raises(ValueError, match=r"weather_mapping_ttl"):
            validate_config_file_settings(config, required_features=["station_data"])

    def test_train_positions_missing_collection(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        del config["collection"]

        # Act & Assert
        with pytest.raises(ValueError, match=r"train_interval_seconds"):
            validate_config_file_settings(config, required_features=["train_positions"])

    def test_train_positions_collection_not_dict(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        config["collection"] = "not a dict"  # ty:ignore[invalid-assignment]

        # Act & Assert
        with pytest.raises(TypeError, match=r"collection.*must be a dictionary"):
            validate_config_file_settings(config, required_features=["train_positions"])

    def test_train_positions_missing_train_interval_seconds(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        del config["collection"]["train_interval_seconds"]

        # Act & Assert
        with pytest.raises(ValueError, match=r"train_interval_seconds"):
            validate_config_file_settings(config, required_features=["train_positions"])

    def test_train_positions_train_interval_not_int(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        config["collection"]["train_interval_seconds"] = 15.5

        # Act & Assert
        with pytest.raises(ValueError, match=r"train_interval_seconds.*integer"):
            validate_config_file_settings(config, required_features=["train_positions"])

    def test_train_positions_missing_rate_limits(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        del config["collection"]["rate_limits"]

        # Act & Assert
        with pytest.raises(ValueError, match=r"rate_limits"):
            validate_config_file_settings(config, required_features=["train_positions"])

    def test_train_positions_rate_limits_not_dict(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        config["collection"]["rate_limits"] = []

        # Act & Assert
        with pytest.raises(ValueError, match=r"rate_limits.*dictionary"):
            validate_config_file_settings(config, required_features=["train_positions"])

    def test_train_positions_missing_cta_in_rate_limits(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        config["collection"]["rate_limits"] = {"other": {}}

        # Act & Assert
        with pytest.raises(ValueError, match=r"rate_limits.cta"):
            validate_config_file_settings(config, required_features=["train_positions"])

    def test_train_positions_cta_max_per_second_not_float(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        config["collection"]["rate_limits"]["cta"]["max_per_second"] = "0.1"

        # Act & Assert
        with pytest.raises(ValueError, match=r"max_per_second.*float"):
            validate_config_file_settings(config, required_features=["train_positions"])

    def test_train_positions_cta_max_at_once_not_int(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        config["collection"]["rate_limits"]["cta"]["max_at_once"] = 3.0

        # Act & Assert
        with pytest.raises(ValueError, match=r"max_at_once.*integer"):
            validate_config_file_settings(config, required_features=["train_positions"])

    def test_weather_collection_missing_rate_limits(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        del config["rate_limits"]

        # Act & Assert
        with pytest.raises(ValueError, match="nws"):
            validate_config_file_settings(
                config, required_features=["weather_collection"]
            )

    def test_weather_collection_rate_limits_not_dict(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        config["rate_limits"] = []  # ty:ignore[invalid-assignment]

        # Act & Assert
        with pytest.raises(TypeError, match=r"rate_limits.*must be a dictionary"):
            validate_config_file_settings(
                config, required_features=["weather_collection"]
            )

    def test_weather_collection_missing_nws(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        config["rate_limits"] = {
            "openweathermap": {"max_per_second": 60, "max_at_once": 60}
        }

        # Act & Assert
        with pytest.raises(ValueError, match=r"nws"):
            validate_config_file_settings(
                config, required_features=["weather_collection"]
            )

    def test_weather_collection_nws_max_per_second_int_allowed(self) -> None:
        # Arrange: NWS validator allows int or float for max_per_second
        config = _make_valid_full_config()
        config["rate_limits"]["nws"]["max_per_second"] = 5

        # Act & Assert
        validate_config_file_settings(config, required_features=["weather_collection"])

    def test_weather_collection_nws_max_per_second_missing(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        del config["rate_limits"]["nws"]["max_per_second"]

        # Act & Assert
        with pytest.raises(ValueError, match=r"nws.max_per_second"):
            validate_config_file_settings(
                config, required_features=["weather_collection"]
            )

    def test_weather_collection_nws_max_per_second_not_numeric(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        config["rate_limits"]["nws"]["max_per_second"] = "5"

        # Act & Assert
        with pytest.raises(ValueError, match=r"nws.max_per_second"):
            validate_config_file_settings(
                config, required_features=["weather_collection"]
            )

    def test_weather_collection_nws_max_at_once_not_int(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        config["rate_limits"]["nws"]["max_at_once"] = 5.5

        # Act & Assert
        with pytest.raises(ValueError, match=r"nws.max_at_once.*integer"):
            validate_config_file_settings(
                config, required_features=["weather_collection"]
            )

    def test_weather_collection_fallback_rate_limits_not_dict(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        config["rate_limits"] = []  # ty:ignore[invalid-assignment]

        # Act & Assert
        with pytest.raises(TypeError, match=r"rate_limits.*must be a dictionary"):
            validate_config_file_settings(
                config, required_features=["weather_collection_fallback"]
            )

    def test_weather_collection_fallback_missing_openweathermap(self) -> None:
        # Arrange
        config = _make_valid_full_config()
        config["rate_limits"] = {"nws": {"max_per_second": 5, "max_at_once": 5}}

        # Act & Assert
        with pytest.raises(ValueError, match=r"openweathermap"):
            validate_config_file_settings(
                config, required_features=["weather_collection_fallback"]
            )

    def test_weather_collection_fallback_openweathermap_max_per_second_missing(
        self,
    ) -> None:
        # Arrange
        config = _make_valid_full_config()
        del config["rate_limits"]["openweathermap"]["max_per_second"]

        # Act & Assert
        with pytest.raises(ValueError, match=r"openweathermap\.max_per_second"):
            validate_config_file_settings(
                config, required_features=["weather_collection_fallback"]
            )

    def test_weather_collection_fallback_openweathermap_max_per_second_not_numeric(
        self,
    ) -> None:
        # Arrange
        config = _make_valid_full_config()
        config["rate_limits"]["openweathermap"]["max_per_second"] = "60"

        # Act & Assert
        with pytest.raises(ValueError, match=r"openweathermap\.max_per_second"):
            validate_config_file_settings(
                config, required_features=["weather_collection_fallback"]
            )

    def test_weather_collection_fallback_openweathermap_max_at_once_not_int(
        self,
    ) -> None:
        # Arrange
        config = _make_valid_full_config()
        config["rate_limits"]["openweathermap"]["max_at_once"] = 60.0

        # Act & Assert
        with pytest.raises(ValueError, match=r"openweathermap\.max_at_once"):
            validate_config_file_settings(
                config, required_features=["weather_collection_fallback"]
            )


class TestValidateConfigFull:
    """Test cases for full validate_config (secrets + file settings)."""

    def test_valid_full_config_passes(
        self,
        valid_full_config: dict[str, dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Arrange
        monkeypatch.setenv("NWS_APP_NAME", "nws_app")
        monkeypatch.setenv("NWS_EMAIL", "nws@example.com")
        config = valid_full_config

        # Act & Assert
        validate_config(config, required_features=None)

    def test_fails_on_missing_secrets(
        self,
        valid_full_config: dict[str, dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Arrange: file settings valid, secrets missing
        monkeypatch.delenv("NWS_APP_NAME", raising=False)
        monkeypatch.delenv("NWS_EMAIL", raising=False)
        config = valid_full_config
        config["secrets"]["cta_api_key"] = ""
        config["secrets"]["openweathermap_api_key"] = ""
        config["features"] = {
            "train_positions": True,
            "weather_collection": True,
            "weather_collection_fallback": True,
            "station_data": True,
        }

        # Act & Assert
        with pytest.raises(ValueError, match="Missing required credentials"):
            validate_config(config, required_features=None)

    def test_fails_on_invalid_file_settings(
        self,
        valid_full_config: dict[str, dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Arrange: secrets valid, file settings invalid (missing cache key)
        monkeypatch.setenv("NWS_APP_NAME", "nws_app")
        monkeypatch.setenv("NWS_EMAIL", "nws@example.com")
        config = valid_full_config
        del config["cache"]["stations_ttl"]
        config["features"] = {
            "station_data": True,
            "train_positions": False,
            "weather_collection": False,
            "weather_collection_fallback": False,
        }

        # Act & Assert
        with pytest.raises(ValueError, match="stations_ttl"):
            validate_config(config, required_features=None)

    def test_secrets_validated_first_when_both_wrong(
        self,
        valid_full_config: dict[str, dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Arrange: missing secrets and invalid file settings; secrets run first
        monkeypatch.delenv("NWS_APP_NAME", raising=False)
        monkeypatch.delenv("NWS_EMAIL", raising=False)
        config = valid_full_config
        config["secrets"]["cta_api_key"] = ""
        del config["cache"]["stations_ttl"]
        config["features"] = {
            "train_positions": True,
            "station_data": True,
            "weather_collection": True,
            "weather_collection_fallback": True,
        }

        # Act & Assert: validate_config calls validate_config_secrets first
        with pytest.raises(ValueError, match="Missing required credentials"):
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
            get_config_section("x", config=config)  # ty:ignore[invalid-argument-type]

    def test_get_config_section_subsection_two_levels(self) -> None:
        # Arrange
        config = {"rate_limits": {"nws": {"calls_per_minute": 10}}}

        # Act
        result = get_config_section("rate_limits.nws", config=config)  # ty:ignore[invalid-argument-type]

        # Assert
        assert result == {"calls_per_minute": 10}

    def test_get_config_section_subsection_three_levels(self) -> None:
        # Arrange
        config = {"a": {"b": {"c": {"k": 1}}}}

        # Act
        result = get_config_section("a.b.c", config=config)  # ty:ignore[invalid-argument-type]

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
            get_config_section("a.b", config=config)  # ty:ignore[invalid-argument-type]

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
            get_config_section("", config=config)  # ty:ignore[invalid-argument-type]

    def test_get_config_section_section_empty_string_exists_returns_section(
        self,
    ) -> None:
        # Arrange
        config = {"": {"x": 1}}

        # Act
        result = get_config_section("", config=config)

        # Assert
        assert result == {"x": 1}
