# Core-engine engineering verification

Evidence class: **engineering-decision-replay-only**. This is local software
verification of an uncommitted candidate, not committed-SHA CI or a phase approval.
Financial research trials: 0. Input: invented synthetic bars and generated Parquet
test fixtures. No active Gate-1 acquisition data was consumed by this verification.

SPEC v1.0 SHA-256:
`dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`.

## Measured result

The integrated repository test run completed with **689 passed**, including the
new engine's 125 focused feature, strategy, snapshot, report and adversarial tests.
Overall statement/branch coverage was **88.76%**. Tests ran on the pinned WSL
Python 3.12.14 environment with warnings treated as errors, using a separate
coverage file so the UI task's evidence was not overwritten:

```bash
COVERAGE_FILE=build/core-engine-coverage PYTHONTZPATH= .venv/bin/python -W error -m pytest -q
```

Ruff check/format and Bandit passed on the new implementation. The final strict
repository type check passed all 80 source/test/script files. Both coverage tiers
passed: core 96% against its 90% requirement; remaining modules 88% against 80%.
These results describe the tested candidate and do not certify later concurrent edits.

The checks include hand-calculated returns/volatility/ATR, future-data prefix
invariance, instrument isolation, resumed-vs-uninterrupted decisions, poisoned
checkpoint rejection, exact timestamp precision, snapshot tampering, source
mixing, atomic failed-run publication and the complete Parquet-to-CLI path.

## Demonstration artifacts

Two consecutive runs of `python -m tradebot.research --synthetic` produced the same
content-derived run ID and identical report and trace hashes:

| Artifact | Identity |
|---|---|
| Run ID | `b7c8ce59884f5519d5045f828b2a76c4ed24a679520e4157416168ea104d14ec` |
| Report SHA-256 | `3ba18eaa0b68211124b0365deee5f2d928ceaf540b0433d3bed795300b0d18bb` |
| Decision trace SHA-256 | `79c44f8654a3ca7a6373d7f2f7029edd70687e849baf2fe0af96de06f61ccf8c` |

Artifacts reside at
`build/research/decision-replay/b7c8ce59884f5519d5045f828b2a76c4ed24a679520e4157416168ea104d14ec/`.
The report contains the exact config, source manifest, implementation-file digests,
runtime, SPEC identity and UNCOMMITTED development marker used by these runs.

Of 320 invented observations across two pairs, 62 produced forecasts, 256 were
warmup decisions, and 2 were suppressed for quality flags. A forecast is an
uncalibrated engineering value, not an order, probability or position size.
The artifacts explicitly state `execution_enabled=false`, `costs_modelled=false`,
`pnl_reported=false`, `economic_evaluation=NOT_PERFORMED` and no gate approvals.

Future code/config/runtime changes naturally produce different identities and
require new verification. The immutable run above remains the evidence for this
particular software demonstration. The mutable `latest.json` pointer is for UI
discovery only and must not replace the recorded artifact hashes.
