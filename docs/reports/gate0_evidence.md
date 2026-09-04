# Gate 0 evidence — approved

Status: **APPROVED**. Principal Isaac Gumbi and independent human reviewer Delsa Mashiki signed on
2026-09-04 after the committed-SHA evidence passed CI. No agent self-certified this gate.

Frozen SPEC judged against: `docs/SPEC.md` v1.0, SHA-256
`dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`

Built from the Appendix G template; statuses follow §10.6 and ADR-0005.

## Evidence categories

| # | Category | Status | Content |
|---|---|---|---|
| 1 | CI run on a committed git SHA | PROVIDED | [CI run 33852037018](https://github.com/isaacgu/tradebot/actions/runs/33852037018) passed `quality` and `secrets` on commit [`4de5f7a540ed216b3568141bd83392af3189c3cf`](https://github.com/isaacgu/tradebot/commit/4de5f7a540ed216b3568141bd83392af3189c3cf). |
| 2 | Report / manifest artifact hashes | PROVIDED | Run artifact `gate0-demo-4de5f7a540ed216b3568141bd83392af3189c3cf`: `build/gate0/first.json` SHA-256 `7bb4abedb65a2d1ef0cd49b84c116af57d306fdf7fb0ae3febe74e230db8bf8b`; `build/sbom.cdx.json` SHA-256 `a5710d235e199bcf25e034c5dc96b31e1e76e2aa9531c2978d8964d0916f9190`. |
| 3 | Observability evidence | PROVIDED | Uploaded expositions: backtest SHA-256 `d9529a003bfe99470bf908aa93813e5f3fae4f40e8bef578158367ed1c5ed249`, paper SHA-256 `fe6438e0fc049a4e7bc63f2ec9df21bdb0e31b5876658fe86f887de1ffaf6852`; each has canonical digest `09a168b515ce11e2b00484bc1e0496c19e32cbe1d4705b3e87bff174d2056d36`, reproduced twice by CI. The Grafana screenshot *format* is `DEFERRED-BY-PHASE` (owner: §9.4; due Gate 1 under §4.6 "Quality dashboard live", in full at Gate 4). |
| 4 | Independent reviewer sign-off | PROVIDED | Delsa Mashiki independently reviewed committed candidate `4de5f7a540ed216b3568141bd83392af3189c3cf` and CI run `33852037018`, then approved on 2026-09-04. |
| 5 | Principal sign-off | PROVIDED | Isaac Gumbi (Principal), 2026-09-04. Explicit approval was supplied in the Codex task. It approves Gate 0 only; it does not enable execution or waive any later gate. |

## Inherited obligations from Gate N−1

None — Gate 0 is the first gate.

## Obligations this gate creates

| id | Category | Deferred at | Due at | Status |
|---|---|---|---|---|
| G0-1 | Grafana screenshot format (§9.4) | Gate 0 | Gate 1 (data-quality dashboard, §4.6); full at Gate 4 | open |
| G0-2 | Gate evidence checker `scripts/check_evidence.py` (§10.6, ADR-0005) | Gate 0 | Gate 1 (§13 P1 row) | open |

## Blockers

- [x] Principal freezes `docs/SPEC.md` as an identified version — **cleared**: frozen v1.0 on
  2026-09-04, errata enumerated in ADR-0004, supplied draft preserved at
  `docs/SPEC-supplied-2026-09-03.md`.
- [x] Principal clarifies the §10.6 dashboard-screenshot requirement — **cleared**: §10.6 rewritten
  to the three-status model; ADR-0005 records the decision and the two rejected alternatives. No
  screenshot is silently marked N/A.
- [x] Principal confirms the P0 interpretations listed in `HANDOFF.md` — **cleared**: all eight
  answered and adopted in ADR-0004 (classes A–E).
- [x] CI runs on a committed git SHA and its immutable URL is linked above — **cleared**: run
  `33852037018` passed on `4de5f7a540ed216b3568141bd83392af3189c3cf`.
- [x] Enforce the §12.3 `master` ruleset — **cleared** on 2026-09-04. At the Principal's direction,
  repository visibility is temporarily public and active ruleset `master-release-gate` (GitHub ID
  `22258574`) requires PR-only squash merges, strict `quality` and `secrets` checks, resolved review
  threads, and deletion/non-fast-forward protection with no configured bypass actors. Returning the
  repository to private before upgrading to GitHub Pro would remove this enforcement and reopen the
  blocker.
- [x] Independent human reviewer records sign-off — **cleared**: Delsa Mashiki independently
  reviewed the committed candidate and CI evidence and approved on 2026-09-04.
- [x] Principal records Gate-0 sign-off — **cleared**: Isaac Gumbi approved on 2026-09-04.

## Reproduce

```bash
uv python install 3.12.14
uv lock --check
uv sync --locked --extra dev
uv run --no-sync make check
uv run --no-sync make evidence-hashes
```

The demo manifest records the synthetic dataset ID, seed, git identity, config hashes, logical trace
hashes and the NN-1 code-parity result. A sidecar records the manifest hash without creating a
self-referential JSON document. The evidence is labelled smoke-only and contains no PnL or
performance statistics.

`docs/SPEC.md` is the supplied UTF-8 text normalized to LF line endings with one terminal newline,
plus the errata enumerated in ADR-0004; the unmodified supplied draft is preserved byte-for-byte at
`docs/SPEC-supplied-2026-09-03.md` (SHA-256 `2335e37d…7e1fbc`). Ruff excludes both from formatting —
this is load-bearing, not cosmetic, because ruff 0.16 formats fenced Python inside Markdown and
would otherwise rewrite the §3.6 sketch and change the provenance hash.

## What the NN-1 code-parity result does and does not claim

This run makes **no claim under SPEC §6.8.** `code_parity` asserts NN-1 **code** parity only — one
strategy class, one bus, one pipeline function, with only `DataFeed` and `Clock` swapped — and is
computed over `trace_fields` only, which deliberately **exclude `ts_recv`**; the two runs' `ts_recv`
values differ by roughly six years by construction, because the backtest wiring runs a `SimClock`
seeded at the 2020 fixture while the paper wiring runs a real `WallClock`. It asserts nothing about
clock-availability behaviour, arrival timing, or order generation, none of which exist at P0.
**Behavioural parity (§6.8) remains wholly outstanding and is a Gate-4 criterion.**

Related limitation: because the synthetic fixture is dated 2020, both timestamps sit years in the
past and the bus's look-ahead admission check is trivially satisfied in paper mode. The guard is
therefore not exercised on the green path. Making the fixture wall-relative would **not** fix this —
`bus.py` rejects any event whose `ts_event` is in the future, so a fixture that lets `make demo`
exit zero must have every bar already closed — and it would break NN-10, since the trace hash covers
`ts_event.isoformat()`. The correct closure is a bus-over-`WallClock` test with injected time
sources, owed at P1.

## Committed-SHA CI evidence

Authoritative candidate run on 2026-09-04:

- CI run: `https://github.com/isaacgu/tradebot/actions/runs/33852037018`
- Python version: `3.12.14`
- uv version: `0.12.9`
- git SHA: `4de5f7a540ed216b3568141bd83392af3189c3cf`
- `docs/SPEC.md` (frozen v1.0) SHA-256: `dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`
- `docs/SPEC-supplied-2026-09-03.md` SHA-256: `2335e37dff7e3e0e7f7b88cf3974d9af5d953c404a32d95703bae55bed7e1fbc`
- `uv.lock` SHA-256: `aad538b2212466bdd90693ed40dbc7aa5049f05b89d62550a72bb7835901eb9f`
- demo manifest SHA-256 (schema v2): `7bb4abedb65a2d1ef0cd49b84c116af57d306fdf7fb0ae3febe74e230db8bf8b`
- CycloneDX SBOM SHA-256: `a5710d235e199bcf25e034c5dc96b31e1e76e2aa9531c2978d8964d0916f9190`
- backtest logical trace SHA-256: `4dd0c11b4c9134f03c8a05e1901fb8b376506096a184feebd750e52d0bdbebdc`
- paper logical trace SHA-256: `4dd0c11b4c9134f03c8a05e1901fb8b376506096a184feebd750e52d0bdbebdc`
- backtest canonical metrics SHA-256: `09a168b515ce11e2b00484bc1e0496c19e32cbe1d4705b3e87bff174d2056d36`
- paper canonical metrics SHA-256: `09a168b515ce11e2b00484bc1e0496c19e32cbe1d4705b3e87bff174d2056d36`
- ruff format/check: `PASS`
- mypy strict: `PASS (25 source files)`
- pytest/coverage: `PASS (140 tests; 96.35% overall; core ≥ 90; non-core ≥ 80)`
- Bandit: `PASS`
- pip-audit: `PASS (no known vulnerabilities)`
- host-timezone replay matrix: `PASS (UTC, Africa/Johannesburg, America/New_York)`
- repeated demo byte comparison: `PASS` (manifest byte-identical; canonical metrics digests equal)
- CycloneDX SBOM generation: `PASS`

The two modes' canonical metrics digests are equal because both wirings dispatch the same three bars
and three forecasts. That is an observability fact, not an NN-1 code-parity claim, and it is
deliberately **not**
part of the `code_parity` predicate. The raw `.prom` files differ between runs — the client stamps
wall-clock time into `*_created` — which is precisely why the canonical record is the hashed artifact
and the raw exposition is upload-only.

The `uv.lock` hash above identifies the dependency graph checked by this committed-SHA run;
`make evidence-hashes` recomputes it rather than trusting the hand-copied value.

## Known limitations carried into P1

- **Closed before freeze:** `WallClock.schedule()` previously accepted a past deadline and fired it
  immediately (while `SimClock` rejected it), and an exception raised inside a scheduled callback
  escaped into asyncio's default handler — the callback silently dropped while the handle still
  reported itself live. Both are fixed; see ADR-0003 Consequences.
- **Open, characterised not fixed:** `bus.publish` validates availability *outside* its `try`. A
  `ClockDiscontinuityError` raised by the clock on the **first** `now()` of a publish therefore
  propagates with `halted == False`, whereas the re-validation inside `_drain()` halts the bus
  (now covered by `test_bus_over_wall_clock_halts_on_a_clock_discontinuity`). The asymmetry is real
  but unreachable under `SimClock`; P0 has no order path, so it is carried to P4 against §3.1 #5.
- `_DemoObserver.rejected` and `.failed` are uncovered, because nothing in the demo path drives a
  rejection or a dispatch failure through the observer.
- **`availability_parity_demonstrated: false`** is asserted by a test on the generated manifest. The
  paper wiring runs a real `WallClock` against a 2020 fixture, so every event is years old and the
  admission guard is trivially satisfied. Closing this needs a bus-over-`WallClock` arrival test with
  injected time sources — the first two now exist in `tests/replay/test_lookahead_canary.py`, and the
  full live-arrival case is owed at P1 (ADR-0006).

## Sign-off

Independent human reviewer: Delsa Mashiki  date: 2026-09-04

Principal: Isaac Gumbi — APPROVED  date: 2026-09-04
