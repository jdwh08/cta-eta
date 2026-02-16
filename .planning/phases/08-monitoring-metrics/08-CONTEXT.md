# Phase 8: Monitoring & Metrics - Context

**Gathered:** 2026-01-27 (updated)
**Status:** Ready for planning

<vision>
## How This Should Work

**Shift from web interface to CLI + metrics files approach.**

The system runs continuously for months collecting ~230k train position snapshots daily. I need to know when data collection is broken - when daemons fail, when APIs are down, when we're missing data.

Monitoring uses CLI commands backed by metrics files:
- Daemons write metrics to structured files (JSON/JSONL)
- CLI provides focused views for humans: status check, error report, gap summary
- Metrics files include alert context for Phase 9 consumption
- Phase 9 alerting reads metrics via CLI or directly from files

This approach is simpler than HTTP servers - no FastAPI endpoint, no web dashboard overhead. Just files + CLI tools on a cheap VPS.

The CLI supports progressive investigation: quick status check shows overall health, error report drills into what's failing and why, gap summary ties to Parquet data for ML training prep.

</vision>

<essential>
## What Must Be Nailed

- **Phase 9 integration is critical** - Metrics must include alert context (what failed, when, why). Design metrics format knowing Phase 9 will consume it for alerting. Each failure should capture enough detail to generate useful alert messages.
- **Gap visibility tied to data** - Gap details stored in Parquet files (from train_positions_daemon gap_analysis). Keep these. Gap summary CLI connects monitoring to actual data collection completeness.
- **Quick health check** - One command that shows daemon health, last collection times, overall system state. Instant "is everything working?" check.

</essential>

<boundaries>
## What's Out of Scope

- **No web interfaces** - No HTTP servers, no FastAPI endpoints, no web dashboards or UIs. CLI and files only.
- **Email/SMS alerting systems** - That's Phase 9. This phase collects metrics and makes them accessible.
- **Complex visualizations** - No graphs, charts, or fancy formatting. Simple text output from CLI commands.
- **Over-engineering** - Cheap VPS deployment means lightweight solutions. Keep resource usage minimal.

</boundaries>

<specifics>
## Specific Ideas

**CLI Commands (focused views):**
1. **Status check** - Show daemon health, last collection times, overall system state
2. **Error report** - Recent failures, API errors, what's broken and why
3. **Gap summary** - Data collection gaps tied to Parquet files for ML training prep
4. **Metrics dump** - Machine-readable output for Phase 9 alerting to consume

**Implementation context:**
- Dashboard work from plan 08-02 has been reversed (deleted monitoring code and packages)
- Tried web interface approach, realized CLI + files is the right fit
- Metrics collection framework from 08-01 is probably okay to keep
- No specific implementation details yet - figure out best approach during planning

**Gap integration:**
- train_positions_daemon already has gap_analysis functions
- Gap details already stored in Parquet metadata
- CLI gap summary should leverage these existing patterns

</specifics>

<notes>
## Additional Context

The shift happened during implementation - initially planned FastAPI endpoints but realized it was too heavy for a simple daemon monitoring setup. CLI + metrics files is more appropriate for this use case.

2 of 3 plans already complete when this context was updated. The remaining work (08-03: API health tracking) should align with this new CLI-focused vision.

Gap tracking particularly important because missing data affects ETA labels in the ML phase. Need clear visibility into where gaps are to handle them properly during model training.

Deploying on cheap small VPS means keeping resource overhead minimal. No separate server process for monitoring - just files that daemons write and CLI tools that read them.

</notes>

---

*Phase: 08-monitoring-metrics*
*Context gathered: 2026-01-26*
*Context updated: 2026-01-27*
