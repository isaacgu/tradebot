"""Interpretable momentum engineering candidate; emits forecasts, never orders."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from statistics import fmean

from tradebot.core.ports import StrategyContext
from tradebot.core.timestamps import require_utc
from tradebot.core.types import Bar, Forecast, VolumeKind
from tradebot.features.causal import FeatureConfig, FeatureSnapshot, compute_features, validate_bars


@dataclass(frozen=True, slots=True)
class MomentumConfig:
    """Engineering parameters; scale 10 is uncalibrated and carries no risk budget."""

    features: FeatureConfig = field(default_factory=FeatureConfig)
    forecast_scale: float = 10.0

    def __post_init__(self) -> None:
        if not isinstance(self.features, FeatureConfig):
            raise TypeError("features must be FeatureConfig")
        if (
            type(self.forecast_scale) is not float
            or not math.isfinite(self.forecast_scale)
            or self.forecast_scale <= 0
        ):
            raise ValueError("forecast_scale must be a positive finite float")


@dataclass(frozen=True, slots=True)
class MomentumDecision:
    """Explanation for one bar: warmup, suppressed, abstain, or forecast."""

    status: str
    reason: str
    features: FeatureSnapshot | None = None
    forecast: Forecast | None = None


_DEFAULT_MOMENTUM_CONFIG = MomentumConfig()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _bar_state(bar: Bar) -> dict[str, object]:
    return {
        "instrument": bar.instrument,
        "ts_open": bar.ts_open.isoformat(),
        "ts_event": bar.ts_event.isoformat(),
        "ts_recv": bar.ts_recv.isoformat(),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": bar.volume,
        "volume_kind": bar.volume_kind.value if bar.volume_kind is not None else None,
        "spread_mean": str(bar.spread_mean) if bar.spread_mean is not None else None,
        "n_ticks": bar.n_ticks,
        "quality_flags": list(bar.quality_flags),
    }


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("checkpoint field must be a string")
    return value


def _optional_int(value: object) -> int | None:
    if value is not None and type(value) is not int:
        raise ValueError("checkpoint integer field must be int or null")
    return value


def _read_bar(value: object) -> Bar:
    if not isinstance(value, dict) or set(value) != {
        "instrument",
        "ts_open",
        "ts_event",
        "ts_recv",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "volume_kind",
        "spread_mean",
        "n_ticks",
        "quality_flags",
    }:
        raise ValueError("invalid checkpoint bar schema")
    flags = value["quality_flags"]
    if not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags):
        raise ValueError("checkpoint flags must be a string list")
    return Bar(
        instrument=_string(value["instrument"]),
        ts_open=datetime.fromisoformat(_string(value["ts_open"])),
        ts_event=datetime.fromisoformat(_string(value["ts_event"])),
        ts_recv=datetime.fromisoformat(_string(value["ts_recv"])),
        open=Decimal(_string(value["open"])),
        high=Decimal(_string(value["high"])),
        low=Decimal(_string(value["low"])),
        close=Decimal(_string(value["close"])),
        volume=_optional_int(value["volume"]),
        volume_kind=VolumeKind(_string(value["volume_kind"]))
        if value["volume_kind"] is not None
        else None,
        spread_mean=Decimal(_string(value["spread_mean"]))
        if value["spread_mean"] is not None
        else None,
        n_ticks=_optional_int(value["n_ticks"]),
        quality_flags=tuple(flags),
    )


class MomentumStrategy:
    """Equal mean of trailing log returns / (sample vol * sqrt(observed bars)).

    Every flag, including an unknown flag, or absent spread resets warmup. This
    deliberately strict engineering policy is not a calibrated trading filter.
    """

    id = "momentum_engineering_v1"

    def __init__(self, instrument: str, config: MomentumConfig = _DEFAULT_MOMENTUM_CONFIG) -> None:
        if not isinstance(instrument, str) or not instrument.strip():
            raise ValueError("instrument must be a nonempty string")
        if not isinstance(config, MomentumConfig):
            raise TypeError("config must be MomentumConfig")
        self._instrument = instrument
        self._config = config
        self._history: tuple[Bar, ...] = ()
        self._last_bar: Bar | None = None
        self._known_at: datetime | None = None
        self._last_decision = MomentumDecision("warmup", "insufficient_history")

    @property
    def instruments(self) -> tuple[str, ...]:
        """Return the single subscribed instrument."""
        return (self._instrument,)

    @property
    def warmup_bars(self) -> int:
        """Return complete clean bars required after startup or a quality reset."""
        return self._config.features.required_bars

    @property
    def last_decision(self) -> MomentumDecision:
        """Return the latest immutable engineering decision explanation."""
        return self._last_decision

    def _decide(self, history: Sequence[Bar], bar: Bar, now: datetime) -> MomentumDecision:
        if bar.quality_flags:
            return MomentumDecision("suppressed", "quality_flags")
        if bar.spread_mean is None:
            return MomentumDecision("suppressed", "missing_spread")
        if len(history) < self.warmup_bars:
            return MomentumDecision("warmup", "insufficient_history")
        features = compute_features(history, known_at=now, config=self._config.features)
        if features.volatility <= 0:
            return MomentumDecision("abstain", "zero_volatility", features)
        components = tuple(
            (horizon, value / (features.volatility * math.sqrt(horizon)))
            for horizon, value in features.log_returns
        )
        if any(not math.isfinite(value) for _, value in components):
            raise ValueError("momentum components must be finite")
        raw_forecast = self._config.forecast_scale * fmean(value for _, value in components)
        if not math.isfinite(raw_forecast):
            raise ValueError("momentum forecast must be finite before clipping")
        forecast = Forecast(
            strategy_id=self.id,
            instrument=self._instrument,
            ts_event=now,
            ts_recv=now,
            value=max(-20.0, min(20.0, raw_forecast)),
            source_event_id=hashlib.sha256(_canonical(_bar_state(bar)).encode()).hexdigest(),
            confidence=None,
            meta=(
                ("evidence_class", "engineering_decision_replay"),
                ("forecast_scale_calibrated", "false"),
                ("forecast_scale", str(self._config.forecast_scale)),
                ("raw_forecast", str(raw_forecast)),
                ("components", _canonical(components)),
                ("horizon_basis", "observed_bars"),
                ("bar_close", bar.ts_close.isoformat()),
                ("gap_seconds", str(features.gap_seconds)),
            ),
        )
        return MomentumDecision("forecast", "momentum", features, forecast)

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> Sequence[Forecast]:
        """Admit one known closed bar and emit a unitless [-20, 20] forecast if ready."""
        now = require_utc(ctx.clock.now(), field="decision time")
        if bar.instrument != self._instrument:
            raise ValueError("unexpected instrument")
        validate_bars((bar,) if self._last_bar is None else (self._last_bar, bar), known_at=now)
        if self._known_at is not None and now < self._known_at:
            raise ValueError("decision clock cannot move backwards")
        history = (
            ()
            if bar.quality_flags or bar.spread_mean is None
            else (*self._history, bar)[-self.warmup_bars :]
        )
        decision = self._decide(history, bar, now)
        # Commit only after every admission, calculation and event construction succeeded.
        self._history, self._last_bar, self._known_at = history, bar, now
        self._last_decision = decision
        return () if decision.forecast is None else (decision.forecast,)

    def state(self) -> Mapping[str, object]:
        """Return a JSON-compatible version/config-bound checkpoint with exact prices."""
        return {
            "version": 1,
            "instrument": self._instrument,
            "config": json.loads(_canonical(asdict(self._config))),
            "history": [_bar_state(bar) for bar in self._history],
            "last_bar": _bar_state(self._last_bar) if self._last_bar is not None else None,
            "known_at": self._known_at.isoformat() if self._known_at is not None else None,
        }

    def restore(self, state: Mapping[str, object]) -> None:
        """Validate and atomically restore a checkpoint; no caller-owned data is retained."""
        if set(state) != {"version", "instrument", "config", "history", "last_bar", "known_at"}:
            raise ValueError("invalid checkpoint schema")
        if type(state["version"]) is not int or state["version"] != 1:
            raise ValueError("unsupported checkpoint version")
        if state["instrument"] != self._instrument:
            raise ValueError("checkpoint instrument mismatch")
        if _canonical(state["config"]) != _canonical(asdict(self._config)):
            raise ValueError("checkpoint config mismatch")
        raw_history = state["history"]
        if not isinstance(raw_history, list) or len(raw_history) > self.warmup_bars:
            raise ValueError("checkpoint history must be a bounded list")
        history = tuple(_read_bar(value) for value in raw_history)
        last = _read_bar(state["last_bar"]) if state["last_bar"] is not None else None
        now = (
            datetime.fromisoformat(_string(state["known_at"]))
            if state["known_at"] is not None
            else None
        )
        if last is None:
            if history or now is not None:
                raise ValueError("empty checkpoint cannot contain history or decision time")
            decision = MomentumDecision("warmup", "insufficient_history")
        else:
            if now is None or last.instrument != self._instrument:
                raise ValueError("checkpoint last bar or decision time mismatch")
            validate_bars(history, known_at=now)
            validate_bars((last,), known_at=now)
            if any(bar.quality_flags or bar.spread_mean is None for bar in history):
                raise ValueError("checkpoint history contains unusable bars")
            if history:
                if history[-1] != last:
                    raise ValueError("checkpoint history/last-bar mismatch")
            elif not last.quality_flags and last.spread_mean is not None:
                raise ValueError("clean last bar cannot have empty history")
            decision = self._decide(history, last, now)
        self._history, self._last_bar, self._known_at = history, last, now
        self._last_decision = decision
