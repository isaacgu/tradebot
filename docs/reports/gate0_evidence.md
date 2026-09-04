# Gate 0 evidence — candidate, not approved

Status: **PENDING**. Neither the Architect nor QA may self-certify this gate (SPEC §13 preamble).

Frozen SPEC judged against: `docs/SPEC.md` v1.0, SHA-256
`dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`

Built from the Appendix G template; statuses follow §10.6 and ADR-0005.

## Evidence categories

| # | Category | Status | Content |
|---|---|---|---|
| 1 | CI run on a committed git SHA | FAILED | No successful committed-SHA CI run is linked in this pack. Remediation: publish the candidate, protect `master` per §12.3, then link the immutable run URL and SHA here. |
| 2 | Report / manifest artifact hashes | FAILED | Local hashes reproduce bit-for-bit (below) but were generated on an uncommitted tree, so they are not gate evidence. Remediation: regenerate on the committed SHA via `make evidence-hashes`. |
| 3 | Observability evidence | FAILED | The canonical Prometheus exposition is now emitted per mode by `make demo`, with a reproducible digest, and CI compares the two runs' digests. Digests below. This row flips to `PROVIDED` as soon as it is produced on a committed SHA. The Grafana screenshot *format* is `DEFERRED-BY-PHASE` (owner: §9.4; due Gate 1 under §4.6 "Quality dashboard live", in full at Gate 4). |
| 4 | Independent reviewer sign-off | FAILED | Independent Codex reviewer agents passed the local technical checks, but Appendix G requires a person to sign. No independent human sign-off has been obtained. Remediation: an independent human reviews the committed-SHA evidence and records name and date below. |
| 5 | Principal sign-off | PROVIDED | Isaac Gumbi (Principal), 2026-09-04. Explicit approval was supplied in the Codex task. It does not waive any failed row, authorize P1, or enable execution. |

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
- [ ] CI runs on a committed git SHA and its immutable URL is linked above.
- [ ] Independent human reviewer records sign-off.
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

## Local candidate evidence

Final local candidate run on 2026-09-04 (useful for review, invalid as formal Gate-0 evidence until
repeated by CI on a committed SHA):

- Python version: `3.12.14`
- uv version: `0.12.9`
- git SHA: `UNCOMMITTED` (intentionally fails the committed-evidence requirement)
- `docs/SPEC.md` (frozen v1.0) SHA-256: `dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`
- `docs/SPEC-supplied-2026-09-03.md` SHA-256: `2335e37dff7e3e0e7f7b88cf3974d9af5d953c404a32d95703bae55bed7e1fbc`
- demo manifest SHA-256 (schema v2): `ea35658d5de63a2b31f48f1b5e8b410c792768b82856b834bed29554486919d4`
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

`uv.lock` is hashed by `make evidence-hashes` at the time of the run; it is not restated here,
because a hand-copied lockfile hash drifts silently from the file it claims to describe.

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

Independent human reviewer: ______________________  date: __________

Principal: Isaac Gumbi — APPROVED  date: 2026-09-04
