"""Read-only monitoring of bounded acquisition evidence, independent of the MT5 runner.

Only small, checksummed JSON checkpoints are read. Raw tick files are never opened,
and a dashboard observation is not a Gate-1 validation or a resume authorization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess  # nosec B404
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest

from tradebot.monitoring.research_status import add_research_metrics, research_status

PREFIX = "tradebot_acquisition_"
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_CHUNKS = 10_000
STRUCTURAL_FIELDS = (
    "timestamp_regressions",
    "time_field_mismatches",
    "bid_nonpositive",
    "ask_nonpositive",
    "crossed_quotes",
    "negative_volume",
    "negative_volume_real",
)
DIAGNOSTIC_FIELDS = (*STRUCTURAL_FIELDS, "locked_quotes", "exact_adjacent_duplicates")
PHASE_MODULES = (
    ("P0", "Foundations", ("core",)),
    ("P1", "Data and quality", ("data",)),
    ("P2", "Backtesting", ("backtest",)),
    ("P3", "Strategy research", ("features", "strategies", "portfolio")),
    ("P4", "Risk, execution and paper wiring", ("risk", "execution", "brokers")),
    ("P5", "Paper operations", ("live",)),
)


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("expected JSON object")
    return cast(dict[str, Any], value)


def _hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("expected finite nonnegative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("expected finite nonnegative number")
    return result


def _stamp(value: object) -> float:
    if not isinstance(value, str):
        raise ValueError("expected UTC timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("expected UTC timestamp")
    return parsed.timestamp()


def _safe_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value):
        raise ValueError("invalid plan identifier")
    return value


class JsonCache:
    """Bounded reads, cached by file identity; invalid changed files are not retained as valid."""

    def __init__(self) -> None:
        self._entries: dict[Path, tuple[int, int, int, dict[str, Any] | None]] = {}

    def read(self, path: Path) -> dict[str, Any] | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            self._entries.pop(path, None)
            return None
        previous = self._entries.get(path)
        identity = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        if previous is not None and previous[:3] == identity:
            if previous[3] is None:
                raise ValueError("invalid cached JSON")
            return previous[3]
        try:
            if stat.st_size > MAX_JSON_BYTES:
                raise ValueError("JSON exceeds monitoring size limit")
            with path.open("rb") as handle:
                data = handle.read(MAX_JSON_BYTES + 1)
            if len(data) > MAX_JSON_BYTES:
                raise ValueError("JSON exceeds monitoring size limit")
            payload = _object(json.loads(data))
        except (ValueError, UnicodeError):
            self._entries[path] = (*identity, None)
            raise ValueError("invalid checkpoint JSON") from None
        self._entries[path] = (*identity, payload)
        return payload


def _read_small_text(path: Path) -> str:
    with path.open("rb") as handle:
        data = handle.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise ValueError("file exceeds monitoring size limit")
    return data.decode("utf-8")


def platform_status(repository: Path, cache: JsonCache) -> dict[str, Any]:
    """Observe documented approval, configuration and source presence; never infer readiness."""
    try:
        spec_hash = hashlib.sha256(
            _read_small_text(repository / "docs/SPEC.md").encode()
        ).hexdigest()
    except (OSError, ValueError):
        spec_hash = None
    phases: list[dict[str, Any]] = []
    for phase, label, modules in PHASE_MODULES:
        count = 0
        readable = (repository / "src/tradebot").is_dir()
        try:
            for module in modules:
                count += sum(
                    path.name != "__init__.py"
                    for path in (repository / "src/tradebot" / module).glob("**/*.py")
                )
        except OSError:
            readable = False
        gate_path = repository / "docs/reports" / f"gate{phase[1:]}_evidence.md"
        approved = False
        spec_matches = False
        try:
            evidence = _read_small_text(gate_path)
            approved = bool(re.search(r"^Status:\s*\*\*APPROVED\*\*", evidence, re.MULTILINE))
            spec_matches = spec_hash is not None and spec_hash in evidence
        except (OSError, ValueError):
            pass
        state = 2 if approved and spec_matches else (1 if count else (0 if readable else -1))
        phases.append(
            {
                "phase": phase,
                "label": label,
                "state": state,
                "source_files": count,
                "gate_approval_recorded": approved,
                "gate_spec_matches": spec_matches,
            }
        )
    execution_enabled = -1
    try:
        live = _read_small_text(repository / "configs/env/live.yaml")
        # Observe this explicit scalar only. Missing/duplicate/unsupported values stay unknown.
        declarations = re.findall(r"^execution_enabled:\s*([^\r\n]*)", live, re.MULTILINE)
        if len(declarations) == 1:
            scalar = declarations[0].split("#", 1)[0].strip()
            if scalar in ("true", "false"):
                execution_enabled = int(scalar == "true")
    except (OSError, ValueError):
        pass
    demo_flags = dict.fromkeys(("execution_enabled", "pnl_reported", "costs_modelled"), -1)
    demo_class = "unavailable"
    try:
        manifest = cache.read(repository / "build/gate0/demo-manifest.json")
        if manifest is not None:
            demo_class = str(manifest.get("evidence_class", "unavailable"))
            for flag in demo_flags:
                if type(manifest.get(flag)) is bool:
                    demo_flags[flag] = int(manifest[flag])
    except (OSError, ValueError):
        pass
    return {
        "phases": phases,
        "execution_enabled": execution_enabled,
        "demo_flags": demo_flags,
        "demo_evidence_class": demo_class,
        "spec_sha256": spec_hash,
        "limitations": [
            "Source files indicate development, not implemented behavior or acceptance.",
            "Recorded approval describes the cited evidence, not the current working tree.",
            "Execution configuration is a declared setting, not account or broker state.",
        ],
    }


def gate1_status(report_path: Path, repository: Path, plan_hash: str) -> dict[str, Any]:
    """Read a checksum-published engineering report without promoting gate approval."""
    status: dict[str, Any] = {
        "report_state": 0,
        "report_timestamp_seconds": 0,
        "selected_days": -1,
        "selected_primary_ticks": -1,
        "independent_rebuilds_byte_identical": -1,
        "raw_files_unchanged": -1,
        "implementation_unchanged": -1,
        "report_code_current": -1,
        "reproducibility_recorded": -1,
        "tick_quality_state": -1,
        "calendar_state": -1,
        "calendar_unknown_days": -1,
        "liquid_hours_criterion_state": -1,
        "flags": {"causal": {}, "retrospective": {}},
        "scope": "no evidence",
        "gate_approval": "not asserted by monitoring",
    }
    try:
        content = _read_small_text(report_path)
    except FileNotFoundError:
        return status
    except (OSError, ValueError):
        status["report_state"] = 3
        return status
    status["report_state"] = 1
    checksum_path = report_path.with_name(report_path.stem + ".sha256.json")
    try:
        checksum = JsonCache().read(checksum_path)
        if checksum is None:
            return status
        if checksum.get(report_path.name) != hashlib.sha256(content.encode()).hexdigest():
            raise ValueError("report checksum mismatch")
        report = _object(json.loads(content))
        if report.get("schema_version") != 1:
            raise ValueError("unsupported Gate1 report schema")
        selection = _object(report.get("selection"))
        if selection.get("plan_hash") != plan_hash:
            raise ValueError("Gate1 report belongs to another acquisition plan")
        days = _number(selection.get("days"))
        primary_ticks = _number(report.get("selected_primary_ticks"))
        if not days.is_integer() or days < 1 or not primary_ticks.is_integer():
            raise ValueError("invalid sample counts")
        flags = (
            "independent_rebuilds_byte_identical",
            "raw_files_unchanged",
            "implementation_unchanged",
        )
        if any(type(report.get(flag)) is not bool for flag in flags):
            raise ValueError("missing measured reproducibility outcomes")
        satisfies = days >= 30 and all(report[flag] for flag in flags)
        result = report.get("reproducibility_status")
        if result not in ("PASSED", "NOT_SATISFIED") or (result == "PASSED") != satisfies:
            raise ValueError("reported reproducibility result contradicts its measurements")
        quality = report.get("quality")
        if not isinstance(quality, list) or not quality:
            raise ValueError("quality summaries missing")
        state_codes = {"INDETERMINATE": 0, "PASSED": 1, "FAILED": 2, "NOT_EVALUABLE": 3}
        tick_states: list[int] = []
        calendar_states: list[int] = []
        unknown_days: set[str] = set()
        counts: dict[str, dict[str, int]] = {"causal": {}, "retrospective": {}}
        for value in quality:
            summary = _object(value)
            tick_states.append(state_codes[str(summary["quality_status"])])
            calendar_states.append(state_codes[str(summary["calendar_status"])])
            missing = summary.get("calendar_days_missing")
            if not isinstance(missing, list):
                raise ValueError("calendar coverage missing")
            for missing_day in missing:
                unknown_days.add(date.fromisoformat(str(missing_day)).isoformat())
            for category, field_name in (
                ("causal", "flag_counts"),
                ("retrospective", "retrospective_flag_counts"),
            ):
                rows = summary.get(field_name)
                if not isinstance(rows, list) or len(rows) > 64:
                    raise ValueError("invalid diagnostic flag counts")
                for row in rows:
                    if not isinstance(row, list) or len(row) != 2:
                        raise ValueError("invalid diagnostic count entry")
                    flag_name, count = _safe_id(row[0]), _number(row[1])
                    if not count.is_integer():
                        raise ValueError("fractional diagnostic count")
                    counts[category][flag_name] = counts[category].get(flag_name, 0) + int(count)
        code = _object(selection.get("code_hashes"))
        if not code or len(code) > 1000:
            raise ValueError("missing or excessive implementation identity")
        current = True
        for relative, expected_hash in code.items():
            source = (repository / relative).resolve()
            source.relative_to(repository.resolve())
            try:
                current &= (
                    hashlib.sha256(_read_small_text(source).encode()).hexdigest() == expected_hash
                )
            except (OSError, ValueError):
                current = False
        criterion = report.get("liquid_hours_flagged_bar_criterion")
        # This report contract has no approved calendar/denominator evidence. A new
        # producer schema is required before monitoring can display criterion success.
        if not isinstance(criterion, str) or not criterion.startswith("INDETERMINATE:"):
            raise ValueError("unsupported liquid-hours acceptance evidence")

        def combine(values: list[int]) -> int:
            return 2 if 2 in values else (1 if all(value == 1 for value in values) else 0)

        status.update(
            {
                "report_state": 2,
                "report_timestamp_seconds": _stamp(report.get("finished_at")),
                "selected_days": int(days),
                "selected_primary_ticks": int(primary_ticks),
                **{flag: int(report[flag]) for flag in flags},
                "report_code_current": int(current),
                "reproducibility_recorded": int(result == "PASSED"),
                "tick_quality_state": combine(tick_states),
                "calendar_state": combine(calendar_states),
                "calendar_unknown_days": len(unknown_days),
                "liquid_hours_criterion_state": 0,
                "flags": counts,
                "scope": str(report.get("scope", "unspecified")),
            }
        )
    except (OSError, ValueError, KeyError, TypeError):
        status["report_state"] = 3
    return status


@dataclass(frozen=True)
class ExpectedChunk:
    symbol: str
    broker_symbol: str
    window: str
    session_date: str

    @property
    def identity(self) -> str:
        return f"{self.symbol}/{self.window}/{self.session_date}"


@dataclass(frozen=True)
class Plan:
    plan_hash: str
    source: str
    repeat_fetches: int
    chunks: tuple[ExpectedChunk, ...]


def read_plan(path: Path) -> Plan:
    """Read the v1 monitoring contract without importing frozen acquisition code."""
    payload = JsonCache().read(path)
    if payload is None or payload.get("schema_version") != 1 or payload.get("chunk_sessions") != 1:
        raise ValueError("monitoring requires a v1 single-session plan")
    if payload.get("purpose") != "source_viability_not_gate_evidence":
        raise ValueError("monitoring supports source-viability plans only")
    symbols = _object(payload.get("symbols"))
    windows = payload.get("windows")
    if not isinstance(windows, list) or not symbols or not windows:
        raise ValueError("plan must contain symbols and windows")
    chunks: list[ExpectedChunk] = []
    seen: set[str] = set()
    for symbol, broker_symbol in sorted(symbols.items()):
        for item in windows:
            window = _object(item)
            cursor = date.fromisoformat(str(window["start_session_date"]))
            end = date.fromisoformat(str(window["end_session_date_exclusive"]))
            if not 0 < (end - cursor).days <= MAX_CHUNKS:
                raise ValueError("invalid or excessive plan window")
            while cursor < end:
                if cursor.weekday() in (6, 0, 1, 2, 3):
                    chunk = ExpectedChunk(
                        _safe_id(symbol),
                        _safe_id(broker_symbol),
                        _safe_id(window["id"]),
                        cursor.isoformat(),
                    )
                    if chunk.identity in seen or len(chunks) >= MAX_CHUNKS:
                        raise ValueError("duplicate or excessive expected chunks")
                    seen.add(chunk.identity)
                    chunks.append(chunk)
                cursor += timedelta(days=1)
    repeats = payload.get("repeat_fetches")
    if type(repeats) is not int or repeats < 1:
        raise ValueError("invalid repeat count")
    canonical = {**payload, "session_timezone": "America/New_York", "session_open": "17:00"}
    return Plan(_hash(canonical), _safe_id(payload.get("source")), repeats, tuple(chunks))


def observe_process(work_dir: Path, repository: Path) -> str:
    """Match the runner and work directory; uncertain inspection never means stopped."""
    if os.name != "nt":
        return "unknown"
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        return "unknown"
    script = (
        "$ErrorActionPreference='Stop'; "
        "@(Get-CimInstance Win32_Process -Filter \"Name = 'python.exe' OR Name = 'pythonw.exe'\" "
        "| Select-Object CommandLine) | ConvertTo-Json -Compress"
    )
    try:
        # Fixed program/arguments; no user interpolation or shell=True; four-second timeout.
        result = subprocess.run(  # noqa: S603  # nosec B603
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            check=True,
            timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        records = json.loads(result.stdout or "[]")
        if not isinstance(records, list):
            records = [records]
        uncertain = False
        for record in records:
            command = _object(record).get("CommandLine")
            if command is None:
                uncertain = True
                continue
            if not isinstance(command, str) or "fbs_tick_continuity_probe.py" not in command:
                continue
            match = re.search(r'--work-dir(?:=|\s+)(?:"([^"]+)"|\x27([^\x27]+)\x27|(\S+))', command)
            if match is None:
                uncertain = True
                continue
            given = Path(next(value for value in match.groups() if value is not None))
            resolved = given if given.is_absolute() else repository / given
            if resolved.resolve() == work_dir.resolve():
                return "running"
        return "unknown" if uncertain else "stopped"
    except (OSError, ValueError, subprocess.SubprocessError):
        return "unknown"


@dataclass
class WindowStats:
    symbol: str
    window: str
    chunks_expected: int = 0
    chunks_completed: int = 0
    chunks_empty: int = 0
    ticks: int = 0
    active_minutes: int = 0
    max_gap_seconds: float | None = None
    fetch_seconds: float = 0
    fetch_rows: int = 0
    fetches: int = 0
    repeat_mismatches: int = 0
    fetch_errors: int = 0
    spread_p50: float | None = None
    spread_p95: float | None = None
    spread_p99: float | None = None
    diagnostics: dict[str, int] = field(default_factory=lambda: dict.fromkeys(DIAGNOSTIC_FIELDS, 0))


class AcquisitionMonitor:
    """Snapshot collector. All mutations are private cache state, never runner state."""

    def __init__(
        self,
        plan_path: Path,
        work_dir: Path,
        report: Path,
        repository: Path,
        *,
        process_observer: Callable[[], str] | None = None,
        cache_seconds: float = 5,
        gate1_report: Path | None = None,
        research_root: Path | None = None,
    ) -> None:
        self.plan = read_plan(plan_path)
        self.work_dir = work_dir.resolve()
        self.report = report.resolve()
        self.repository = repository.resolve()
        self.gate1_report = (
            gate1_report or (self.repository / "build/gate1/30day/report.json")
        ).resolve()
        self.research_root = research_root or (self.repository / "build/research/decision-replay")
        self.process_observer = process_observer or (
            lambda: observe_process(self.work_dir, self.repository)
        )
        self.cache_seconds = cache_seconds
        self.json = JsonCache()
        self._snapshot: dict[str, Any] | None = None
        self._next_refresh = 0.0
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            if self._snapshot is None or time.monotonic() >= self._next_refresh:
                self._snapshot = self._collect()
                self._next_refresh = time.monotonic() + self.cache_seconds
            return self._snapshot

    def _checkpoint(self, expected: ExpectedChunk) -> dict[str, Any] | None:
        path = (
            self.work_dir
            / self.plan.plan_hash
            / expected.symbol
            / expected.window
            / (f"{expected.session_date}.source-ticks.tsv.checkpoint.json")
        )
        payload = self.json.read(path)
        if payload is None:
            return None
        integrity = _object(payload.get("integrity"))
        unsigned = {key: value for key, value in payload.items() if key != "integrity"}
        if integrity.get("algorithm") != "sha256" or integrity.get("payload_sha256") != _hash(
            unsigned
        ):
            raise ValueError("checkpoint checksum mismatch")
        if (
            payload.get("schema_version") != 1
            or payload.get("plan_hash") != self.plan.plan_hash
            or payload.get("source") != self.plan.source
        ):
            raise ValueError("checkpoint plan identity mismatch")
        chunk = _object(payload.get("chunk"))
        for key, value in (
            ("chunk_id", expected.identity),
            ("logical_symbol", expected.symbol),
            ("broker_symbol", expected.broker_symbol),
            ("window_id", expected.window),
            ("session_date", expected.session_date),
        ):
            if chunk.get(key) != value:
                raise ValueError("checkpoint chunk identity mismatch")
        environment = _object(payload.get("environment"))
        if payload.get("environment_sha256") != _hash(environment):
            raise ValueError("checkpoint environment checksum mismatch")
        _stamp(payload.get("completed_at_utc"))
        metrics = _object(chunk.get("metrics"))
        for key in ("tick_count", "active_minutes", *DIAGNOSTIC_FIELDS):
            count_value = _number(metrics.get(key))
            if not count_value.is_integer():
                raise ValueError("fractional count")
        gap = metrics.get("max_intertick_gap_milliseconds")
        if gap is not None:
            _number(gap)
        spreads = metrics.get("positive_spread_counts")
        if not isinstance(spreads, list):
            raise ValueError("missing spread distribution")
        for row in spreads:
            if not isinstance(row, list) or len(row) != 2:
                raise ValueError("invalid spread distribution")
            if _number(row[0]) <= 0 or not _number(row[1]).is_integer():
                raise ValueError("invalid spread bin")
        fetches = payload.get("fetches")
        comparisons = payload.get("repeat_comparisons")
        if (
            not isinstance(fetches, list)
            or len(fetches) != self.plan.repeat_fetches
            or not isinstance(comparisons, list)
            or len(comparisons) != self.plan.repeat_fetches - 1
        ):
            raise ValueError("incomplete repeat evidence")
        for fetch in fetches:
            record = _object(fetch)
            for key in STRUCTURAL_FIELDS:
                _number(_object(record.get("metrics")).get(key))
            shape = _object(record.get("shape"))
            _number(shape.get("elapsed_seconds"))
            _number(record.get("tick_count"))
            for key in ("discarded_before_start", "discarded_after_end"):
                _number(shape.get(key))
            if type(_object(shape.get("mt5_error_snapshot")).get("code")) is not int:
                raise ValueError("missing request outcome")
        for comparison in comparisons:
            if type(_object(comparison).get("identical")) is not bool:
                raise ValueError("missing repeat comparison verdict")
        return payload

    def _collect(self) -> dict[str, Any]:
        now = datetime.now(UTC).timestamp()
        windows: dict[tuple[str, str], WindowStats] = {}
        spreads_by_window: dict[tuple[str, str], dict[Decimal, int]] = {}
        valid: list[dict[str, Any]] = []
        invalid = 0
        for expected in self.plan.chunks:
            group = windows.setdefault(
                (expected.symbol, expected.window), WindowStats(expected.symbol, expected.window)
            )
            group.chunks_expected += 1
            try:
                checkpoint = self._checkpoint(expected)
            except (OSError, ValueError, KeyError, TypeError):
                invalid += 1
                continue
            if checkpoint is None:
                continue
            valid.append(checkpoint)
            metrics = _object(_object(checkpoint["chunk"])["metrics"])
            group.chunks_completed += 1
            group.chunks_empty += int(metrics["tick_count"] == 0)
            group.ticks += int(metrics["tick_count"])
            group.active_minutes += int(metrics["active_minutes"])
            bins = spreads_by_window.setdefault((expected.symbol, expected.window), {})
            for spread, count in metrics["positive_spread_counts"]:
                price = Decimal(str(spread))
                bins[price] = bins.get(price, 0) + int(count)
            gap = metrics.get("max_intertick_gap_milliseconds")
            if gap is not None:
                group.max_gap_seconds = max(group.max_gap_seconds or 0, _number(gap) / 1000)
            for key in DIAGNOSTIC_FIELDS:
                group.diagnostics[key] += int(metrics[key])
            for fetch in checkpoint["fetches"]:
                shape = fetch["shape"]
                group.fetches += 1
                group.fetch_seconds += _number(shape["elapsed_seconds"])
                group.fetch_rows += int(fetch["tick_count"])
                group.fetch_errors += int(shape["mt5_error_snapshot"]["code"] != 1)
            group.repeat_mismatches += sum(
                not item["identical"] for item in checkpoint["repeat_comparisons"]
            )
        stats = list(windows.values())
        for window_key, bins in spreads_by_window.items():
            total = sum(bins.values())
            if total:
                for attribute, quantile in (
                    ("spread_p50", 0.5),
                    ("spread_p95", 0.95),
                    ("spread_p99", 0.99),
                ):
                    rank, cumulative = math.ceil(total * quantile), 0
                    for spread, count in sorted(bins.items()):
                        cumulative += count
                        if cumulative >= rank:
                            setattr(windows[window_key], attribute, float(spread))
                            break
        latest = max((_stamp(item["completed_at_utc"]) for item in valid), default=0)
        structural_anomalies = sum(
            int(fetch["metrics"][key])
            for item in valid
            for fetch in item["fetches"]
            for key in STRUCTURAL_FIELDS
        ) + sum(
            int(fetch["shape"][key])
            for item in valid
            for fetch in item["fetches"]
            for key in ("discarded_before_start", "discarded_after_end")
        )
        ticks = sum(item.ticks for item in stats)
        structural_state = (
            "anomalies"
            if structural_anomalies
            else ("clean_observed" if ticks > 0 and not invalid else "unknown")
        )
        environments = {_hash(_object(item["environment"])) for item in valid}
        current_hashes: dict[str, str] = {}
        for key, relative in (
            ("runner_sha256", "scripts/fbs_tick_continuity_probe.py"),
            ("analysis_module_sha256", "src/tradebot/data/acquisition_probe.py"),
            ("spec_sha256", "docs/SPEC.md"),
        ):
            try:
                current_hashes[key] = hashlib.sha256(
                    (self.repository / relative).read_bytes()
                ).hexdigest()
            except OSError:
                pass
        identity_current = (
            bool(valid)
            and len(current_hashes) == 3
            and len(environments) == 1
            and all(
                all(item["environment"].get(key) == value for key, value in current_hashes.items())
                for item in valid
            )
        )
        report, report_invalid = self._latest_report()
        report_stamp = _stamp(report["completed_at_utc"]) if report else 0
        report_chunks = (
            int(_object(report.get("dataset", {})).get("observed_chunks", 0)) if report else 0
        )
        report_stale = not report or report_stamp < latest or report_chunks != len(valid)
        process_state = self.process_observer()
        if process_state not in ("running", "stopped", "unknown"):
            process_state = "unknown"
        completed = len(valid) == len(self.plan.chunks) and invalid == 0
        return {
            "schema_version": 1,
            "observed_at_utc": datetime.fromtimestamp(now, UTC).isoformat(),
            "snapshot_timestamp_seconds": now,
            "source": self.plan.source,
            "plan_hash": self.plan.plan_hash,
            "process_state": process_state,
            "run_state": (
                2
                if completed and process_state == "stopped"
                else {"unknown": -1, "stopped": 0, "running": 1}[process_state]
            ),
            "process_stage": "not_reported",
            "chunks_expected": len(self.plan.chunks),
            "chunks_completed": len(valid),
            "chunks_empty": sum(item.chunks_empty for item in stats),
            "checkpoints_invalid": invalid,
            "ticks": ticks,
            "active_minutes": sum(item.active_minutes for item in stats),
            "last_checkpoint_timestamp_seconds": latest,
            "report_timestamp_seconds": report_stamp,
            "report_chunks": report_chunks,
            "report_stale": bool(report_stale),
            "report_invalid": report_invalid,
            "retrieval_complete": completed,
            "structural_state": structural_state,
            "structural_anomalies": structural_anomalies,
            "quality_indeterminate": True,
            "cross_source_not_evaluable": True,
            "evidence_identity_current": identity_current,
            "evidence_environment_count": len(environments),
            "fetch_errors": sum(item.fetch_errors for item in stats),
            "reported_failure": bool(
                report and report.get("failure", {}).get("kind") not in (None, "PLANNED_STOP")
            ),
            "report_status": report.get("status", "MISSING") if report else "MISSING",
            "windows": [asdict(item) for item in stats],
            "platform": platform_status(self.repository, self.json),
            "gate1": gate1_status(self.gate1_report, self.repository, self.plan.plan_hash),
            "research": research_status(self.research_root, self.repository),
            "limitations": [
                "Source-viability monitoring only; no trading or Gate-1 acceptance claim.",
                "OS process activity and checkpoint freshness are separate observations.",
                "Stage is not reported; unchanged checkpoints can reflect resume verification.",
                "Completed empty chunks count as retrieved, not as usable history.",
                "Only JSON checksums are checked; raw tick files are not scanned or verified.",
                "Clean observed means no within-fetch anomalies in available nonempty evidence.",
                "Cross-session structural checks await the completed evidence review.",
                "Quality awaits the expected-liquidity calendar and approved thresholds.",
            ],
        }

    def _latest_report(self) -> tuple[dict[str, Any] | None, int]:
        reports: list[dict[str, Any]] = []
        invalid = 0
        partial = self.report.with_name(f"{self.report.stem}.partial{self.report.suffix}")
        for path in (self.report, partial):
            try:
                value = self.json.read(path)
                if value is None:
                    continue
                if _object(value.get("plan")).get("plan_hash") != self.plan.plan_hash:
                    raise ValueError("report belongs to another plan")
                _stamp(value.get("completed_at_utc"))
                _number(_object(value.get("dataset")).get("observed_chunks"))
                reports.append(value)
            except (OSError, ValueError, KeyError, TypeError):
                invalid += 1
        return max(
            reports, key=lambda item: _stamp(item["completed_at_utc"]), default=None
        ), invalid


def render_metrics(snapshot: Mapping[str, Any]) -> bytes:
    """Expose bounded labels and numeric observations; identities remain in the JSON view."""
    registry = CollectorRegistry()
    scalar_names = (
        "run_state",
        "snapshot_timestamp_seconds",
        "chunks_expected",
        "chunks_completed",
        "chunks_empty",
        "checkpoints_invalid",
        "ticks",
        "active_minutes",
        "last_checkpoint_timestamp_seconds",
        "report_timestamp_seconds",
        "report_chunks",
        "report_stale",
        "report_invalid",
        "retrieval_complete",
        "structural_anomalies",
        "quality_indeterminate",
        "cross_source_not_evaluable",
        "evidence_identity_current",
        "evidence_environment_count",
        "fetch_errors",
        "reported_failure",
    )
    for name in scalar_names:
        Gauge(PREFIX + name, f"Acquisition {name.replace('_', ' ')}.", registry=registry).set(
            float(snapshot[name])
        )
    for name, states in (
        ("process_state", ("running", "stopped", "unknown")),
        ("structural_state", ("unknown", "clean_observed", "anomalies")),
    ):
        gauge = Gauge(PREFIX + name, f"Observed {name} (one-hot).", ("state",), registry=registry)
        for state in states:
            gauge.labels(state).set(int(snapshot[name] == state))
    names = (
        "chunks_expected",
        "chunks_completed",
        "chunks_empty",
        "ticks",
        "active_minutes",
        "max_gap_seconds",
        "fetch_seconds",
        "fetch_rows",
        "fetches",
        "repeat_mismatches",
        "fetch_errors",
        "spread_p50",
        "spread_p95",
        "spread_p99",
    )
    for name in names:
        gauge = Gauge(
            PREFIX + "window_" + name,
            f"Window {name.replace('_', ' ')}.",
            ("symbol", "window"),
            registry=registry,
        )
        for window in snapshot["windows"]:
            if window[name] is not None:
                gauge.labels(window["symbol"], window["window"]).set(window[name])
    diagnostics = Gauge(
        PREFIX + "window_diagnostic_rows",
        "Primary-fetch diagnostic row counts.",
        ("symbol", "window", "kind"),
        registry=registry,
    )
    for window in snapshot["windows"]:
        for kind, value in window["diagnostics"].items():
            diagnostics.labels(window["symbol"], window["window"], kind).set(value)
    platform = snapshot["platform"]
    phase_state = Gauge(
        "tradebot_platform_phase_state",
        "0=no source, 1=source present, 2=approval recorded, -1=unknown.",
        ("phase", "label"),
        registry=registry,
    )
    phase_files = Gauge(
        "tradebot_platform_phase_source_files",
        "Implementation source-file inventory; not readiness.",
        ("phase", "label"),
        registry=registry,
    )
    gate = Gauge(
        "tradebot_platform_gate_approval_recorded",
        "Approval text recorded in gate evidence.",
        ("phase",),
        registry=registry,
    )
    for phase in platform["phases"]:
        phase_state.labels(phase["phase"], phase["label"]).set(phase["state"])
        phase_files.labels(phase["phase"], phase["label"]).set(phase["source_files"])
        gate.labels(phase["phase"]).set(int(phase["gate_approval_recorded"]))
    Gauge(
        "tradebot_platform_execution_enabled",
        "Declared live execution flag: -1 unknown, 0 false, 1 true.",
        registry=registry,
    ).set(platform["execution_enabled"])
    demo = Gauge(
        "tradebot_platform_demo_flag",
        "Flags observed in the synthetic demo manifest; -1 unknown.",
        ("flag",),
        registry=registry,
    )
    for flag, value in platform["demo_flags"].items():
        demo.labels(flag).set(value)
    gate1 = snapshot["gate1"]
    for name in (
        "report_state",
        "report_timestamp_seconds",
        "selected_days",
        "selected_primary_ticks",
        "independent_rebuilds_byte_identical",
        "raw_files_unchanged",
        "implementation_unchanged",
        "report_code_current",
        "reproducibility_recorded",
        "tick_quality_state",
        "calendar_state",
        "calendar_unknown_days",
        "liquid_hours_criterion_state",
    ):
        Gauge(
            "tradebot_gate1_" + name,
            f"Gate1 report {name.replace('_', ' ')}; -1 means unobserved.",
            registry=registry,
        ).set(gate1[name])
    flag_rows = Gauge(
        "tradebot_gate1_flag_rows",
        "Measured causal or retrospective diagnostic counts.",
        ("category", "flag"),
        registry=registry,
    )
    for category, counts in gate1["flags"].items():
        for flag, count in counts.items():
            flag_rows.labels(category, flag).set(count)
    add_research_metrics(registry, snapshot["research"])
    return generate_latest(registry)


def make_handler(monitor: AcquisitionMonitor) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/health":
                body, content_type = b'{"status":"ok","scope":"exporter_only"}', "application/json"
            elif path in ("/metrics", "/api/status"):
                try:
                    snapshot = monitor.snapshot()
                    body = (
                        render_metrics(snapshot)
                        if path == "/metrics"
                        else json.dumps(snapshot, allow_nan=False).encode()
                    )
                except (OSError, ValueError, KeyError, TypeError):
                    self.send_error(503, "Monitoring snapshot unavailable")
                    return
                content_type = CONTENT_TYPE_LATEST if path == "/metrics" else "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument(
        "--plan", type=Path, default=Path("configs/probes/fbs_tick_continuity_v1.json")
    )
    parser.add_argument("--work-dir", type=Path, default=Path("build/fbs-tick-continuity-v1"))
    parser.add_argument(
        "--report", type=Path, default=Path("docs/reports/fbs-tick-continuity-v1-candidate.json")
    )
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--gate1-report", type=Path, default=Path("build/gate1/30day/report.json"))
    parser.add_argument(
        "--research-root", type=Path, default=Path("build/research/decision-replay")
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    repository = args.repository.resolve()
    paths = [
        value if value.is_absolute() else repository / value
        for value in (args.plan, args.work_dir, args.report)
    ]
    gate1_report = (
        args.gate1_report if args.gate1_report.is_absolute() else repository / args.gate1_report
    )
    research_root = (
        args.research_root if args.research_root.is_absolute() else repository / args.research_root
    )
    monitor = AcquisitionMonitor(
        paths[0],
        paths[1],
        paths[2],
        repository,
        gate1_report=gate1_report,
        research_root=research_root,
    )
    server = ThreadingHTTPServer((args.host, args.port), make_handler(monitor))
    print(f"Read-only acquisition metrics at http://{args.host}:{args.port}/metrics", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
