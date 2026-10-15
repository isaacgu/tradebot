# P3 preparation — research-control engineering increment

2026-09-05. Evidence class: **ENGINEERING_ONLY**. Financial variants tried: **0**.
This is a tested research-preparation increment, not a trading engine acceptance,
P3 completion, economic result or phase-gate approval. Gate 1 remains unapproved.

## What was built

Three additive modules implement immutable declarations, an auditable attempt ledger,
chronological split/embargo validation and a fixed synthetic demonstration through
the existing causal engine. START is stored before execution, including the requested
partition. Failed attempts cannot be replaced by successful retries, and interrupted
attempts remain visibly incomplete. Repeated successful attempts have separate audit
rows but identical content-addressed reports and decision traces.

The runner permits only training and validation. A lockbox request fails before
fixture access and is logged as failed. Each allowed partition begins with fresh
strategy state. Receipt/availability times and prospective label endpoints determine
eligibility; observations crossing partition boundaries are purged.

Existing `python -m tradebot.research`, report schema, dashboard pointer and Grafana
remain unchanged. New reports have a separate ENGINEERING_ONLY schema and output
root, with no `latest.json`. No broker, cost model, financial statistics or external
data input was added. ADR 0011 describes the design and limits.

## Observed verification

Runtime: pinned WSL Ubuntu CPython 3.12.14; HEAD
`b05d47b6135681b5007601e3a4758ac827278c4b`.

- 137 new tests passed in independent review: 84 split, 46 registry, 7 integration.
- Combined new and existing causal-engine regression suite: **262 passed**.
- Branch-aware coverage of the three new modules: **90.33%** (80% required).
- Ruff lint passed; source Bandit passed; strict mypy passed for the owned modules
  and tests. Staged tests explicitly ignore S101 because they are outside the normal
  `tests/` path used by the project assertion exemption.
- Two complete CLI invocations produced **four separately recorded successful
  engineering attempts**, one fixed variant, and byte-identical artifacts for each
  repeated partition. No financial variants were tried.

The split tests were first run red with the expected missing-module failure. During
integration, tests also exposed the in-progress metadata signature mismatch and
correctly rejected a preregistration after an implementation file changed between
attempts. The final frozen-byte suite passed; these earlier development results are
not represented as successful validation.

### Fixed synthetic demonstration

Experiment ID:
`29f692fdfc898f6649deccd8c287cb0516c30d14000550b2dd4d55109d5fd318`.
Source: 320 invented one-minute bars, two FX-named synthetic series. Each replay
classifies the entire fixture as training 146, validation 136, lockbox 6, purged 12,
and outside/embargo 20. Only the requested unlocked partition reaches the strategy.

| Partition | Processed | Warmup | Suppressed | Forecasts | Lockbox decisions |
| --- | ---: | ---: | ---: | ---: | ---: |
| Training | 146 | 128 | 0 | 18 | 0 |
| Validation | 136 | 128 | 2 | 6 | 0 |

Forecast counts are software outputs, not successful trades or evidence of an edge.
No PnL, costs, fills, orders, fitted parameters or gate approvals are reported.

Artifact SHA-256 identities:

| Partition | Artifact | SHA-256 |
| --- | --- | --- |
| Training | report.json | `55a04e480ec54f37fa852ec912c90155e8660fe6196e5b3316b7e1fcbad56fc1` |
| Training | decisions.jsonl | `2f5aa4808edc019ec361d40ae8c45f58eebbedacb3e3819ef622cf42a04682cc` |
| Validation | report.json | `eb349ab023646a40cca3af7df3d15e1c4f0fdc7ec802f5f684655b336f168b81` |
| Validation | decisions.jsonl | `e47275c63d8ea5408ce7a06aea598d577dfef6f05aa7dbdc94ad4ef80453f33a` |

Reports live under
`build/p3-engineering-staging-20260905/demo/artifacts/<report-sha256>/`.
The demo ledger is `build/p3-engineering-staging-20260905/demo/registry.sqlite`.
After the two invocations it has nine events (one declaration, four starts, four
finishes), with head
`6ad3cdeb905b9a7d301953f1202614e61739029e29f278ef308a3257cce1d612`.
These build artifacts are local and ignored by Git; preserve them separately if
needed as archival engineering evidence.

## Reproduce

From the repository in the pinned WSL environment, before canonical publication:

```bash
PYTHONTZPATH= PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  build/p3-engineering-staging-20260905/run_staged.py \
  --output-root build/p3-engineering-staging-20260905/demo \
  --attempt-prefix new-review \
  --git-sha b05d47b6135681b5007601e3a4758ac827278c4b
```

Use a fresh attempt prefix for every retry; previously recorded IDs are rejected.
After additive publication, the equivalent entry point is
`python -m tradebot.research.experiment_demo`, with the same explicit arguments.
Moving identical source bytes from staging into the package does not itself change
the content-based identity. Changing implementation, config, runtime or fixture
requires a new declaration, never modification of an old one.

The focused combined test command was:

```bash
PYTHONTZPATH= PYTHONDONTWRITEBYTECODE=1 \
COVERAGE_FILE=build/p3-engineering-staging-20260905/.coverage \
.venv/bin/python -m pytest -o addopts= -p no:cacheprovider \
  build/p3-engineering-staging-20260905/tests \
  tests/unit/features tests/unit/strategies tests/unit/research \
  tests/replay/test_research_adversarial.py \
  --cov=tradebot.research.registry --cov=tradebot.research.splits \
  --cov=tradebot.research.experiment_demo --cov-branch \
  --cov-report=term-missing --cov-fail-under=80 -q
```

## Limits and next engineering need

The fixture was already known, generated and hashed in full. This proves lockbox
non-delivery to strategy, **not** untouched real-market data. The local ledger is
not an external timestamp authority, complete financial-trial census or tamper-proof
storage. Coordinated rewriting of the database and hashes is outside its protection.
Audit validates the ledger; artifact hashes must be checked independently after
storage recovery. Publication is not a proven power-loss-durable cross-store
transaction. A crash can leave an orphan artifact and an incomplete attempt.

The lower-level classifier accepts an actual label endpoint supplied by its caller;
the selector applies the fixed declared horizon. No labels are valued and no CPCV
or financial validation is implemented. Engineering attempt counts must not be
interpreted as independent DSR trials.

The next major capability remains a separately reviewed fill/cost simulation and
its known-answer tests, before economic evaluation. Real strategy research also
requires accepted snapshots, sufficient deep/stress history, an economic hypothesis
and prospective research plan, and the remaining statistical and gate requirements.
