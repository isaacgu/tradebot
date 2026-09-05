# Engine and data-repair delivery — September 5, 2026

## Outcome and scope

Source candidate `5b56316a82749a8e60a9fea3596871c69325d400` is committed and
pushed to [draft PR 4](https://github.com/isaacgu/tradebot/pull/4). Its required
`quality` and `secrets` CI jobs passed. Nothing was merged to `master`.
This record supplements, rather than replaces, the
[earlier platform delivery](platform_candidate_20260905.md).

The runnable deliverable is an **offline synthetic decision engine and separate
synthetic fill/cost-accounting runner**, plus whole-system observation dashboards.
No real-data fitting run, accepted strategy, always-on broker execution loop or
financial validation has been completed. Gate 1 remains unapproved. The USD 1,000
demo profile and execution-disabled state are unchanged.

This 32-file increment contains:

- Purpose-scoped research admission checks that reject ordinary real-venue replay
  without a separately trusted, exact-purpose release and its required evidence.
  No production release, registry, eligibility or human approval is supplied.
- Reference-month repairs for complete session selection, calendar identity,
  sufficient lookahead, causal/retrospective separation, counting and approval
  validation. Source capture checks redirects before following them.
- Dashboard separation of **Report integrity** and **Code match**. An intact report
  produced by older implementation bytes is explicitly historical, not current.
  Missing evidence stays unknown rather than being converted to zero or a pass.
- Updated README, engine runbook and engineering decision records. These explain
  synthetic inputs and the whole-system scope without implying training readiness.

The admission guard is a trusted Python workflow boundary, not an arbitrary-code
sandbox or human identity authenticator. The source-capture controls are not a
general network sandbox. Earlier broader security work remains incomplete; no
comprehensive security certification is claimed. Keep services loopback-only and
use the project's pinned inputs, not arbitrary downloaded checkpoint bundles.

## Exact verification

[CI run 33966725497](https://github.com/isaacgu/tradebot/actions/runs/33966725497)
tested PR merge `bae2d442bf9d5b245a1dc8f13fc58136906b1400`, whose parents are
master `0291e5567bd21c5611193dc79bc9cfd159a53ac7` and source candidate
`5b56316a82749a8e60a9fea3596871c69325d400`.

- **1,610 warnings-as-errors tests passed**, 88.34% overall coverage; displayed
  core/non-core tiers were 96%/88%.
- Ruff lint/format, strict mypy on 117 files, dependency audits and full-history
  Gitleaks passed. All 160 checked files were formatted.
- 57 timezone checks passed separately in UTC, Johannesburg and New York.
- Gate-0 demo manifests and canonical metric digests reproduced. This proves
  engineering parity, not financial or Gate-1 acceptance.

The retained CI verification independently checked the exact head/test-merge
relationship, successful job logs, artifact whitelist and checksums, demo parity,
canonical metrics and SBOM. It is at
`build/gate1/platform-publication-20260905/ci-33966725497/verification.json`, SHA-256
`2087107176ece56e4254b33da9469fb4578564b02c9f235f87f639f7acdc9b09`.
The inventory SHA-256 is
`ab600603ead32ec38351ce6d0b952697137449f3cdb87fd77092a3d86ef06e68`.

Before publication, the joint canonical local check passed all ten steps with the
same 1,610 tests and coverage, and no source/configuration/test drift:
`build/preuse/local-checks-20260905T123255Z/result.json`, SHA-256
`b524e0174e9637558b8ca4381481ae5a62192e171e6fc4746c2d5e135f94ef10`.
The earlier formatting-failed run is preserved, not relabelled successful.

The exact 32-file publication manifest was sealed after the final formatting fix;
all staged blobs matched the reviewed files. Scope was 518,434 bytes, with no raw
tick files or account payloads. Full-scope and final-delta Gitleaks checks returned
zero findings. Manifest:
`build/gate1/guard-data-publication-preflight-20260905T122155Z/final-intended-manifest-v2.json`,
SHA-256 `1383ee51a871d19132d293cbe674dcea0ec4340cfc93d3d7d8867f38bcb60f81`.

## Actual runtime checks

The committed decision replay was run twice sequentially. Both runs produced the
same report bytes: 320 invented bars, 256 warmup, 2 suppressed, 0 abstain and 62
forecast decisions. Financial evaluation was not performed; execution stayed off.
Report:
`build/research/decision-replay/1429e94259d3a009a1883be6cf63cbe4072dcb1232b904dbe264d4570fac7d28/report.json`,
SHA-256 `a530c3b2ff1f830bfc63dfd872d4ca3561e0cabd6a3285c5e1269381860c94d0`.
Original reports remain preserved. Prometheus confirmed the fresh report's
implementation-current value is 1; the preceding historical report had value 0.

The execution demo also ran twice sequentially against the committed marker,
with two completed, zero failed and zero incomplete attempts. Each included four
invented cases and eight simulated fills. Both resolved to the same immutable
report; all 28 declared implementation hashes matched current source. **Zero
broker orders were sent.** Report:
`build/execution-smoke-5b56316/artifacts/bb470a2e1ce069ebcf6dea37f36b4ea065694ec92a7ddb06aef60f2bfe9f15ff/report.json`,
SHA-256 `bb470a2e1ce069ebcf6dea37f36b4ea065694ec92a7ddb06aef60f2bfe9f15ff`.

Run from the pinned environment; see the [engine runbook](../runbooks/core-engine.md):

```bash
uv run --no-sync python -m tradebot.research --synthetic
uv run --no-sync python -m tradebot.backtest.execution_demo --help
```

The execution runner requires an explicit attempt identity and output location;
use a new attempt rather than overwriting prior evidence. These commands are not
instructions to initiate training or connect a live broker order adapter.

## Dashboard QA

Grafana 13.2.1 at localhost was checked using Playwright with the existing Microsoft
Edge browser, at 1440 x 1000 and 390 x 844 viewport sizes. No browser or dependency
installation was needed. The page identity, six-panel content, nonblank rendering,
absence of error overlays/page exceptions and mobile horizontal fit passed.

The exercised flow was Engineering Replay → Trading System Overview → Research &
Backtesting → Engineering Replay. Both viewports showed **Verified report** and
**Current code** after the new replay; before it, the intact older report correctly
showed **HISTORICAL · code changed**. The mobile warning text was made fully visible.

One console/network caveat remains: Grafana's anonymous-user `/api/user/stars`
request returns HTTP 401. The tested data queries, panels and navigation still work;
this is not a console-clean claim or a multi-browser/real-order test.
Screenshots were inspected and retained in the local temporary directory
`tradebot-replay-ui-4801f6cd005d478195b7c686ffb6f492` under the operator's temporary
folder (`current-desktop.png`, `current-mobile.png`). They are not part of the
immutable CI evidence bundle.

Open [the whole-system dashboard](http://localhost:3000/d/tradebot-system) or
[the engineering replay](http://localhost:3000/d/tradebot-research).
The broker view remains read-only observation: MT5 can stay running and minimized,
but this does not make its externally/manual-opened positions bot-generated trades.

## Data rebuild and the alternative evidence route

The acquisition task launched a new immutable verification sequence at
2026-09-05 12:31:33 UTC:
`build/gate1/reference-repair-verification-20260905-v2/`.
Its launch SHA-256 is
`3a37dc014fcfe85e97f57662595b7b8ba8e990857ce2906ca3f813bfcbb9e65e`.
It pins 34 input/source hashes and rebuilds the complete October EURUSD reference
month twice, followed by the original seeded random 30-day selection twice.
The observed log has imported 25 selected inputs and reached the first rebuild
from 27 immutable raw files. **No final result existed at this observation.**
Process launch and an intermediate log are not completion or acceptance evidence.
No duplicate job, raw-data overwrite, new broker download or training was started.

The original failed/stopped runs remain preserved. Source, configuration and declared
dependencies are frozen while the owner performs this sequence; additive delivery
documentation does not alter their bytes. The existing dashboard's sampled-30-day
report pointer must not be replaced with the differently scoped reference-month
report without a separately verified consumer change.

The [reference-definition proposal](reference_definition_proposal.md) offers a
project-owned, source-backed QA policy rather than waiting indefinitely for a
complete FBS historical schedule. It is **not** an FBS trading-hours guarantee.
Fresh source captures and the draft calendar/policy are retained in
`build/gate1/reference-definition-20260905-v4/`; proposal SHA-256
`e2f22ccae7697985e5cf585d8f70c7c57819005af3a0c87fd4a051410df03c46`.
The proposed scope is October 2024 EURUSD, 13:00–16:30 London time, excluding
weekends, October 3 and October 14, with the explicit counting rules in that document.
Its actual knowledge/preparation date is in 2026, not backdated to 2024. Earlier
diagnostics had already been seen; this is not pristine statistical preregistration.
No definition approval has been inferred, and the unchanged `<0.1%` acceptance
criterion has not been relaxed or fitted to a result.

## What still requires completion and approval

1. Delsa and Isaac review and explicitly decide on the exact proposed QA definition.
   Completing a mechanical rebuild with its draft is not adoption of that definition.
2. Finish and verify the frozen rebuilds, then evaluate the complete reference month
   using the reviewed definitions. Preserve a failure or indeterminate result.
3. Complete all five personal bar checks in the
   [independent reviewer record](gate1_independent_review.md). Automated comparisons
   and the previously received approval statements do not supply these observations.
4. Bind the exact final code/CI, SPEC, dataset, calendar, policy, reports and review
   hashes into one package. Delsa signs the independent decision; Isaac signs the
   [Principal decision](gate1_principal_approval.md) for that same package. The
   [approval guide](gate1_approval_guide.md) and [evidence register](gate1_evidence.md)
   retain older candidate bindings until final rebinding; this new CI is additional
   evidence, not a transplanted signature or automatic change to their statuses.
5. Complete the later backtester/financial, strategy-admission and risk/execution
   requirements. The first genuine fitting workflow still needs implementation and
   review; it was not queued or executed by this delivery. Gate 1 alone does not
   authorize training, paper orders or live trading.

Build artifacts are local and Git-ignored. Repository links alone do not deliver
them to a reviewer on another machine. A deadline, public-source alternative or
request to continue implementation does not itself waive these requirements.
