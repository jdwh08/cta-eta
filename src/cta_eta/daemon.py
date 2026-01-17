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

import json
import logging
import signal
from abc import ABC, abstractmethod
from pathlib import Path
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

        Loads persisted state from previous run if available.

        Args:
            config: Configuration dictionary with daemon settings
            logger: Logger instance for structured logging
        """
        self.config = config
        self.logger = logger
        self.running = False

        # Load persisted state from previous run
        self._load_state()

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
        """Save current daemon state to JSON file.

        Calls _get_state() to retrieve state from subclass, then writes
        to .daemon_state/{ClassName}.json. Creates directory if needed.
        Logs errors but doesn't crash on I/O failures.
        """
        try:
            state = self._get_state()
            state_dir = Path(".daemon_state")
            state_dir.mkdir(exist_ok=True)

            state_file = state_dir / f"{self.__class__.__name__}.json"
            with state_file.open("w") as f:
                json.dump(state, f, indent=2)

            self.logger.info(
                f"Saved daemon state to {state_file}",
                extra={"extra_fields": {"state_file": str(state_file)}}
            )
        except Exception as e:
            self.logger.error(
                f"Failed to save daemon state: {e}",
                extra={
                    "extra_fields": {
                        "error_type": type(e).__name__,
                        "error_message": str(e)
                    }
                }
            )

    def _load_state(self) -> dict[str, str | int | float] | None:
        """Load persisted daemon state from JSON file.

        Reads state from .daemon_state/{ClassName}.json if it exists.
        Returns None if file doesn't exist or is corrupt.

        Returns:
            Dictionary with persisted state, or None if unavailable
        """
        try:
            state_file = Path(".daemon_state") / f"{self.__class__.__name__}.json"
            if not state_file.exists():
                self.logger.info(
                    "No previous state found, starting fresh",
                    extra={"extra_fields": {"state_file": str(state_file)}}
                )
                return None

            with state_file.open("r") as f:
                state = json.load(f)

            self.logger.info(
                f"Loaded daemon state from {state_file}",
                extra={"extra_fields": {"state_file": str(state_file), "state_keys": list(state.keys())}}
            )
            return state
        except Exception as e:
            self.logger.error(
                f"Failed to load daemon state: {e}",
                extra={
                    "extra_fields": {
                        "error_type": type(e).__name__,
                        "error_message": str(e)
                    }
                }
            )
            return None

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
