"""Test multi-source weather data merger."""

from __future__ import annotations

from cta_eta.data_collection.merging.weather_merger import merge_weather_sources


class TestMergeWeatherSources:
    """Test cases for merge_weather_sources function."""

    def test_both_sources_present_prefers_nws_for_overlaps(self) -> None:
        """Test that NWS data is preferred over Open-Meteo for overlapping fields."""
        # Arrange
        nws_data = {
            "timestamp": "2026-01-19T12:00",
            "temperature_f": 35.0,
            "wind_speed_mph": 10.0,
        }
        om_data = {
            "timestamp": "2026-01-19T12:00",
            "visibility_m": 10000.0,
            "wind_speed_mph": 11.0,  # Should be ignored in favor of NWS
        }

        # Act
        result = merge_weather_sources(nws_data, om_data)

        # Assert
        assert result["timestamp"] == "2026-01-19T12:00"
        assert result["temperature_f"] == 35.0
        assert result["wind_speed_mph"] == 10.0  # NWS value preferred
        assert result["visibility_m"] == 10000.0

    def test_nws_missing_uses_open_meteo_and_owm(self) -> None:
        """Test fallback to Open-Meteo and OpenWeatherMap when NWS is missing."""
        # Arrange
        nws_data = None
        om_data = {"timestamp": "2026-01-19T12:00", "temperature_f": 34.0}
        owm_data = {"timestamp": "2026-01-19T12:00", "humidity_percent": 75.0}

        # Act
        result = merge_weather_sources(nws_data, om_data, owm_data)

        # Assert
        assert result["timestamp"] == "2026-01-19T12:00"
        assert result["temperature_f"] == 34.0
        assert result["humidity_percent"] == 75.0

    def test_open_meteo_missing_uses_nws_and_owm(self) -> None:
        """Test fallback to NWS and OpenWeatherMap when Open-Meteo is missing."""
        # Arrange
        nws_data = {"timestamp": "2026-01-19T12:00", "temperature_f": 35.0}
        om_data = None
        owm_data = {"timestamp": "2026-01-19T12:00", "snow_depth_m": 0.05}

        # Act
        result = merge_weather_sources(nws_data, om_data, owm_data)

        # Assert
        assert result["timestamp"] == "2026-01-19T12:00"
        assert result["temperature_f"] == 35.0
        assert result["snow_depth_m"] == 0.05

    def test_all_sources_missing_returns_none(self) -> None:
        """Test that None is returned when all sources are missing."""
        # Arrange
        nws_data = None
        om_data = None
        owm_data = None

        # Act
        result = merge_weather_sources(nws_data, om_data, owm_data)

        # Assert
        assert result is None

    def test_preserves_numeric_types(self) -> None:
        """Test that numeric fields remain numeric (float), not strings."""
        # Arrange
        nws_data = {
            "timestamp": "2026-01-19T12:00",
            "temperature_f": 35.0,
            "humidity_percent": 65,
        }
        om_data = {
            "timestamp": "2026-01-19T12:00",
            "visibility_m": 10000.0,
            "snow_depth_m": 0.05,
        }

        # Act
        result = merge_weather_sources(nws_data, om_data)

        # Assert
        assert isinstance(result["temperature_f"], float)
        assert isinstance(result["humidity_percent"], (int, float))
        assert isinstance(result["visibility_m"], float)
        assert isinstance(result["snow_depth_m"], float)
        assert isinstance(result["timestamp"], str)

    def test_all_three_sources_with_correct_precedence(self) -> None:
        """Test merging all three sources with correct precedence (NWS > OM > OWM)."""
        # Arrange
        nws_data = {
            "timestamp": "2026-01-19T12:00",
            "temperature_f": 35.0,
            "wind_speed_mph": 10.0,
        }
        om_data = {
            "timestamp": "2026-01-19T12:00",
            "visibility_m": 10000.0,
            "wind_speed_mph": 11.0,  # Should be ignored
            "humidity_percent": 70.0,
        }
        owm_data = {
            "timestamp": "2026-01-19T12:00",
            "wind_speed_mph": 12.0,  # Should be ignored
            "humidity_percent": 75.0,  # Should be ignored
            "pressure_hpa": 1013.0,
        }

        # Act
        result = merge_weather_sources(nws_data, om_data, owm_data)

        # Assert
        assert result["wind_speed_mph"] == 10.0  # NWS wins
        assert result["humidity_percent"] == 70.0  # Open-Meteo wins over OWM
        assert result["pressure_hpa"] == 1013.0  # OWM unique field
        assert result["visibility_m"] == 10000.0  # Open-Meteo unique field
        assert result["temperature_f"] == 35.0  # NWS unique field

    def test_only_nws_data(self) -> None:
        """Test that only NWS data returns just NWS fields."""
        # Arrange
        nws_data = {
            "timestamp": "2026-01-19T12:00",
            "temperature_f": 35.0,
            "humidity_percent": 65.0,
        }
        om_data = None
        owm_data = None

        # Act
        result = merge_weather_sources(nws_data, om_data, owm_data)

        # Assert
        assert result == nws_data

    def test_only_open_meteo_data(self) -> None:
        """Test that only Open-Meteo data returns just Open-Meteo fields."""
        # Arrange
        nws_data = None
        om_data = {
            "timestamp": "2026-01-19T12:00",
            "visibility_m": 10000.0,
            "snow_depth_m": 0.05,
        }
        owm_data = None

        # Act
        result = merge_weather_sources(nws_data, om_data, owm_data)

        # Assert
        assert result == om_data

    def test_only_owm_data(self) -> None:
        """Test that only OpenWeatherMap data returns just OWM fields."""
        # Arrange
        nws_data = None
        om_data = None
        owm_data = {
            "timestamp": "2026-01-19T12:00",
            "pressure_hpa": 1013.0,
            "humidity_percent": 75.0,
        }

        # Act
        result = merge_weather_sources(nws_data, om_data, owm_data)

        # Assert
        assert result == owm_data

    def test_empty_dict_treated_as_none(self) -> None:
        """Test that empty dictionaries are treated as missing sources."""
        # Arrange
        nws_data = {}
        om_data = {"timestamp": "2026-01-19T12:00", "visibility_m": 10000.0}
        owm_data = {}

        # Act
        result = merge_weather_sources(nws_data, om_data, owm_data)

        # Assert
        # Should only have Open-Meteo data since NWS and OWM are empty
        assert result == om_data
