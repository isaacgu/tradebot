# ADR-0016: Training-only forecast calibration

Status: implemented engineering candidate, 2026-10-15. No new gate, dataset
release, economic experiment, or human approval is asserted.

## Scope

The requested fitting pipeline needs an actual estimator, not another replay of
the fixed uncalibrated multiplier. Add a one-instrument, one-scalar calibrator for
the existing S1 engineering momentum formula. No ML dependency or parameter
search is introduced. The original decision replay retains its uncalibrated
label; it does not silently start consuming newly fitted artifacts.

The frozen SPEC v1.0 and data thresholds are unchanged. Failed FBS evidence is
not made eligible by implementing this code.

## Definition and interpretation

Raw strength is the equal mean of horizon log returns divided by sample one-bar
volatility times the square root of each observed-bar horizon. The strategy and
fitter share `momentum_components`; fitting never tries to invert a clipped
forecast. On training observations only:

```text
scale = 10 / arithmetic_mean(abs(raw_strength))
validation_forecast = clip(scale * raw_strength, -20, +20)
```

The target of 10 applies before capping. The post-cap mean and clipping count are
measured separately; the report does not claim they equal 10. This is the simple
mean-based interpretation of SPEC 5.4, not a tuned performance optimum. Carver's
[primary discussion of scalar estimation](https://qoppac.blogspot.com/2016/01/pysystemtrader-estimated-forecast.html)
describes normalization from absolute raw forecasts, and also discusses median
estimation, pooling and sample-length risks. Those alternatives are not adopted
or optimized here; no backfilling of a future scalar is permitted.

At least two nonconstant finite observations are needed for this engineering
estimator. That is an arithmetic validity check, **not** sufficient statistical
power, adequate history or a research acceptance criterion. All-zero, constant,
nonfinite, overflowing-scale and insufficient inputs fail without a default.

## Temporal boundary and source authority

`fit_replay` requires the existing guard-owned snapshot for real venues and the
exact `STRATEGY_TRAINING` purpose. Frozen validation requires the separate
`ECONOMIC_EVALUATION` purpose. Dataset and selected-file manifests must match the
approved stream. No new purpose, release, trusted registry or gate bypass exists.

Chronological routing occurs inside the authorized replay consumer. Only the
selected training/validation partition reaches a freshly initialized strategy.
Unselected rows still have source, sequence, timeframe, availability and interval
checks; the consumer requests true EOF and verifies the full snapshot. An invalid
unselected tail therefore invalidates the fit. Decision time, not market close,
is used for the boundary and declared horizon purge. The embargo remains at
least that horizon. No outcomes or labels enter this unsupervised scalar.

The input reader may inspect and hash unselected bytes and routing timestamps.
This is not a claim that a market lockbox is unseen. Lockbox rows never reach
the strategy or estimator, and this pipeline exposes no lockbox execution option.

Every flag still suppresses forecasts, including `TS_RECV_IMPUTED`; the flags are
not stripped. Historical provenance-vs-defect semantics remain a separate policy
question. All-suppressed historical FBS data will fail fitting rather than train
on an invented replacement sample or silently adopt a new availability policy.

## Artifact lifecycle

Each fit binds instrument, dataset, selected-file manifest, full replay config,
producer implementation, exact split, training observation hash, sample count and
decision range. `publish_calibration` creates canonical JSON in a content-addressed
directory and never overwrites different bytes. `load_calibration` requires a
caller-pinned SHA-256, bounded canonical schema and internally coherent numbers.
This detects changes; it does not authenticate a human or create permission.

Validation loads the saved artifact before accessing data and rejects changed
splits, configuration, instrument, dataset or implementation. It checks the
artifact again at completion. It applies the frozen scalar only; there is no
refitting, parameter ranking or outcome-based selection.

The fixed `calibration_demo` CLI accepts only invented fixture data. It records a
declaration and STARTED attempt before fitting, preserves failures, persists each
fit before validation, and publishes a deterministic report. It reports the
measured training and validation counts, both fitted artifact hashes, zero
market-data models and zero financial trials. The report preserves source
provenance and release/registry fields (null for these synthetic fixtures).

## Remaining acceptance work

The estimator is not an end-to-end financial research system. Full portfolio
sizing and risk/OMS integration, broker-backed execution costs, statistical
validation, a reviewed economic hypothesis, accepted deep-history/stress inputs
and an artifact-bound data release remain necessary. There is no continuous
training service, model promotion or live trading action in this change.

The current reference month remains a numerical failure, and no production
trusted release registry is installed. Passing these software tests is not the
condition that permits real-data training: the existing data and phase gates
must genuinely pass too.
