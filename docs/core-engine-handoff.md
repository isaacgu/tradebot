# Core engine handoff

Assignment: begin core-engine engineering while data evidence continues collecting;
coordinate with the active UI task. SPEC v1.0 SHA-256
`dccdcbd9a237009116b4b3219860f371a3bc51700f20b1199746479921689f37`.

The first implementation is a deterministic decision replay, documented in
[the runbook](runbooks/core-engine.md) and [ADR-0010](adr/0010-causal-decision-replay.md).
It adds isolated FX feature/strategy state, immutable clean-bar snapshot reading,
causal event-bus delivery, checkpoint validation and immutable decision artifacts.
Its momentum parameters are uncalibrated engineering settings. No economic strategy
evaluation, cost modelling, fills, PnL, order submission or approval is claimed.

Coordination established with Codex task **Review bot progress and GUI**
(`01a06dc2-f007-7c31-9ecf-2f1989530173`). That task owns P1 data, Gate-1 evidence,
exporters and Grafana. New engine work was staged under ignored
`build/core-engine-staging` while its verification snapshot ran. Its acquisition,
core/data implementations, lockfile, SPEC and resume-bound HEAD remain untouched.

Assumptions: the Principal's request authorizes engineering preparation in parallel
with P1, without self-certifying a gate or beginning strategy selection. Synthetic
tests can prove mechanics while actual data acceptance remains outstanding.
Any later treatment of provenance-only flags needs an explicit documented research
policy; the current candidate suppresses all flags. Economic calendar, macro,
cross-asset and tick features are not yet connected to forecasts.

The next owner should connect the verified report contract to the UI, then implement
the cost/fill harness after Gate-1 acceptance. The pending multi-year strategy
history requirement and all financial/statistical gates still apply.

## Verification and UI handoff

Canonical publication and integration are complete. The full local suite passed
689 tests with 88.76% overall coverage; both core/non-core coverage tiers passed.
Strict mypy passed all 80 source/test/script files. Ruff format/check and Bandit
passed on new engine code. The 125 focused tests include full Parquet-to-command
integration and independent adversarial review. Two canonical synthetic command
runs produced identical report and trace hashes. Exact identities and commands are
recorded in [the engineering verification report](reports/core-engine-engineering.md).

The UI task received the report path, schema and hashes, and is implementing its
own Engineering Replay view. The core report contract is frozen for that consumer.
No commit was made while the acquisition/rebuild owner preserves resume identity.
The core task made no changes to the existing frozen core/data implementations,
probe scripts, dependency lock, top-level package initialization or specification.
