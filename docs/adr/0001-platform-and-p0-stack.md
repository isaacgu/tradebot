# ADR-0001: Platform and Phase-0 stack

Status: accepted — adopted against SPEC v1.0 (2026-09-04), SHA-256 `dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`. Errata adopted at freeze are enumerated in ADR-0004.

## Context

SPEC §3 requires Python 3.12, a deliberately small event-driven architecture, strict typing,
structured logging, Prometheus metrics, CI, and either uv or Poetry. Gate 0 must run from a clean
machine without requiring PostgreSQL, Redis, Docker, or a broker. Reproducibility requires a
locked dependency graph. The repository was initially empty and the host had Python 3.12 but no
package manager selected.

Alternatives considered:

1. A modular Python process with an in-process synchronous kernel and uv lockfile.
2. The same modular process managed with Poetry. This adds a second project-specific metadata
   layer without a benefit for this build.
3. Separate services connected through Redis from Phase 0. This would add failure modes and make
   the causal demo depend on infrastructure before inter-process communication is needed.

## Decision

Use Python 3.12.14 for gate/CI evidence (the package supports the 3.12 series), a `src/` package
built by exact-pinned `uv_build` 0.12.9, frozen internal dataclasses, Pydantic v2 at YAML
boundaries, a synchronous in-process event bus, `structlog`, the Prometheus Python client,
pytest, ruff, mypy strict mode, Bandit, pip-audit, and GitHub Actions. Use uv 0.12.9 with a
committed lockfile; CI installs uv with the official SHA-pinned setup action and uses the frozen
resolution.
PostgreSQL, Redis, Docker, Grafana, and broker SDKs remain architectural defaults but are not P0
runtime dependencies.

The evaluated FBS integration uses the Windows-only `MetaTrader5` Python IPC client to a local MT5
terminal; it is not installable in the Linux CI/runtime selected here, so this is a platform
compatibility constraint rather than merely deferred dependency timing. P1 may use MT5 only as an
offline Windows extraction source without changing runtime ports, while P4 must decide in a
separate broker ADR between a deliberately isolated Windows bridge and a different execution
broker. No MT5 dependency, adapter, Wine order path, or Windows-hosted core is introduced in P0.

The P0 “paper” run is explicitly a live-shaped, forecast-only wiring smoke test. P4 still owns
the actual PaperBroker, OMS, reconciliation, and NN-1 behavioural-parity evidence. The P0 “backtest” is likewise
a wiring smoke test with no PnL or performance claims, so SPEC NN-7's mandatory cost model is not
being evaded.

The current official uv guide recommends `astral-sh/setup-uv`; the workflow pins the documented
v9 commit rather than a moving tag:
https://docs.astral.sh/uv/guides/integration/github/

## Consequences

The decision path is deterministic and locally testable. Async remains confined to clock/feed
I/O edges. Later phases can add Redis streams between processes without changing event/strategy
contracts. The broad Python dependency ranges remain safe only because `uv.lock` is authoritative
and CI runs `uv lock --check`, `uv sync --locked`, then only `uv run --no-sync`, refusing implicit
lock updates. The exact build backend is also constrained. uv itself is an additional bootstrap
tool.

## Verification

`uv lock --check`, `uv sync --locked --extra dev`, `uv run --no-sync make check`, and the clean
Ubuntu CI workflow must pass. The demo must require no services or network after dependency
installation. `pip-audit` and Gitleaks run in CI. Gate evidence records Python, uv, lockfile,
config, trace, and manifest hashes.
