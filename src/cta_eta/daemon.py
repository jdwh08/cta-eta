"""Base daemon framework for continuous 24/7 operation.

This module provides an abstract base class for building long-running polling
daemons with lifecycle management, signal handling, and state persistence.

Usage example:
    class TrainPoller(BaseDaemon):
        def run(self) -> None:
            while self.running:
                # Poll train API
                time.sleep(15)

        def _get_state(self) -> dict[str, str | int | float]:
            return {"last_poll_timestamp": time.time()}

    config = load_config()
    logger = get_logger("train_poller")
    daemon = TrainPoller(config, logger)
    daemon.start()  # Runs until SIGTERM/SIGINT received
"""

import logging
import signal
from abc import ABC, abstractmethod
from types import FrameType


class BaseDaemon(ABC):
    """Abstract base class for long-running daemon processes.

    Provides lifecycle management (start/run/stop), signal handling for
    graceful shutdown, and state persistence across restarts.

    Subclasses must implement:
        - run(): Main daemon logic executed in start()
        - _get_state(): Return current daemon state for persistence

    Attributes:
        config: Configuration dictionary from config.toml
        logger: Structured logger instance
        running: Boolean flag controlling main loop execution
    """

    config: dict[str, dict[str, str | int | float | bool]]
    logger: logging.Logger
    running: bool

    def __init__(self, config: dict[str, dict[str, str | int | float | bool]], logger: logging.Logger) -> None:
        """Initialize daemon with configuration and logger.

        Args:
            config: Configuration dictionary with daemon settings
            logger: Logger instance for structured logging
        """
        self.config = config
        self.logger = logger
        self.running = False

    def start(self) -> None:
        """Start the daemon and run main loop.

        This is the main entry point for the daemon. It:
        1. Logs startup event
        2. Registers signal handlers for graceful shutdown
        3. Sets running flag to True
        4. Calls run() method (implemented by subclass)
        5. Catches and logs exceptions

        The daemon runs until stop() is called (typically via signal handler).
        """
        self.logger.info(
            f"Starting {self.__class__.__name__} daemon",
            extra={"extra_fields": {"daemon_class": self.__class__.__name__}}
        )

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        self.running = True

        try:
            self.run()
        except Exception as e:
            self.logger.error(
                f"Daemon error: {e}",
                extra={
                    "extra_fields": {
                        "error_type": type(e).__name__,
                        "error_message": str(e)
                    }
                },
                exc_info=True
            )
            raise

    @abstractmethod
    def run(self) -> None:
        """Main daemon logic - implement in subclass.

        This method should contain the main loop of the daemon.
        Typically: while self.running: <do work>

        The running flag will be set to False when stop() is called,
        allowing the loop to exit gracefully.
        """
        pass

    def _signal_handler(self, signum: int, frame: FrameType | None) -> None:
        """Handle shutdown signals (SIGTERM, SIGINT).

        Args:
            signum: Signal number received
            frame: Current stack frame (unused)
        """
        signal_name = signal.Signals(signum).name
        self.logger.info(
            f"Received {signal_name}, initiating graceful shutdown",
            extra={"extra_fields": {"signal": signal_name, "signal_number": signum}}
        )
        self.stop()

    def stop(self) -> None:
        """Stop the daemon gracefully.

        This method is called during shutdown (typically by signal handler).
        It sets the running flag to False, calls _save_state() hook, and
        logs the shutdown event. Safe to call multiple times (idempotent).
        """
        if not self.running:
            return  # Already stopped

        self.logger.info(
            f"Stopping {self.__class__.__name__} daemon",
            extra={"extra_fields": {"daemon_class": self.__class__.__name__}}
        )
        self.running = False
        self._save_state()

    def _save_state(self) -> None:
        """Save current daemon state - placeholder for Task 3.

        Subclasses can override _get_state() to provide state to persist.
        This method will be implemented in Task 3 with JSON serialization.
        """
        pass

    @abstractmethod
    def _get_state(self) -> dict[str, str | int | float]:
        """Get current daemon state for persistence - implement in subclass.

        Returns:
            Dictionary with daemon state to persist across restarts.
            Keys should be strings, values should be JSON-serializable
            primitives (str, int, float).

        Example:
            return {
                "last_poll_timestamp": time.time(),
                "next_weather_check": next_check_time,
                "poll_count": 12345
            }
        """
        pass
