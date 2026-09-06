from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]
DASHBOARD_ROOT = REPOSITORY / "deploy/grafana/dashboards"
CURRENT_DASHBOARDS = ("acquisition", "system", "data-quality", "research")


def dashboard(name: str) -> dict[str, Any]:
    return json.loads((DASHBOARD_ROOT / f"{name}.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def panel_for_metric(name: str, expression: str) -> dict[str, Any]:
    matches = [
        panel
        for panel in dashboard(name)["panels"]
        if any(target.get("expr") == expression for target in panel.get("targets", []))
        and panel["type"] in ("stat", "bargauge")
    ]
    assert len(matches) == 1
    return matches[0]  # type: ignore[no-any-return]


def displayed_color(panel: dict[str, Any], value: int) -> str:
    """Resolve the mapping/threshold paths Grafana uses for these numeric stats."""
    defaults = panel["fieldConfig"]["defaults"]
    for mapping in defaults["mappings"]:
        if mapping["type"] == "value" and str(value) in mapping["options"]:
            return str(mapping["options"][str(value)]["color"])
    assert defaults["color"]["mode"] == "thresholds"
    color = ""
    for step in defaults["thresholds"]["steps"]:
        if step["value"] is None or value >= step["value"]:
            color = step["color"]
    return color


@pytest.mark.parametrize("name", CURRENT_DASHBOARDS)
def test_current_snapshot_panels_cannot_retain_a_range_history_value(name: str) -> None:
    panels = [panel for panel in dashboard(name)["panels"] if panel["type"] in ("stat", "bargauge")]
    assert panels
    for panel in panels:
        assert panel["options"]["reduceOptions"]["calcs"] == ["last"]
        assert panel["fieldConfig"]["defaults"]["noValue"]
        for target in panel["targets"]:
            assert target["instant"] is True
            assert target["range"] is False
            # Missing series must remain missing instead of becoming a healthy zero.
            assert "or vector(0)" not in target["expr"]


def test_corpus_history_remains_an_explicit_range_query() -> None:
    history = [p for p in dashboard("acquisition")["panels"] if p["type"] == "timeseries"]
    assert len(history) == 1
    assert history[0]["targets"][0]["expr"] == "tradebot_acquisition_ticks"
    assert history[0]["targets"][0]["instant"] is False
    assert history[0]["targets"][0]["range"] is True


@pytest.mark.parametrize(
    ("name", "expression", "expected_color"),
    [
        ("acquisition", "tradebot_acquisition_chunks_empty", "orange"),
        ("system", "tradebot_acquisition_chunks_empty", "orange"),
        ("acquisition", "tradebot_acquisition_checkpoints_invalid", "red"),
        ("acquisition", "tradebot_acquisition_fetch_errors", "red"),
        ("acquisition", "tradebot_acquisition_window_repeat_mismatches", "orange"),
        ("data-quality", "tradebot_gate1_calendar_unknown_days", "orange"),
        (
            "acquisition",
            'sum(tradebot_acquisition_window_diagnostic_rows{kind="crossed_quotes"})',
            "red",
        ),
        (
            "acquisition",
            'sum(tradebot_acquisition_window_diagnostic_rows{kind="locked_quotes"})',
            "orange",
        ),
    ],
)
def test_any_nonzero_anomaly_count_receives_a_visible_warning(
    name: str, expression: str, expected_color: str
) -> None:
    panel = panel_for_metric(name, expression)
    assert displayed_color(panel, 0) == "blue"
    assert displayed_color(panel, 1) == expected_color
    assert displayed_color(panel, 379_467) == expected_color
    assert panel["fieldConfig"]["defaults"]["unit"] == "locale"
    assert panel["fieldConfig"]["defaults"]["decimals"] == 0


def test_missing_calendar_report_is_unknown_and_never_a_zero_or_pass() -> None:
    panel = panel_for_metric("data-quality", "tradebot_gate1_calendar_unknown_days")
    assert displayed_color(panel, -1) == "gray"
    assert panel["fieldConfig"]["defaults"]["mappings"][0]["options"]["-1"]["text"] == (
        "No evidence"
    )


def test_replay_integrity_does_not_hide_historical_implementation() -> None:
    current = panel_for_metric("research", "tradebot_research_implementation_current")
    assert current["title"] == "Code match"
    assert current["fieldConfig"]["defaults"]["noValue"] == "UNKNOWN"
    assert displayed_color(current, 0) == "orange"
    assert displayed_color(current, 1) == "blue"
    mapping = current["fieldConfig"]["defaults"]["mappings"][0]["options"]
    assert "HISTORICAL" in mapping["0"]["text"]
    assert mapping["1"]["text"] == "Current code"
    integrity = next(p for p in dashboard("research")["panels"] if p["id"] == 2)
    assert integrity["title"] == "Report integrity"
    assert "check Code match separately" in integrity["description"]


@pytest.mark.parametrize("name", ("acquisition", "system"))
def test_retrieval_completion_cannot_be_mistaken_for_quality_acceptance(name: str) -> None:
    retrieval = panel_for_metric(name, "tradebot_acquisition_run_state")
    quality = panel_for_metric(name, "tradebot_acquisition_quality_indeterminate")
    complete = retrieval["fieldConfig"]["defaults"]["mappings"][0]["options"]["2"]
    assert complete == {"text": "Retrieval complete", "color": "blue"}
    assert displayed_color(quality, 1) == "orange"


@pytest.mark.parametrize("name", CURRENT_DASHBOARDS)
def test_dashboard_panels_do_not_obscure_neighboring_evidence(name: str) -> None:
    panels = dashboard(name)["panels"]
    assert len({panel["id"] for panel in panels}) == len(panels)
    for index, panel in enumerate(panels):
        left = panel["gridPos"]
        assert 0 <= left["x"] < left["x"] + left["w"] <= 24
        assert left["y"] >= 0 and left["h"] > 0
        for other in panels[index + 1 :]:
            right = other["gridPos"]
            overlap_x = left["x"] < right["x"] + right["w"] and right["x"] < (left["x"] + left["w"])
            overlap_y = left["y"] < right["y"] + right["h"] and right["y"] < (left["y"] + left["h"])
            assert not (overlap_x and overlap_y), (panel["id"], other["id"])
