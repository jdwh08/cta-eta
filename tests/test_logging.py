"""Unit tests for logging utilities."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from cta_eta.logging import (
    HumanReadableFormatter,
    JSONFormatter,
    get_logger,
    log_api_call,
    log_context,
    setup_logger,
)

if TYPE_CHECKING:
    from collections.abc import Generator

# Constants for test values
_ERROR_CODE_500 = 500
_MIN_EXPECTED_CALLS = 2


@pytest.fixture
def reset_logging() -> Generator[None]:
    """Reset logging state between tests."""
    # Remove all handlers from root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.WARNING)

    # Clear all loggers
    logging.Logger.manager.loggerDict.clear()

    yield

    # Cleanup after test
    root_logger.handlers.clear()
    logging.Logger.manager.loggerDict.clear()


@pytest.mark.usefixtures("reset_logging")
class TestJSONFormatter:
    """Test cases for JSONFormatter."""

    def test_format_basic_record(self) -> None:
        """Test formatting a basic log record."""
        # Arrange
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        record.created = time.time()

        # Act
        result = formatter.format(record)

        # Assert
        data = json.loads(result)
        assert data["level"] == "INFO"
        assert data["logger"] == "test_logger"
        assert data["message"] == "Test message"
        assert "timestamp" in data
        assert data["timestamp"].endswith("Z")

    def test_format_with_extra_fields(self) -> None:
        """Test formatting with extra_fields attribute."""
        # Arrange
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=None,
        )
        record.created = time.time()
        record.extra_fields = {
            "error_type": "ValueError",
            "error_code": _ERROR_CODE_500,
        }

        # Act
        result = formatter.format(record)

        # Assert
        data = json.loads(result)
        assert data["error_type"] == "ValueError"
        assert data["error_code"] == _ERROR_CODE_500
        assert data["level"] == "ERROR"

    def test_format_timestamp_format(self) -> None:
        """Test that timestamp is in ISO 8601 format with milliseconds."""
        # Arrange
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        record.created = 1609459200.123  # Fixed timestamp

        # Act
        result = formatter.format(record)

        # Assert
        data = json.loads(result)
        assert "T" in data["timestamp"]
        assert data["timestamp"].endswith("Z")
        assert "." in data["timestamp"]

    def test_format_all_log_levels(self) -> None:
        """Test formatting for all log levels."""
        # Arrange
        formatter = JSONFormatter()
        levels = [
            logging.DEBUG,
            logging.INFO,
            logging.WARNING,
            logging.ERROR,
            logging.CRITICAL,
        ]

        for level in levels:
            record = logging.LogRecord(
                name="test_logger",
                level=level,
                pathname="test.py",
                lineno=1,
                msg=f"Level {level}",
                args=(),
                exc_info=None,
            )
            record.created = time.time()

            # Act
            result = formatter.format(record)

            # Assert
            data = json.loads(result)
            assert data["level"] == logging.getLevelName(level)


@pytest.mark.usefixtures("reset_logging")
class TestHumanReadableFormatter:
    """Test cases for HumanReadableFormatter."""

    def test_format_basic_record(self) -> None:
        """Test formatting a basic log record."""
        # Arrange
        formatter = HumanReadableFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        record.created = time.time()

        # Act
        result = formatter.format(record)

        # Assert
        assert "INFO" in result
        assert "test_logger" in result
        assert "Test message" in result

    def test_format_with_extra_fields(self) -> None:
        """Test formatting with extra_fields attribute."""
        # Arrange
        formatter = HumanReadableFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="Warning message",
            args=(),
            exc_info=None,
        )
        record.created = time.time()
        record.extra_fields = {"key": "value", "count": 42}

        # Act
        result = formatter.format(record)

        # Assert
        assert "WARNING" in result
        assert "key" in result or "value" in result
        assert "42" in result


@pytest.mark.usefixtures("reset_logging")
class TestSetupLogger:
    """Test cases for setup_logger function."""

    def test_setup_logger_creates_logger(self) -> None:
        """Test that setup_logger creates a logger instance."""
        # Arrange & Act
        logger = setup_logger("test_module")

        # Assert
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test_module"

    def test_setup_logger_sets_level(self) -> None:
        """Test that setup_logger sets the correct log level."""
        # Arrange & Act
        logger = setup_logger("test_module", log_level="DEBUG")

        # Assert
        assert logger.level == logging.DEBUG

    def test_setup_logger_json_format(self) -> None:
        """Test that setup_logger uses JSON formatter when requested."""
        # Arrange & Act
        logger = setup_logger("test_module", json_format=True)

        # Assert
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0].formatter, JSONFormatter)

    def test_setup_logger_human_readable_format(self) -> None:
        """Test that setup_logger uses human-readable formatter when requested."""
        # Arrange & Act
        logger = setup_logger("test_module", json_format=False)

        # Assert
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0].formatter, HumanReadableFormatter)

    def test_setup_logger_removes_existing_handlers(self) -> None:
        """Test that setup_logger removes existing handlers to avoid duplicates."""
        # Arrange
        logger = setup_logger("test_module")
        initial_handler_count = len(logger.handlers)

        # Act
        logger2 = setup_logger("test_module")

        # Assert
        assert logger is logger2
        assert len(logger.handlers) == 1
        assert len(logger.handlers) == initial_handler_count

    def test_setup_logger_all_levels(self) -> None:
        """Test setup_logger with all valid log levels."""
        # Arrange
        levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

        for level in levels:
            # Act
            logger = setup_logger(f"test_{level}", log_level=level)

            # Assert
            assert logger.level == getattr(logging, level)


@pytest.mark.usefixtures("reset_logging")
class TestGetLogger:
    """Test cases for get_logger function."""

    def test_get_logger_returns_logger(self) -> None:
        """Test that get_logger returns a logger instance."""
        # Arrange & Act
        logger = get_logger("test_module")

        # Assert
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test_module"

    def test_get_logger_returns_same_instance(self) -> None:
        """Test that get_logger returns the same instance for the same name."""
        # Arrange & Act
        logger1 = get_logger("test_module")
        logger2 = get_logger("test_module")

        # Assert
        assert logger1 is logger2


@pytest.mark.usefixtures("reset_logging")
class TestLogContext:
    """Test cases for log_context context manager."""

    def test_log_context_adds_fields(self) -> None:
        """Test that log_context adds fields to log records."""
        # Arrange
        setup_logger("test_module", json_format=True)
        formatter = JSONFormatter()

        # Act
        with log_context(request_id="abc123", user_id="user456"):
            record = logging.LogRecord(
                name="test_module",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="Test message",
                args=(),
                exc_info=None,
            )
            record.created = time.time()
            result = formatter.format(record)

        # Assert
        data = json.loads(result)
        assert data["request_id"] == "abc123"
        assert data["user_id"] == "user456"

    def test_log_context_merges_with_existing(self) -> None:
        """Test that log_context merges with existing context."""
        # Arrange
        setup_logger("test_module", json_format=True)
        formatter = JSONFormatter()

        # Act
        with log_context(base="value1"), log_context(additional="value2"):
            record = logging.LogRecord(
                name="test_module",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="Test",
                args=(),
                exc_info=None,
            )
            record.created = time.time()
            result = formatter.format(record)

        # Assert
        data = json.loads(result)
        assert data["base"] == "value1"
        assert data["additional"] == "value2"

    def test_log_context_resets_after_exit(self) -> None:
        """Test that log_context resets after exiting."""
        # Arrange
        setup_logger("test_module", json_format=True)
        formatter = JSONFormatter()

        # Act
        with log_context(temp="value"):
            pass

        record = logging.LogRecord(
            name="test_module",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        record.created = time.time()
        result = formatter.format(record)

        # Assert
        data = json.loads(result)
        assert "temp" not in data

    def test_log_context_empty(self) -> None:
        """Test that log_context works with no arguments."""
        # Arrange
        setup_logger("test_module", json_format=True)
        formatter = JSONFormatter()

        # Act
        with log_context():
            record = logging.LogRecord(
                name="test_module",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="Test",
                args=(),
                exc_info=None,
            )
            record.created = time.time()
            result = formatter.format(record)

        # Assert
        data = json.loads(result)
        assert "level" in data
        assert "message" in data


@pytest.mark.usefixtures("reset_logging")
class TestLogApiCall:
    """Test cases for log_api_call decorator."""

    def test_log_api_call_logs_start(self) -> None:
        """Test that log_api_call logs API call start."""
        # Arrange
        logger = setup_logger("test_module")
        logger.info = MagicMock(spec=logging.Logger.info)  # pyright: ignore[assignment]

        @log_api_call(logger)
        def test_function() -> str:
            return "result"

        # Act
        test_function()

        # Assert
        assert logger.info.call_count >= 1  # pyright: ignore[attr-defined]
        start_call = logger.info.call_args_list[0]  # pyright: ignore[attr-defined]
        assert "API call started" in str(start_call)
        assert "test_function" in str(start_call)

    def test_log_api_call_logs_success(self) -> None:
        """Test that log_api_call logs successful API call."""
        # Arrange
        logger = setup_logger("test_module")
        logger.info = MagicMock(spec=logging.Logger.info)  # pyright: ignore[assignment]

        @log_api_call(logger)
        def test_function() -> str:
            return "result"

        # Act
        result = test_function()

        # Assert
        assert result == "result"
        assert logger.info.call_count >= _MIN_EXPECTED_CALLS  # pyright: ignore[attr-defined]
        success_calls = [
            call
            for call in logger.info.call_args_list
            if "completed" in str(call)  # pyright: ignore[attr-defined]
        ]
        assert len(success_calls) > 0

    def test_log_api_call_logs_timing(self) -> None:
        """Test that log_api_call logs response time."""
        # Arrange
        logger = setup_logger("test_module")
        logger.info = MagicMock(spec=logging.Logger.info)  # pyright: ignore[assignment]

        @log_api_call(logger)
        def slow_function() -> str:
            time.sleep(0.1)
            return "result"

        # Act
        slow_function()

        # Assert
        success_calls = [
            call
            for call in logger.info.call_args_list
            if "completed" in str(call)  # pyright: ignore[attr-defined]
        ]
        assert len(success_calls) > 0
        call_kwargs = success_calls[0][1]
        assert "response_time_ms" in str(call_kwargs)

    def test_log_api_call_logs_error(self) -> None:
        """Test that log_api_call logs errors."""
        # Arrange
        logger = setup_logger("test_module")
        logger.info = MagicMock(spec=logging.Logger.info)  # pyright: ignore[assignment]
        logger.error = MagicMock(spec=logging.Logger.error)  # pyright: ignore[assignment]

        error_msg = "Test error"

        @log_api_call(logger)
        def failing_function() -> None:
            raise ValueError(error_msg)

        # Act & Assert
        with pytest.raises(ValueError, match=error_msg):
            failing_function()

        # Assert error was logged
        logger.error.assert_called()  # pyright: ignore[attr-defined]
        error_call = logger.error.call_args  # pyright: ignore[attr-defined]
        assert "API call failed" in str(error_call)
        assert error_msg in str(error_call)

    def test_log_api_call_preserves_function_signature(self) -> None:
        """Test that log_api_call preserves function signature."""
        # Arrange
        logger = setup_logger("test_module")

        @log_api_call(logger)
        def test_function(arg1: str, arg2: int = 42) -> str:
            return f"{arg1}_{arg2}"

        # Act
        result = test_function("test", arg2=100)

        # Assert
        assert result == "test_100"

    def test_log_api_call_with_args_and_kwargs(self) -> None:
        """Test that log_api_call logs function arguments."""
        # Arrange
        logger = setup_logger("test_module")
        logger.info = MagicMock(spec=logging.Logger.info)  # pyright: ignore[assignment]

        @log_api_call(logger)
        def test_function(arg1: str, arg2: str, kwarg1: str | None = None) -> str:
            return f"{arg1}_{arg2}_{kwarg1}"

        # Act
        test_function("a", "b", kwarg1="c")

        # Assert
        start_call = logger.info.call_args_list[0]  # pyright: ignore[attr-defined]
        assert "args" in str(start_call) or "kwargs" in str(start_call)

    def test_log_api_call_handles_missing_name(self) -> None:
        """Test that log_api_call handles functions without __name__ attribute."""
        # Arrange
        logger = setup_logger("test_module")

        # Create a callable without __name__
        class CallableWithoutName:
            def __call__(self) -> str:
                return "result"

        callable_obj = CallableWithoutName()

        # Act & Assert
        with pytest.raises(ValueError, match="__name__ attribute"):
            log_api_call(logger)(callable_obj)()


@pytest.mark.usefixtures("reset_logging")
class TestFormatterErrorHandling:
    """Test error handling in formatters."""

    def test_json_formatter_invalid_extra_fields_type(self) -> None:
        """Test JSONFormatter raises ValueError for non-dict extra_fields."""
        # Arrange
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        record.created = time.time()
        record.extra_fields = "not a dict"  # pyright: ignore[assignment]

        # Act & Assert
        with pytest.raises(ValueError, match="extra_fields must be a dictionary"):
            formatter.format(record)

    def test_human_readable_formatter_invalid_extra_fields_type(self) -> None:
        """Test HumanReadableFormatter raises ValueError for non-dict extra_fields."""
        # Arrange
        formatter = HumanReadableFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        record.created = time.time()
        record.extra_fields = ["not", "a", "dict"]  # pyright: ignore[assignment]

        # Act & Assert
        with pytest.raises(ValueError, match="extra_fields must be a dictionary"):
            formatter.format(record)

    def test_json_formatter_with_context(self) -> None:
        """Test JSONFormatter includes context variables."""
        # Arrange
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        record.created = time.time()

        # Act
        with log_context(test_context="value123"):
            result = formatter.format(record)

        # Assert
        data = json.loads(result)
        assert data["test_context"] == "value123"

    def test_human_readable_formatter_with_context(self) -> None:
        """Test HumanReadableFormatter includes context variables."""
        # Arrange
        formatter = HumanReadableFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        record.created = time.time()

        # Act
        with log_context(ctx_key="ctx_value"):
            result = formatter.format(record)

        # Assert
        assert "ctx_key" in result or "ctx_value" in result

    def test_json_formatter_without_extra_fields(self) -> None:
        """Test JSONFormatter works when extra_fields attribute doesn't exist."""
        # Arrange
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        record.created = time.time()
        # Don't set extra_fields

        # Act
        result = formatter.format(record)

        # Assert
        data = json.loads(result)
        assert data["level"] == "INFO"
        assert data["message"] == "Test message"
        assert "extra_fields" not in data or "error_type" not in data

    def test_human_readable_formatter_without_extra_fields(self) -> None:
        """Test HumanReadableFormatter works when extra_fields attribute doesn't exist."""
        # Arrange
        formatter = HumanReadableFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        record.created = time.time()
        # Don't set extra_fields

        # Act
        result = formatter.format(record)

        # Assert
        assert "INFO" in result
        assert "Test message" in result


@pytest.mark.usefixtures("reset_logging")
class TestLogContextEdgeCases:
    """Test edge cases for log_context."""

    def test_log_context_nested_context_preservation(self) -> None:
        """Test that nested contexts preserve outer context values."""
        # Arrange
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        record.created = time.time()

        # Act
        with log_context(outer="outer_value"):
            with log_context(inner="inner_value"):
                result1 = formatter.format(record)
            result2 = formatter.format(record)

        # Assert
        data1 = json.loads(result1)
        data2 = json.loads(result2)
        assert data1["outer"] == "outer_value"
        assert data1["inner"] == "inner_value"
        assert data2["outer"] == "outer_value"
        assert "inner" not in data2

    def test_log_context_token_reset_on_exception(self) -> None:
        """Test that context is reset even when exception occurs."""
        # Arrange
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        record.created = time.time()

        exception_msg = "Test exception"

        def raise_test_exception() -> None:
            raise ValueError(exception_msg)

        # Act
        try:
            with log_context(temp="value"):
                raise_test_exception()
        except ValueError:
            pass

        result = formatter.format(record)

        # Assert
        data = json.loads(result)
        assert "temp" not in data


@pytest.mark.usefixtures("reset_logging")
class TestLogApiCallEdgeCases:
    """Test edge cases for log_api_call decorator."""

    def test_log_api_call_with_return_value(self) -> None:
        """Test log_api_call preserves return values."""
        # Arrange
        logger = setup_logger("test")
        logger.info = MagicMock(spec=logging.Logger.info)  # pyright: ignore[assignment]

        @log_api_call(logger)
        def return_dict() -> dict[str, int | str]:
            return {"key": "value", "count": 42}

        # Act
        result = return_dict()

        # Assert
        assert result == {"key": "value", "count": 42}
        assert logger.info.call_count >= _MIN_EXPECTED_CALLS  # pyright: ignore[attr-defined]

    def test_log_api_call_with_exception_chaining(self) -> None:
        """Test log_api_call properly handles exception chaining."""
        # Arrange
        logger = setup_logger("test")
        logger.error = MagicMock(spec=logging.Logger.error)  # pyright: ignore[assignment]

        inner_error_msg = "Inner error"
        outer_error_msg = "Outer error"

        def raise_inner_error() -> None:
            raise ValueError(inner_error_msg)

        @log_api_call(logger)
        def raise_nested() -> None:
            try:
                raise_inner_error()
            except ValueError as e:
                raise RuntimeError(outer_error_msg) from e

        # Act & Assert
        with pytest.raises(RuntimeError, match=outer_error_msg):
            raise_nested()

        # Assert error was logged
        logger.error.assert_called()  # pyright: ignore[attr-defined]

    def test_log_api_call_with_no_args(self) -> None:
        """Test log_api_call works with functions that take no arguments."""
        # Arrange
        logger = setup_logger("test")
        logger.info = MagicMock(spec=logging.Logger.info)  # pyright: ignore[assignment]

        @log_api_call(logger)
        def no_args() -> str:
            return "success"

        # Act
        result = no_args()

        # Assert
        assert result == "success"
        logger.info.assert_called()  # pyright: ignore[attr-defined]

    def test_log_api_call_with_complex_args(self) -> None:
        """Test log_api_call handles complex function arguments."""
        # Arrange
        logger = setup_logger("test")
        logger.info = MagicMock(spec=logging.Logger.info)  # pyright: ignore[assignment]

        @log_api_call(logger)
        def complex_args(
            pos_arg: str, *args: str, kw_arg: str | None = None, **kwargs: str
        ) -> str:
            return f"{pos_arg}_{len(args)}_{kw_arg}_{len(kwargs)}"

        # Act
        result = complex_args("a", "b", "c", kw_arg="kw", extra="val")

        # Assert
        assert "a_2_kw_1" in result
        logger.info.assert_called()  # pyright: ignore[attr-defined]
