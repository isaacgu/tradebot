# Tradebot

A phased, research-to-live systematic trading platform for FX majors and equity indices.
Only the Phase-0 deterministic foundation exists in this repository. It does not place orders,
connect to a broker, calculate PnL, or provide evidence of trading performance.

## Current gate status

Gate 0 is **not approved**. The specification is frozen at **v1.0** (SHA-256
`dccdcbd9…1689f37`, errata enumerated in [ADR-0004](docs/adr/0004-freeze-errata.md), supplied draft
preserved unmodified at `docs/SPEC-supplied-2026-09-03.md`), and the eight Phase-0 interpretation
questions are answered and adopted. Isaac Gumbi recorded Principal approval on 2026-09-04, and
[CI run 33852037018](https://github.com/isaacgu/tradebot/actions/runs/33852037018) passed both
required jobs on committed candidate `4de5f7a540ed216b3568141bd83392af3189c3cf`. At the Principal's
direction, the repository is temporarily public so GitHub can enforce the `master-release-gate`
ruleset without a Pro subscription. Gate 0 still requires an independent human review. The
implementation is therefore a reviewable Phase-0 candidate, not a certified foundation and not
authorization to begin Phase 1. See the
[Gate-0 evidence pack](docs/reports/gate0_evidence.md) for the row-by-row status.

## Clean-machine setup

Prerequisites: GNU Make, Git, and uv 0.12.9. Install that exact uv release with the official
versioned installer (PowerShell example), then let uv install the pinned managed Python build:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/0.12.9/install.ps1 | iex"
```

Then run in PowerShell, bash, or another shell where `uv` and `make` are available:

```bash
uv python install 3.12.14
uv lock --check
uv sync --locked --extra dev
uv run --no-sync make check
```

The lockfile is authoritative. CI uses the same frozen resolution. A pip-compatible developer
fallback is available when diagnosing a local uv installation, but it is not gate evidence:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
python -m pytest
```

## Gate-0 demo

```bash
uv run --no-sync make demo
```

`make demo` runs the same non-tradable `HelloStrategy` and event pipeline twice:

- backtest wiring: fixed pull feed + `SimClock`;
- paper wiring: fixed arrival-order feed + guarded `WallClock`.

Both consume three already-closed synthetic bars. The command exits non-zero unless their
canonical forecast traces match, writes `build/gate0/demo-manifest.json`, prints structured run
logs, and labels the artifact as smoke-test evidence only. It performs no real sleeping or
network access.

Individual verification commands:

```bash
uv run --no-sync ruff format --check .
uv run --no-sync ruff check .
uv run --no-sync mypy
uv run --no-sync python -W error -m pytest
uv run --no-sync coverage report --include="src/tradebot/core/*" --fail-under=90
uv run --no-sync coverage report --omit="src/tradebot/core/*" --fail-under=80
uv run --no-sync bandit -q -r src
uv run --no-sync pip-audit
```

## Design constraints already enforced

- Platform-defined events are frozen, slotted, UTC-only objects. Prices and quantities at
  boundaries are `Decimal` values.
- The bus rejects an event when either its market timestamp or observation timestamp is later
  than the injected clock, and revalidates structurally typed events immediately before use.
- Handlers run by registration order; re-entrant events append FIFO; a handler exception halts
  dispatch and propagates.
- `SimClock` cannot regress. `WallClock` compares elapsed wall and monotonic time and raises on
  backward or excessive step changes.
- YAML is loaded safely, duplicate and unknown keys fail startup, and canonical resolved config
  is hashed with SHA-256.
- Strategy context exposes only a read-only clock—not a feed, broker, wall clock, or mode flag.

See [interfaces](docs/interfaces.md), [ADRs](docs/adr/), and the
[pending Gate-0 evidence pack](docs/reports/gate0_evidence.md).
