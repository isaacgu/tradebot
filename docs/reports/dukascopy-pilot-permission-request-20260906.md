# Dukascopy pilot permission request

Status: DRAFT — NOT SENT. Prepared September 6, 2026.
This is a new, small EUR/USD pilot request. The earlier full-history and index request is unchanged.

## Message to send

Subject: Permission for a small historical EUR/USD tick-data research pilot

Dear Dukascopy team,

I am Isaac Gumbi, an individual developing a private trading bot. I would like to compare a small sample of your historical EUR/USD bid/ask tick data with another broker's history. Development and refinement will remain demo-only; possible future use would be trading my own account through FBS. I do not manage others' money or sell data or signals.

Your Historical Data Export page offers free downloads for research. Before proceeding, please confirm permission for the specific automated access and local retention below, or direct me to a documented route whose terms expressly permit them.

**Pilot scope:** EUR/USD only, 24 short intervals on 14 dates in October 2024. Each interval is 71 minutes, with the exact UTC ranges listed below. No indices, GBP/USD, or full-history backfill are included in this request.

**Access:** I propose an agent/script-assisted download through your approved Historical Data Export route, or another documented endpoint you designate. Please confirm the exact permitted URL/API, any login requirement, timestamp convention, bid/ask field definitions, and volume units. I will not assume that an accessible archive URL is authorized.

**Load:** one request at a time, at least two seconds between data requests, and two acquisition passes to check reproducibility. This would be 48 data requests if each response covers one listed interval, or 76 if your approved route serves the 38 distinct hourly blocks covering them. There would be no automatic retries. These are proposed limits, not claimed provider limits; I will follow any stricter limits you specify.

**Retention and use:** may I keep the original responses plus parsed/derived copies locally, with checksums and backups, indefinitely for reproducibility? Please expressly clarify how this fits your database/storage restriction and whether the stated research and potential own-account use are permitted. No raw or cleaned quotes will be sold, published, or redistributed. Please also confirm whether non-reconstructable aggregate findings may be shared with an independent reviewer.

**Cost:** please confirm whether this exact pilot is permitted without a fee. I am not agreeing to any paid service. If special terms or a separate licence are required, please provide them first.

If you prefer to provide the small sample directly, please state its permitted retention and research uses in your reply. I will not start automated acquisition before receiving the required written permission.

Exact requested intervals, all UTC, start inclusive and end exclusive:

- 2024-10-07: 12:40–13:51 UTC.
- 2024-10-07: 12:41–13:52 UTC.
- 2024-10-09: 12:24–13:35 UTC.
- 2024-10-09: 13:28–14:39 UTC.
- 2024-10-11: 13:38–14:49 UTC.
- 2024-10-16: 14:11–15:22 UTC.
- 2024-10-17: 10:55–12:06 UTC.
- 2024-10-17: 14:10–15:21 UTC.
- 2024-10-18: 13:28–14:39 UTC.
- 2024-10-21: 11:06–12:17 UTC.
- 2024-10-21: 11:57–13:08 UTC.
- 2024-10-21: 13:15–14:26 UTC.
- 2024-10-21: 13:48–14:59 UTC.
- 2024-10-23: 14:01–15:12 UTC.
- 2024-10-23: 14:02–15:13 UTC.
- 2024-10-24: 12:18–13:29 UTC.
- 2024-10-25: 14:22–15:33 UTC.
- 2024-10-28: 12:27–13:38 UTC.
- 2024-10-29: 12:39–13:50 UTC.
- 2024-10-29: 12:48–13:59 UTC.
- 2024-10-29: 14:55–16:06 UTC.
- 2024-10-30: 12:28–13:39 UTC.
- 2024-10-30: 13:25–14:36 UTC.
- 2024-10-31: 13:25–14:36 UTC.

Thank you for your help.

Kind regards,
Isaac Gumbi

## Internal preparation notes — do not include these in the email

- The selected minutes are the unchanged FBS investigation targets, not cleaner periods selected to obtain a passing result.
- The 24 targets comprise 17 gap minutes and seven price-flagged minutes containing ten price events. They are all EURUSD; GBPUSD qualification is a separate later stage.
- The machine-readable scope and source hashes are in [the pilot plan](dukascopy-pilot-plan-20260906.json).
- No data or account identifiers are attached. The request includes only instrument and time ranges, research purpose, proposed access limits, and intended retention.
- Keep the reply's exact wording, date, sender, scope, conditions and any case reference in a separate evidence record. Do not treat a receipt timestamp as the provider's decision timestamp.
- A permission reply does not pass data-quality acceptance, approve strategy training, or authorize live orders.
