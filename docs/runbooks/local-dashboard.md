# Local trading-system dashboard

The system entry point is <http://localhost:3000/d/tradebot-system>. Data and quality
evidence is at <http://localhost:3000/d/tradebot-data-quality>; the acquisition
drilldown is <http://localhost:3000/d/tradebot-acquisition>. These are native Grafana
dashboards. The new read-only Broker & Trades view is
<http://localhost:3000/d/tradebot-broker>; its local observer, queries and table transforms
have been verified, with browser captures recorded separately in the Gate-1 evidence pack.
The README describes their system-wide product structure.

The five-panel <http://localhost:3000/d/tradebot-research> Engineering Replay view
shows a completed report's caveats, artifact validity, source class, bar count and
decision counts. It is not a live signal or trade dashboard.

The overview distinguishes implemented source, operational services and approved
gates. The engineering replay preview is implemented but not economically evaluated.
Future financial backtest, accepted-strategy, execution and risk capabilities remain
visibly unimplemented until the actual components and evidence exist. There are no
mock P&L values, positions, fills, or trading controls.

## Start and stop

From the repository root in PowerShell:

```powershell
./scripts/start_dashboard.ps1
./scripts/stop_dashboard.ps1
```

To restart only the broker observer without stopping the charts or acquisition:

```powershell
./scripts/stop_dashboard.ps1 -Name broker
./scripts/start_dashboard.ps1
```

To point only the acquisition/data-quality observer at the replacement acceptance
run, stop that observer and pass its new report path:

```powershell
./scripts/stop_dashboard.ps1 -Name exporter
./scripts/start_dashboard.ps1 -Gate1Report 'build/gate1/30day-stable-b102ecdd/report.json'
```

The replacement report is now complete and its reproducibility checks pass, while tick quality
is FAILED and calendar acceptance remains unknown. For future builds, the report need not
exist while a rebuild is active; missing evidence stays unknown.
`-Gate1Report` defaults to `build/gate1/30day/report.json`. Supplying a different path
does not reconfigure an already-running observer, so the selective stop is necessary.
Keep supplying the desired path when starting that observer after a reboot. These
commands do not stop or restart the historical acquisition worker or offline rebuild.
The original `build/gate1/30day` run was stopped after code drift and remains preserved,
not reclassified as passing evidence.

The default terminal path is `C:\Program Files\MetaTrader 5\terminal64.exe`.
If the already-open terminal uses another installation, pass its exact executable
path to `./scripts/start_dashboard.ps1 -BrokerTerminal 'C:\path\terminal64.exe'`.
Use the terminal to select/log in to the intended account; never put credentials
in the launcher, exporter arguments or repository.

The launcher starts hidden local processes and records their identity in
`build/monitoring/services.json`. It is safe to rerun while those processes remain
alive. The stop command verifies executable and creation time before stopping only
those recorded monitoring processes. Neither command starts/stops the FBS probe.
Log files are under `build/monitoring/logs/`. A browser may be closed while monitoring
continues. Restart monitoring after a machine reboot; there is no boot service.

Native Grafana cold starts have taken roughly four to five minutes on this machine.
The launcher now waits up to 300 seconds by default. Set `-StartupTimeoutSeconds`
to an explicit value from 15 to 600 when needed. A readiness timeout leaves the
recorded process running; it does not mean Grafana has exited. Inspect the latest
Grafana log and check readiness before retrying:

```powershell
Invoke-RestMethod http://127.0.0.1:3000/api/health -TimeoutSec 5
```

Once this returns `database: ok`, rerun the launcher with the desired `-Gate1Report`
path to verify all four services. It reuses the recorded, identity-checked processes. Do not
blindly kill/relaunch Grafana or start another copy after a cold-start timeout.

## Runtime setup

The supplied runtime lives entirely under ignored `build/monitoring/`. For a fresh
machine, use Python 3.12.14 and the exact versions/official URLs/checksums in
`deploy/monitoring-versions.json`. Verify each archive with `Get-FileHash -Algorithm
SHA256` before extraction. Extract Grafana under `build/monitoring/grafana` and
Prometheus under `build/monitoring/prometheus`, stripping the archive's top directory.
Expected binaries are `grafana/bin/grafana.exe` and `prometheus/prometheus.exe`.

The current Windows observer was exercised on Python 3.12.5, recorded in its audit
artifact; the locked Linux tests and offline corpus use Python 3.12.14. These are
separate runtime observations, not a claim that the observer used the exact core pin.

Create `build/monitoring/venv` using Windows Python 3.12. After creating it, pin its
installer to pip 26.2, then export and install the project's hash-locked dependencies
(the existing WSL environment can perform export):

```powershell
./build/monitoring/venv/Scripts/python.exe -m pip install --upgrade pip==26.2
uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file build/monitoring/requirements.txt
./build/monitoring/venv/Scripts/python.exe -m pip install --require-hashes -r build/monitoring/requirements.txt
```

The local audit found known advisories in the originally seeded pip 24.2. The
observer-only upgrade to 26.2 was independently re-audited successfully; the old
finding remains in the baseline evidence. This is an explicit installer-version
pin, not a generic latest upgrade. Do not run it against the acquisition environment.

Install the Windows-only broker bridge into that same observer environment:

```powershell
./build/monitoring/venv/Scripts/python.exe -m pip install -r deploy/broker-requirements.txt
```

This separate requirements file pins MetaTrader5 5.0.6162 and NumPy 2.5.2, matching
the verified acquisition environment. These are exact version pins, not a
hash-locked dependency export. They do not belong to the Linux data runtime, and
this command must not be pointed at the active acquisition environment.

The startup script supplies `PYTHONPATH=src`; it does not install a second copy of
the bot. The active MT5 acquisition environment is independent.

## Interpret Broker & Trades

The observer publishes real terminal account state, open positions, pending orders
and quotes. It reports the actual account kind (demo/live/contest); it does not
assume every connection is a demo. Manual and externally created activity is
included and remains `not_bot_certified`. Broker-reported profit is not validated
strategy performance. No endpoint can open, modify or close a trade.

The broker exporter listens only on `127.0.0.1:8766`. `/health` proves exporter
liveness, not terminal connectivity or a fresh account snapshot. `/api/status`
reports `snapshot_available`, `snapshot_stale`, last successful observation time,
`last_error`, `account_changed` and `ipc_poisoned`; `/metrics` feeds the charts. An unavailable or
stale snapshot must not be read as zero positions or zero profit. Account names,
identifiers, comments and credentials are not exposed in the published snapshot.

Polling is no faster than once every five seconds. The default terminal-read
timeout is ten seconds. After a read timeout the observer blocks further MT5 IPC
instead of starting overlapping calls. Check the terminal and logs, then use the
selective broker restart above. This restarts the observer, not the MT5 terminal
or the historical acquisition worker.

The observer privately pins the first successful account identity and checks it
around each snapshot. After an intentional account switch in MT5, restart only the
broker observer using the commands above. Until restarted, the previous account's
last observation remains explicitly stale; it is not silently relabelled as the
new account. Account identifiers remain private in memory and are never exported.
Exactly one already-running terminal must match the configured executable.

The broker HTTP endpoint rejects untrusted `Host` headers, but this is not user
authentication. Prometheus and Grafana remain unauthenticated, single-user-localhost
services. Do not expose them on a shared machine or network without a separate
authenticated deployment design.

## Historical-calendar clarification needed

The October 2024 source research found an unresolved gap between advertised weekly
opens and first observed EURUSD/GBPUSD ticks. A historical timestamp/session offset
and incomplete coverage are both possible; neither has been established. Preserve
the original tick bytes and do not approve the liquid-hours denominator from this
pattern alone. A repeatable rebuild does not prove the original UTC interpretation.

Draft questions for broker support (not sent automatically):

- Confirm the October 2024 meaning and UTC basis of MT5 Python tick `time` and
  `time_msc` for FBS-Demo, including whether any historical export/server offset applies.
- Provide dated EURUSD and GBPUSD quote sessions, weekly opens/closes, maintenance
  breaks and DST transitions for October 2024, identifying the relevant server.
- Supply instrument-specific FULL/PARTIAL/CLOSED dates and expected liquid intervals,
  or an authoritative dated schedule from which they can be reviewed. Explain the
  approximately 3h05 first-tick delay before October 27 and 1h53 on October 27 without
  assuming either missing ticks or a clock correction.

## Interpret Engineering Replay

The acquisition observer also reads `build/research/decision-replay/latest.json`
as discovery metadata, then validates the referenced completed report's exact path,
bytes, schema, identities, counts and non-trading safety claims. It does not run a
replay, import the strategy engine or read the detailed decision trace. Missing or
rejected reports stay unknown; they do not become zero bars or an approved strategy.

The first visible report contains 320 synthetic bars and 62 forecasts, 256 warmups,
2 suppressed decisions and zero abstentions. This tests engineering mechanics, not
market performance. The source-class panel explicitly distinguishes synthetic from
immutable-clean-snapshot evidence; both remain engineering-only. Forecasts are not
live calls, orders, calibrated probabilities or position sizes. See the core-engine
runbook for producer commands and provenance; the viewing surface never starts them.

## Interpret health and progress

- A running process can be revalidating immutable saved ticks before fetching more.
  The current probe does not emit a detailed resume-validation heartbeat. Therefore
  a flat checkpoint count does not by itself establish a stall.
- Process detection failure is UNKNOWN, not STOPPED. The exporter may need ordinary
  host process-query access when launched outside a restricted execution sandbox.
- Completed chunks include successful empty responses; usable history is a separate
  measure. The expected denominator comes from the selected frozen plan.
- Retrieval complete, observed structural checks, quality acceptance and human gate
  approval are different states. Unknown quality is never presented as clean.
- Saved reports can lag checkpoints. Their age and stale status are displayed.
- The overview's source-presence inventory is not a coverage test or a gate approval.

## Diagnose local services

Endpoints: acquisition exporter `/health`, `/api/status`, `/metrics` at port 8765;
broker observer at port 8766 (freshness semantics above); Prometheus
`/-/ready` and `/api/v1/targets` at port 9090; Grafana `/api/health` at port 3000.
All listeners bind to 127.0.0.1. Local Grafana access is anonymous Viewer only,
with login and initial admin creation disabled. Do not expose these ports remotely.

If a port belongs to another process, the launcher stops with an explanation.
Do not kill that process without identifying it. If a service fails, inspect its
timestamped stdout/stderr log. Fix the cause and rerun the launcher. Prometheus
retains 30 days of local metrics; losing those metrics does not delete source ticks.

The Grafana and Prometheus configurations and dashboard JSON are versioned in
`deploy/`. Checksum-pinned binary provenance is separate from the Python lockfile.

## Capture and export

Browser screenshots work with this installation. Grafana's server-rendered image
export requires an optional image renderer plugin/service, which is not installed;
an inactive image-export action is not a dashboard or data-source failure. Install
and configure a renderer separately only if server-side image export is needed.
