"""Base daemon framework for continuous 24/7 operation.

This module provides an abstract base class for building long-running polling
daemons with lifecycle management, signal handling, and state persistence.

Usage example:
    class TrainPoller(AsyncBaseDaemon):
        async def run(self) -> None:
            while self.running:
                # Poll train API
                await self.sleep(15)

        async def _get_state(self) -> dict[str, str | int | float]:
            return {"last_poll_timestamp": time.time()}
"""

from __future__ import annotations

import asyncio
import json
import signal
from abc import ABC, abstractmethod
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cta_eta.data_collection.exceptions import DaemonNotStartedError
from cta_eta.data_collection.orchestration.diagnostics import (
    DaemonDiagnostics,
    DaemonDiagnosticsConfig,
)

if TYPE_CHECKING:
    import logging
    from types import FrameType


type ConfigValue = Any
type ConfigSection = dict[str, ConfigValue]
type Config = dict[str, ConfigSection]


class AsyncBaseDaemon(ABC):
    """Abstract base class for long-running async daemons.

    This mirrors `BaseDaemon`'s lifecycle pattern (start/run/stop, signal handling,
    and state persistence) but is designed for asyncio-native implementations.

    Key behaviors:
    - `start()` is synchronous and blocks the calling thread until shutdown.
    - `run()` is async and should cooperatively exit when `self.running` becomes False.
    - `sleep()` is interruptible: it wakes early on shutdown, enabling fast SIGTERM.
    - Signal handlers are registered on the running event loop when possible.
    """

    config: Config
    logger: logging.Logger
    running: bool

    _loop: asyncio.AbstractEventLoop | None
    _shutdown: asyncio.Event | None
    _shutdown_requested: bool
    _diagnostics_task: asyncio.Task[None] | None
    _diagnostics_interval_s: float

    def __init__(
        self,
        config: Config,
        logger: logging.Logger,
    ) -> None:
        """Initialize daemon with configuration and logger.

        Loads persisted state from previous run if available.
        """
        self.config = config
        self.logger = logger
        self.running = False

        self._loop = None
        self._shutdown = None
        self._shutdown_requested = False
        self._diagnostics_task = None
        self._diagnostics_interval_s = 0.0

        diag_cfg = DaemonDiagnosticsConfig.from_config(
            None,
            daemon_name=self.__class__.__name__,
            config=self.config,
        )
        self._diagnostics_interval_s = diag_cfg.summary_interval_seconds
        self.diagnostics = DaemonDiagnostics(
            logger=logger,
            daemon_name=self.__class__.__name__,
            config=diag_cfg,
        )

        # Load and apply state
        state = self._load_state()
        self._apply_state(state if state is not None else {})

    def start(self) -> None:
        """Start the daemon and run the async main loop.

        This is the synchronous entry point. It:
        - Logs startup
        - Runs an asyncio event loop until shutdown
        - Logs unexpected exceptions and re-raises
        """
        self.logger.info(
            f"Starting {self.__class__.__name__} daemon",
            extra={"extra_fields": {"daemon_class": self.__class__.__name__}},
        )

        try:
            asyncio.run(self._run_main())
        except Exception as e:
            self.logger.exception(
                "Daemon error",
                extra={
                    "extra_fields": {
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                    }
                },
            )
            raise

    async def _run_main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._shutdown = asyncio.Event()

        if self._shutdown_requested:
            self._shutdown.set()

        self._register_signal_handlers()
        self.running = True

        if self.diagnostics.enabled:
            self._diagnostics_task = asyncio.create_task(
                self._run_diagnostics_loop(),
                name=f"{self.__class__.__name__}.diagnostics",
            )

        run_task = asyncio.create_task(
            self.run(), name=f"{self.__class__.__name__}.run"
        )
        shutdown_task = asyncio.create_task(
            self._shutdown.wait(),
            name=f"{self.__class__.__name__}.shutdown_wait",
        )

        try:
            done, pending = await asyncio.wait(
                {run_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            # If shutdown was requested, cancel the main run task so we don't hang on
            # long waits (HTTP, throttlers, etc). Cooperative `self.running` checks
            # alone are not sufficient for fast shutdown.
            if shutdown_task in done and not run_task.done():
                run_task.cancel()

            with suppress(asyncio.CancelledError):
                await run_task
        finally:
            for task in pending:
                task.cancel()

            diagnostics_task = self._diagnostics_task
            if diagnostics_task is not None:
                diagnostics_task.cancel()
                with suppress(asyncio.CancelledError):
                    await diagnostics_task

            # Ensure `running` is consistent even if `run()` exits unexpectedly.
            self.running = False

    def _register_signal_handlers(self) -> None:
        """Register SIGTERM/SIGINT handlers to stop gracefully.

        On Unix event loops, `loop.add_signal_handler` provides reliable delivery into
        the event loop. If unsupported, we fall back to `signal.signal`.
        """
        loop = self._loop
        for signum in (signal.SIGTERM, signal.SIGINT):
            if loop is None:
                signal.signal(signum, self._signal_handler)
                continue
            try:
                loop.add_signal_handler(signum, self._signal_handler, signum, None)
                continue
            except NotImplementedError:
                pass
            signal.signal(signum, self._signal_handler)

    @abstractmethod
    async def run(self) -> None:
        """Run the main async daemon logic.

        Implementations should typically loop while `self.running` and periodically
        `await self.sleep(...)` instead of `asyncio.sleep(...)` so the daemon responds
        promptly to shutdown signals.
        """

    async def sleep(self, seconds: float) -> None:
        """Sleep for up to `seconds`, waking early if shutdown is requested.

        This is the preferred sleep primitive for daemon loops because it makes
        shutdown fast even for long polling intervals.
        """
        if seconds <= 0:
            return

        shutdown = self._shutdown
        if shutdown is None:
            raise DaemonNotStartedError

        try:
            await asyncio.wait_for(shutdown.wait(), timeout=seconds)
        except TimeoutError:
            return

    def _signal_handler(self, signum: int, frame: FrameType | None) -> None:  # noqa: ARG002
        signal_name = signal.Signals(signum).name
        self.logger.info(
            f"Received {signal_name}, initiating graceful shutdown",
            extra={"extra_fields": {"signal": signal_name, "signal_number": signum}},
        )
        self.stop()

    def stop(self) -> None:
        """Stop the daemon gracefully.

        This is safe to call multiple times. It:
        - Logs shutdown once
        - Marks the daemon as not running
        - Triggers an interruptible shutdown event (if started)
        - Calls pre-shutdown hook for daemon-specific cleanup
        - Persists state
        """
        if not self.running:
            self._shutdown_requested = True
            return

        self.logger.info(
            f"Stopping {self.__class__.__name__} daemon",
            extra={"extra_fields": {"daemon_class": self.__class__.__name__}},
        )

        self.running = False
        self._shutdown_requested = True

        shutdown = self._shutdown
        loop = self._loop
        if shutdown is not None and loop is not None and loop.is_running():
            loop.call_soon_threadsafe(shutdown.set)

        # Best-effort final diagnostics summary + snapshot on clean shutdown.
        if self.diagnostics.enabled:
            self.diagnostics.maybe_log_summary(force=True)
            self._save_diagnostics_snapshot()

        # Pre-shutdown hook for daemon-specific cleanup (e.g., flush storage)
        try:
            self._pre_shutdown_hook()
        except Exception as e:
            self.logger.exception(
                "Error in pre-shutdown hook",
                extra={
                    "extra_fields": {
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                    }
                },
            )

        self._save_state()

    def _save_state(self) -> None:
        """Save daemon state to JSON file.

        Wraps state save in try/except to log errors but not crash shutdown.
        State persistence is critical for restart gap detection.
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
                extra={"extra_fields": {"state_file": str(state_file)}},
            )
        except Exception as e:
            self.logger.exception(
                "Failed to save daemon state",
                extra={
                    "extra_fields": {
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                    }
                },
            )

    def _save_diagnostics_snapshot(self) -> None:
        """Persist a diagnostics snapshot (best-effort, never raises)."""
        try:
            state_dir = Path(".daemon_state")
            state_dir.mkdir(exist_ok=True)
            snapshot_file = state_dir / f"{self.__class__.__name__}.diagnostics.json"
            with snapshot_file.open("w", encoding="utf-8") as f:
                json.dump(self.diagnostics.snapshot(), f, indent=2)
            self.logger.info(
                f"Saved diagnostics snapshot to {snapshot_file}",
                extra={"extra_fields": {"snapshot_file": str(snapshot_file)}},
            )
        except Exception as e:
            self.logger.exception(
                "Failed to save diagnostics snapshot",
                extra={
                    "extra_fields": {
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                    }
                },
            )

    def _load_state(self) -> dict[str, str | int | float] | None:
        try:
            state_file = Path(".daemon_state") / f"{self.__class__.__name__}.json"
            if not state_file.exists():
                self.logger.info(
                    "No previous state found, starting fresh",
                    extra={"extra_fields": {"state_file": str(state_file)}},
                )
                return None

            with state_file.open("r") as f:
                state = json.load(f)

            self.logger.info(
                f"Loaded daemon state from {state_file}",
                extra={
                    "extra_fields": {
                        "state_file": str(state_file),
                        "state_keys": list(state.keys()),
                    }
                },
            )
        except Exception as e:
            self.logger.exception(
                "Failed to load daemon state",
                extra={
                    "extra_fields": {
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                    }
                },
            )
            return None
        else:
            return state

    @abstractmethod
    def _get_state(self) -> dict[str, str | int | float]:
        """Return current daemon state for persistence."""

    @abstractmethod
    def _apply_state(self, state: dict[str, str | int | float]) -> None:
        """Apply loaded state to daemon instance.

        Subclasses can override this to restore state attributes from the
        loaded state dict. Called during __init__ after _load_state().

        Args:
            state: State dictionary loaded from persistent storage (empty dict if no state)

        """

    def _pre_shutdown_hook(self) -> None:
        """Pre-shutdown hook for daemon-specific cleanup.

        Called before state save during shutdown. Subclasses can override to
        perform cleanup tasks like flushing buffered storage.

        Default implementation attempts to flush storage if available.
        """
        # Attempt to flush storage if present
        storage = getattr(self, "storage", None)
        if storage is None:
            self.logger.warning("No storage available to flush")
            return

        flush = getattr(storage, "flush", None)
        if not callable(flush):
            self.logger.warning("Storage has no flush method")
            return

        try:
            flush()
            self.logger.debug("Flushed storage during shutdown")
        except Exception as e:  # noqa: BLE001
            self.logger.warning(
                f"Failed to flush storage: {e}",
                extra={
                    "extra_fields": {
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                    }
                },
            )

    async def _run_diagnostics_loop(self) -> None:
        """Periodically log diagnostics summaries while running.

        This loop is intentionally simple and best-effort. It should never block the
        daemon shutdown path; it is cancelled during `_run_main` teardown.
        """
        shutdown = self._shutdown
        if shutdown is None:
            return

        while self.running:
            interval = max(1.0, float(self._diagnostics_interval_s))
            with suppress(TimeoutError):
                await asyncio.wait_for(shutdown.wait(), timeout=interval)
            self.diagnostics.maybe_log_summary()
