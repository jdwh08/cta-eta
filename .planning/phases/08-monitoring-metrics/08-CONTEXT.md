# Phase 8: Monitoring & Metrics - Context

**Gathered:** 2026-01-26
**Status:** Ready for planning

<vision>
## How This Should Work

The system runs continuously for months collecting ~230k train position snapshots daily. I need to know when data collection is broken - when daemons fail, when APIs are down, when we're missing data.

Monitoring should provide multiple ways to check system health:
- **Logs for deep investigation** - tail logs when something's weird
- **Quick status checks** - simple way to see "is everything alive and healthy?"
- **Metrics for alerting** - structured data that Phase 9 can use to notify me of critical failures

The monitoring should feel like progressive investigation: start with high-level daemon status, drill into recent API call history if something looks off, check data gap summaries to verify collection completeness.

Lightweight web endpoint (FastAPI) makes sense if practical - we're deploying on a cheap small VPS, so keep it simple. More important than the interface is having a clear readout of status, issues, and logs. I want to avoid metrics.jsonl if possible since it's harder to access and diagnose state or issues.

</vision>

<essential>
## What Must Be Nailed

- **Immediate visibility into broken collection** - Cannot afford silent failures when collecting months of training data. If a daemon dies or an API is persistently failing, I need to know.
- **Tied to the data** - Gap details are already stored in Parquet files (from train_positions_daemon gap_analysis functions). Keep these. Monitoring should reflect what's actually happening with data collection.
- **Built for Phase 9 alerting** - Design metrics collection knowing it will feed the alerting system. Include context needed for alert messages, track what needs notification, make it easy for alerting to consume.

</essential>

<boundaries>
## What's Out of Scope

- Email/SMS alerting systems - that's Phase 9, this phase just collects and displays metrics
- Complex dashboards with graphs - no fancy visualizations or historical trend charts, just current state and basic stats
- Performance optimization analysis - track the metrics, don't analyze performance improvements or do deep profiling
- Over-engineering - we're on a cheap VPS, keep resource usage minimal

</boundaries>

<specifics>
## Specific Ideas

- **FastAPI over Flask** - If using HTTP server for endpoints, FastAPI makes more sense (Flask feels dated)
- **Progressive investigation interface:**
  1. High-level daemon status - which daemons running, when last collected data, health state
  2. Recent API call history - what succeeded, what failed, error messages for each service
  3. Data collection gaps - summary of when we missed data, how long gaps were, which lines/datasets affected
- **Leverage existing gap_analysis** - train_positions_daemon already has gap detection functions, build on these
- **Gap details in Parquet** - Already storing gap metadata with the data files, keep this pattern
- **Preference against metrics.jsonl** - Harder to access and diagnose state/issues compared to endpoints or status files

</specifics>

<notes>
## Additional Context

The monitoring needs to support investigation when things look wrong. Quick path from "something seems off" to "here's exactly what's happening with the APIs and data collection."

Gap tracking is particularly important because missing data will throw off ETA labels in the later machine learning phase. We need to know where the gaps are to handle them properly during model training.

Deploying on cheap small VPS means keeping resource overhead minimal - lightweight solutions preferred.

</notes>

---

*Phase: 08-monitoring-metrics*
*Context gathered: 2026-01-26*
