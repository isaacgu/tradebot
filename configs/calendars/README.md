# Calendar configuration

Phase 1 supplies calendar infrastructure in `tradebot.data.calendar`. It does not supply
approved historical trading/liquidity rules by default.

Economic and macro observations are stored separately per source, record, field and vintage in
append-only SQLite. Queries at time T return only vintages whose availability is no later than T.
Historical sources without archived as-of evidence become available at retrieval and are flagged;
the event's declared release date is never used to invent an earlier receipt time.

Expected-liquidity snapshots are explicit JSON records with an exact instrument key,
`session_date`, `FULL` / `PARTIAL` / `CLOSED`, source and citation, `effective_at`, `available_at`,
`valid_until`, and ordered non-overlapping half-open UTC `expected_intervals`. A CLOSED date has
no intervals. FULL/PARTIAL require explicit intervals; labels alone do not define liquid hours.
Missing, future-effective or expired entries return unknown, never an assumed FULL day.

These expectations belong to quality/completeness checks. They are not imported by market/bar-
boundary functions and must not change historical bar grouping. The separate currency settlement
calendar needed for financing/swap calculations is still outstanding.

`scripts/validate_gate1_calendar.py` records one actual Fed-source integration and a separately
labelled synthetic revision test in `docs/reports/gate1_calendar.json`. Current FBS schedules and
a dated October 2024 notice are preserved as context, but do not establish approved historical
FX liquidity dates. No entries are approved here; source-backed coverage and its provenance must
be reviewed before they can make calendar-dependent Gate-1 quality criteria evaluable.
