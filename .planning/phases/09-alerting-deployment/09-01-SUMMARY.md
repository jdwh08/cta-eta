# Phase 9 Plan 1: Alert Threshold Logic Summary

**Shipped `alerting.py` with tested `should_send_alert()`, `format_alert_message()`, `save_alert_timestamp()`, and `load_last_alert_time()` — pure stdlib, best-effort I/O, 100% test coverage.**

## Accomplishments

- Implemented `should_send_alert(metrics_data, last_alert_path, cooldown_hours)` with correct cooldown logic: checks `should_alert` flag first, then compares elapsed time against cooldown window
- Implemented `format_alert_message(violations)` formatting each violation as `- {metric}: actual={actual} exceeds threshold={threshold}` with graceful handling of missing keys and empty lists
- Implemented `save_alert_timestamp(path)` and `load_last_alert_time(path)` with best-effort I/O (OSError suppressed, invalid JSON returns None)
- 20 passing tests at 100% branch coverage on alerting.py, 0 basedpyright errors, 0 ruff errors
- Used TYPE_CHECKING block for `Path` import to satisfy ruff TC003 rule (consistent with Python 3.13 style)

## Files Created/Modified

- `src/cta_eta/monitoring/alerting.py` - Core alerting logic module with 4 public functions, stdlib only (json, time, logging, pathlib)
- `src/cta_eta/monitoring/__init__.py` - Added `alerting` to `__all__`
- `tests/monitoring/test_alerting.py` - 20 tests across 4 test classes covering all plan-specified cases

## Decisions Made

**Top-level `should_alert` key**: The plan spec explicitly states `metrics_data.get("should_alert", False)` at the top level. The Phase 8 CLI `metrics --json` output nests this inside `alert_context`, so consumers of `should_send_alert()` need to extract the relevant dict slice before passing it, or the plan 02 runner will handle extraction. This matches the plan as written.

**OSError suppression in `save_alert_timestamp`**: The test for OSError suppression uses a file blocking the directory path (rather than a deeply nested non-existent path) because `mkdir(parents=True)` would otherwise succeed. This properly exercises the NotADirectoryError path which is a subclass of OSError.

## Issues Encountered

**Ruff TC003 / TC005**: Initial implementation put `Path` import at module level and had an empty `TYPE_CHECKING` block in tests. Fixed by moving `Path` to `TYPE_CHECKING` blocks in both files and removing the empty block from the test file.

**ERA001 section separators**: Ruff flagged `# ---- section ----` comments in the test file as commented-out code. Suppressed with `# ruff: noqa: ERA001` at file level since these are intentional organizational separators.

## Next Step

Ready for 09-02-PLAN.md (SMTP email sender + alert runner)
