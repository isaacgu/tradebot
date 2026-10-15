# Dukascopy pilot preparation — September 6, 2026

## Outcome

The user selected Dukascopy for the bounded alternative-source pilot. Preparation is complete, but acquisition has not started: written permission for the proposed automated retrieval and retention is still missing. No Dukascopy ticks have been downloaded, no account has been opened, and no inquiry has been sent.

This is a permission-and-scope preparation note, not a completed data-quality report. The analytical comparison/report is blocked on authorized source data; no quality scores or source suitability verdicts are invented.

## Available exports do not establish permission for this workflow

Dukascopy's [Historical Data Export](https://www.dukascopy.com/swiss/english/marketwatch/historical/) advertises free historical CSV exports, including ticks, for research and backtesting.

Its [Terms of Use](https://www.dukascopy.com/swiss/english/legal-pages/terms-of-use/), reviewed September 6, 2026, limit personal downloads to non-commercial use, restrict database construction/storage, and require prior express written consent for automated access. We have not established an exception covering this bot's reproducible corpus. The [Important Disclaimer](https://www.dukascopy.com/swiss/english/legal-pages/important-disclaimer/) supplies no additional acquisition licence.

Accordingly ADR-0007's existing licence gate remains in force. FBS permission does not apply to Dukascopy. A human's permitted personal export is not treated as permission for an agent downloader or indefinite training-data retention. No alternate endpoint, browser automation, or JForex account was used to bypass this unresolved scope. This is a conservative project permission check, not a legal opinion.

## The pilot stays focused on the original EURUSD evidence

The frozen recapture plan identifies 24 EURUSD target minutes on 14 UTC dates: 17 gap minutes and seven price-flagged minutes containing ten price events. Every context is [target minute minus 65 minutes, target minute plus six minutes). Their union intersects 38 distinct UTC hour blocks.

GBPUSD has no target in these sealed forensic inputs. It remains a later qualification candidate; it is not silently added as a control. The plan normalizes timestamps as instants to UTC, preserving the original 71-minute ranges.

The selected minutes intentionally overrepresent anomalies. Their flag rate must never be used as a full-month source-quality rate or as a substitute denominator for the existing acceptance test.

## Ready for the provider's response

- [Small EURUSD permission request](dukascopy-pilot-permission-request-20260906.md): prepared, not sent; replaces neither the old full-history draft nor its evidence.
- [Machine-readable pilot plan](dukascopy-pilot-plan-20260906.json): exact ranges, deduplicated hourly scope, original source hashes, proposed limits, permission requirements, and comparison checks.
- The actual download route remains unset. The provider must confirm a route and permitted usage; a subsequent downloader must enforce the written limits.
- The proposed ceiling is one outstanding data request, at least two seconds between requests, two passes, no automatic retries, and at most 76 data requests if the approved route is hourly. These are a request proposal, not an adopted vendor rate.
- Free-only remains the budget. No paid subscription or licence has been accepted.

## What happens after permission

First inspect the reply against the exact access route, cost, retention, research purpose, and reviewer-output requirements. Then implement and test the approved acquisition path and capture the sample without modifying FBS files.

Qualification will preserve original responses and provenance; validate timestamp/field/unit conventions, ordering, duplicates, invalid quotes and gaps; compare the 17 exact FBS gap intervals and the ten price-event paths across venues. Other-venue activity is not proof that FBS omitted a tick, and asynchronous quote times must remain visible.

Only after the pilot is inspected should a separate full reference/stress-period qualification be proposed. It must not splice venues, rewrite failed evidence, relax thresholds, or choose a cleaner reference month. Training and execution remain disabled; no gate is approved.

## Verification and limitations

Two read-only reviews independently confirmed the licensing issue and window inventory. The new plan is derived solely from the frozen plan and review CSVs, with hashes recorded in its inputs. Original FBS artifacts, ADR-0007, the earlier permission draft and the user's existing reference-definition edit remain unchanged.

No Dukascopy sample has been measured. Quote fidelity, coverage, historical revisions and full-corpus acceptance therefore remain unknown. No statistical chart is included because there are no acquired Dukascopy observations to plot.
