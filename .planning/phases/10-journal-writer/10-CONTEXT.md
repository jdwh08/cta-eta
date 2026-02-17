# Phase 10: IPC Journal Writer - Context

**Gathered:** 2026-02-16
**Status:** Ready for planning

<vision>
## How This Should Work

Instead of writing one Parquet file per poll cycle, daemons append each poll's result to a local Arrow IPC "journal" file using a `JournalWriter` class. After each polling cycle completes, the new data is appended to the current journal. Every 15 minutes (configurable), the journal rotates to a new file.

Journal files live in a hive-style directory structure locally (e.g., `data/train/year=2026/month=02/day=16/journal_HHMMSS.ipc`).

This is a clean break — the old per-poll Parquet write path is removed entirely. Both the train position daemon and weather daemon adopt this approach. Weather data is lower frequency, so it may only need the IPC journal without worrying about rotation frequency as long as the weather polling interval is at or above the journal rotation interval.

</vision>

<essential>
## What Must Be Nailed

The full pipeline works end-to-end — no single piece is more critical:

- **JournalWriter class** — reliable append of Arrow record batches to an IPC stream file after each poll
- **Journal rotation** — every 15 minutes (configurable), the current journal closes and a new one opens cleanly
- **Daemon refactor** — both train position and weather daemons write via JournalWriter instead of Parquet-per-poll
- **Clean break** — old per-poll Parquet write code is removed, not kept alongside

</essential>

<boundaries>
## What's Out of Scope

- **Compaction batch job** — converting journals → single daily Parquet file is Phase 11
- **Schema validation / drift detection** — that's Phase 12
- **Cloud upload** — deferred to Phase 11 or later
- **Data joining, feature engineering, standardization** — future pipeline work
- **Backfilling existing per-poll Parquet files** — Phase 10 is going-forward only

</boundaries>

<specifics>
## Specific Ideas

- Local hive-style structure for journal files: `data/{daemon}/year=YYYY/month=MM/day=DD/journal_HHMMSS.ipc`
- Journal rotation interval: 15 minutes default, configurable via `config.toml` (likely under `partition_hour` / `partition_by` settings)
- Weather daemon: lower polling frequency — can use IPC journal approach as long as polling rate ≥ rotation interval
- Train daemon: 15-second polling cycle → append to current journal each cycle

</specifics>

<notes>
## Additional Context

The user's original vision had a two-phase pipeline (local IPC journals → daily Parquet compaction + cloud upload). After discussion, Phase 10 covers only the first part (journal writing). The compaction batch job became Phase 11, pushing the original Phase 11 (Schema Enforcement) to Phase 12.

The roadmap has been updated to reflect: Phase 10 (Journal Writer) → Phase 11 (Compaction) → Phase 12 (Schema Enforcement).

</notes>

---

*Phase: 10-journal-writer*
*Context gathered: 2026-02-16*
