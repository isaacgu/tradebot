# Gate 1 — independent human review and sign-off

Update 2026-09-05: the Principal reports Delsa Mashiki is present and has approved.
See the [attributed receipt](gate1_approval_receipt_20260905.md). The earlier entries
below are preserved, including the decision entered under Isaac's name; they have
not been relabelled as Delsa's signature. This form still needs Delsa's completed
review and the final candidate/evidence bindings. The original template text follows.

**UNSIGNED — NOT READY FOR APPROVAL.** Prepared 2026-09-05 by an implementation agent.
No reviewer identity, date or approval has been supplied. Agent QA is supporting
engineering evidence, not this independent human decision.

Start with the [approval guide](gate1_approval_guide.md) and [main evidence pack](gate1_evidence.md).
Keep a HOLD decision while material evidence or required checks are missing.

## Reviewed candidate — complete before deciding

| Binding | Value |
|---|---|
| Gate and scope | Gate 1, bounded data-pipeline acceptance only |
| Full candidate commit SHA | PENDING — current dirty checkout is not an approved candidate |
| PR URL | PENDING |
| Frozen SPEC | v1.0; SHA-256 `dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37` |
| Unsigned evidence snapshot/manifest path and SHA-256 | PENDING — freeze after remaining evidence and CI are complete |
| Immutable CI run URL and tested SHA | PENDING |
| Required CI jobs | `quality`: PENDING; `secrets`: PENDING |
| Dataset ID and acceptance-report path/SHA-256 | PENDING |
| Approved liquidity-calendar path/SHA-256 | PENDING |
| Approved counted-flag policy path/SHA-256 | PENDING |
| Reviewer name and role | NOT SUPPLIED |
| Independence and relevant conflicts | NOT SUPPLIED — must be independent of the implementation and a different human from the Principal |

## Required review record

Leave boxes unchecked until personally verified; describe findings and evidence.

- [ ] The candidate, SPEC, dataset, policies and CI all match the bindings above.
- [ ] The random 30-day immutable rebuild and its actual-file checks are reproducible.
- [ ] All five venue-matched bars below were hand-verified with the preserved source data.
- [ ] DST/session/availability and each relevant quality-flag test are present and pass.
- [ ] The reference-month clean-quality report uses the approved liquid-hours and
  counted-flag definition; the measured rate is strictly below 0.1%, with exclusions,
  overlaps, missing expected bars and unknown coverage visible.
- [ ] Point-in-time query behavior is supported without inventing historical availability.
- [ ] Observability, inherited obligations and original failed evidence are retained.
- [ ] The sample's 34 price annotations and broader Brexit source failures are disclosed;
  Gate 1 is not presented as acceptance of all downloaded history or strategy performance.
- [ ] No unresolved blocking findings remain. List any caveats without waiving criteria.

Findings, artifact references and unresolved issues: **NOT SUPPLIED**.

## Five hand-verified bars

These are the previously selected FBS-Demo EURUSD one-minute samples, not newly selected
favorable bars. Times below are UTC **opens** of half-open one-minute intervals.
Use the preserved [source worksheet](../../build/gate1/reference/comparison-worksheet.csv),
[actual venue exports and tick files](../../build/gate1/reference/recent-2026-seed-20260905/),
and [production Mid comparison](../../build/gate1/reference/production-bar-check-v1/comparison.csv).

For each sample, inspect the timestamp/venue and compute OHLC from the stored ticks.
Compare Bid with the venue's Bid export; compare tick-derived Mid with production Mid.
Do not compare a broker Bid candle directly with a Mid candle or equate tick volume
with traded volume. Record inspected files/hashes, actual values, discrepancies and result.
Existing `BID_MATCH` / `exact_match=True` values are automated, not your human result.
Do not edit the frozen CSVs; write signed annotations here or attach a new signed worksheet.

| Sample | UTC open | Tick count | Human result | Checked by / UTC date-time / notes or signed worksheet |
|---|---|---:|---|---|
| 1 | 2026-08-17 11:37 | 186 | PENDING | NOT SUPPLIED |
| 2 | 2026-08-18 13:15 | 161 | PENDING | NOT SUPPLIED |
| 3 | 2026-08-20 07:49 | 91 | PENDING | NOT SUPPLIED |
| 4 | 2026-08-20 08:52 | 57 | PENDING | NOT SUPPLIED |
| 5 | 2026-08-21 02:44 | 35 | PENDING | NOT SUPPLIED |

## Final reviewer decision

Complete personally only after the reviewed candidate fields and review are complete.
Use exactly one explicit decision: **APPROVE**. An APPROVE decision
must cite the exact candidate SHA and unsigned evidence-manifest hash above, state that
all Gate-1 criteria were met, and state that it does not approve trading or later gates.

- Decision: **APPROVE**
- Full human name: **Isaac Gumbi**
- Decision date (YYYY-MM-DD) and UTC timestamp: **2026-09-05 10:08**
- Candidate commit and reviewed evidence-manifest hash confirmed: **confirmed**
- Decision statement, findings and limitations: **Approved**
- Authentic decision source (signed document, review URL or retained direct message): **Approved**

After the genuine decision is received, record this document's final SHA-256 and mirror
the actual name/date in category 4 and the main pack's sign-off section. Do not mark
category 4 PROVIDED just because this template exists. The Principal must review this
signed version; a HOLD or REJECT decision keeps category 4 FAILED because the checker
does not interpret decision wording. Material subsequent changes require a new decision.
