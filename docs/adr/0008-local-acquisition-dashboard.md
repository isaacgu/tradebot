# ADR-0008: Local whole-system observability

Status: accepted for the requested Phase-1 implementation, 2026-09-04.
Specification: frozen v1.0, SHA-256
`dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`.

## Context and decision

The operator requested one localhost entry point for evaluating the whole system,
including data collection and quality, research and backtests, strategy and portfolio
state, execution and risk, evidence, operations, and read-only broker observations.
Unimplemented modules and missing evidence remain explicit; an absent metric is not
rendered as healthy, zero, or approved.

A stopped acquisition previously left a stale two-chunk report while 53 durable
checkpoints existed. Final reports alone cannot answer whether collection is running.
Keep the separate, read-only Python acquisition exporter on port 8765. It reads the
selected plan, completed checkpoints, report freshness, and process identity. It does
not connect to MT5 or import the acquisition runner. Its dashboard shows process
health, collected/expected/empty chunks, symbol/window coverage, ticks, request
pacing, quality diagnostics, and explicit unknown states. Collection completion is
distinct from data-quality acceptance. A successful empty response is a completed
request, not usable history. Unknown process access never becomes a stopped or
healthy assertion, and checkpoint silence is not a heartbeat.

Add a second, read-only broker snapshot exporter on `127.0.0.1:8766`. It observes an
already-running MT5 terminal through account, position, order, and optional quote read
operations. It is not a streaming feed, the Phase-4 broker adapter, an execution path,
or an order-entry API. Snapshot collection is serialized and defaults to one poll
every five seconds; HTTP requests only read the most recent snapshot. A native process
identity check is a best-effort guard against implicitly launching a terminal, not an
absolute operating-system guarantee.

Core account, position, and order reads form one snapshot. On failure, the exporter
retains the last successful snapshot and exposes its age, stale state, and error rather
than publishing a fresh-looking empty result. A timed-out IPC read poisons that
observer instance: it performs no further MT5 IPC until the operator restarts the
observer, preventing overlapping terminal calls. An account change must likewise
latch a restart-required state. The account identity used for that comparison stays
private in memory; account identifiers, names, comments, credentials, and order-entry
controls are never published. Manual and externally created positions and orders are
included and labelled `not_bot_certified`; broker-reported profit is not this bot's
after-cost performance.

Broker HTTP requests must pass a loopback `Host` allowlist before routing or returning
financial state. The private in-memory account pin, account-change latch, predicate
requiring exactly one matching already-running terminal process, and `Host` allowlist
are implemented and locally verified in the scoped checks below. An intentional
account switch requires an observer restart; the prior account snapshot remains
stale until then. This does not upgrade the trusted-localhost model to authentication.

All listeners bind to loopback. Grafana grants local anonymous Viewer access to the
provisioned dashboards, disables logins and initial admin creation, and stores its
runtime state under ignored `build/monitoring/`. Prometheus and Grafana therefore use
a trusted, single-user-localhost model, not application authentication. Ports 3000,
9090, 8765, and 8766 must not be exposed to a shared host or remote network; doing so
requires a separate authenticated deployment decision. Per-position and per-order
labels can accumulate high cardinality over the 30-day Prometheus retention window,
so this first local view is not suitable unchanged for high-turnover or multi-account
deployment.

Native Windows services are launched as hidden background processes because Docker's
daemon is unavailable on this host. Official versioned archives are checksum-verified
before extraction. No system service or boot task is installed. Their configuration
and dashboard JSON are kept in version control. Closing the browser does not stop the
processes; machine shutdown does. The launcher is idempotent and logs locally.

The existing probe and analysis module remain unchanged while acquisition is in
progress: their hashes and Git revision are part of checkpoint resume provenance.

The acquisition observer also consumes completed engineering-replay reports through
a bounded read-only validator. The five-panel Engineering Replay view exposes only
artifact state, source class, bar count and decision counts with explicit caveats.
It verifies report identities and non-trading claims; it does not start a replay,
import the engine or read detailed traces. Missing/rejected evidence stays unknown.
Synthetic and immutable-clean-snapshot replays are both engineering-only, not live
signals or financial evaluation. The producer is separately governed by ADR-0010.

## Alternatives

- A custom web application would duplicate the v1 observability stack.
- Reading only final reports reproduces the stale-status defect.
- Making the acquisition exporter connect to MT5 would erase the acquisition/broker
  trust boundary and couple monitoring availability to collection.
- Treating the broker observer as a streaming or execution adapter would prematurely
  create a second trading path outside the Phase-4 controls.
- Publishing account identity for account-change detection would expose information
  that the dashboard does not need; a private in-memory pin provides the guard.

## Verification

Acquisition unit tests cover aggregation, empty/missing/invalid checkpoints, and
process-state uncertainty. The final broker Python delta passed 64 targeted tests,
Ruff formatting/lint, strict mypy and Bandit. Tests cover atomic snapshots, genuine
empty results, retained stale data, timeout poisoning, account-change latching,
terminal process checks, row and quote bounds, sensitive-field exclusion, and rejected
non-loopback, malformed, or duplicate `Host` headers. Account switches were simulated
in unit tests; no live account switch was performed. A native check after selective
observer restart observed a fresh demo snapshot, zero actual positions/orders, no
account-change or timeout latch, and seven successful polls. A request bearing a
foreign Host received HTTP 403 without financial or account-identity fields.

The final JSON-only dashboard polish separately passed all 34 real query definitions
through Prometheus and Grafana. Three nonempty synthetic fixtures exercised Grafana's
actual table transforms, verifying two correctly joined rows for each positions,
orders and quotes table and the no-expiration mapping. Synthetic inputs were never
ingested or presented as real broker activity. The local whole-tree 530-test baseline
predates these broker changes; its result does not certify the changed whole tree.
Hashes and scoped artifacts are recorded in `docs/reports/gate1_evidence.md`.

The additive research consumer separately passed 36 focused warnings-as-errors
tests, Ruff formatting/lint, strict mypy and Bandit. Its initial verified artifact
is a completed synthetic 320-bar replay, not performance or a Gate-1 data-acceptance
claim. This later consumer is outside the historical 530-test baseline snapshot.
The combined monitoring test group passed 101 warnings-as-errors tests without
coverage collection before the final research JSON freshness patch. After that
patch, 13 focused tests passed. The final four research queries are instant-only
with no range-history samples and matched direct Prometheus/Grafana values; the
1280x720 rendered view showed all counts/caveats without console warnings/errors.
These scoped checks are not a replacement whole-repository test run.
The post-fix combined monitoring group also passed all 101 tests in 5.00s with
warnings as errors/no coverage collection. Native command-line inspection verified
the optional launcher report-path override after selective observer restart; a
not-yet-created acceptance report remains missing/unknown rather than a pass.

Local HTTP checks verify both exporters, Prometheus scrape health, and Grafana's
provisioned dashboards. Browser inspection verifies rendered values, stale/unknown
states, links, and filters against the source snapshots. A local screenshot and
exposition supplement Gate evidence; neither automatically approves a gate.

Sources:
[Grafana Prometheus support](https://grafana.com/docs/grafana/latest/datasources/prometheus/),
[Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/).
