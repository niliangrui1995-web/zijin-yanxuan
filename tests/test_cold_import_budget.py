# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

from scripts import cold_import_budget
from scripts.cold_import_budget import (
    PROBE_SAMPLE_COUNT,
    TARGET_BUDGETS,
    ImportBudget,
    aggregate_samples,
    evaluate_measurement,
    probe_target,
)


def test_cold_import_budgets_cover_light_packages_and_main_window():
    assert set(TARGET_BUDGETS) == {"app.services", "infra.market_data", "ui.main_window_qt"}
    assert TARGET_BUDGETS["ui.main_window_qt"].requires_qapplication is True
    assert TARGET_BUDGETS["ui.main_window_qt"].max_elapsed_ms == 1000.0
    assert TARGET_BUDGETS["ui.main_window_qt"].max_rss_delta_mb == 48.0


def test_cold_import_budget_reports_time_and_memory_regressions():
    budget = ImportBudget(max_elapsed_ms=100.0, max_rss_delta_mb=10.0)

    failures = evaluate_measurement(
        {"elapsed_ms": 101.0, "rss_delta_mb": 11.0},
        budget,
    )

    assert failures == [
        {"metric": "elapsed_ms", "actual": 101.0, "budget": 100.0},
        {"metric": "rss_delta_mb", "actual": 11.0, "budget": 10.0},
    ]


def test_cold_import_budget_uses_median_and_keeps_every_sample():
    samples = [
        {"elapsed_ms": 110.0, "rss_delta_mb": 20.0, "qapplication_created": True},
        {"elapsed_ms": 4100.0, "rss_delta_mb": 200.0, "qapplication_created": True},
        {"elapsed_ms": 120.0, "rss_delta_mb": 22.0, "qapplication_created": True},
    ]

    measurement = aggregate_samples("ui.main_window_qt", samples)

    assert measurement["sample_count"] == PROBE_SAMPLE_COUNT
    assert measurement["aggregation"] == "median"
    assert measurement["elapsed_ms"] == 120.0
    assert measurement["rss_delta_mb"] == 22.0
    assert measurement["samples"] == samples
    assert evaluate_measurement(measurement, TARGET_BUDGETS["ui.main_window_qt"]) == []


def test_probe_target_runs_three_isolated_child_samples(monkeypatch):
    calls = []

    def fake_run(command, *, cwd, env, **kwargs):
        calls.append({"command": command, "cwd": cwd, "db_path": env["VCP_HUNTER_DB_PATH"], **kwargs})
        sample_number = len(calls)
        payload = {
            "target": "app.services",
            "elapsed_ms": float(sample_number),
            "rss_before_mb": 10.0,
            "rss_after_mb": 11.0,
            "rss_delta_mb": float(sample_number),
            "qapplication_created": False,
        }
        return SimpleNamespace(returncode=0, stdout=f"{cold_import_budget.json.dumps(payload)}\n", stderr="")

    monkeypatch.setattr(cold_import_budget.subprocess, "run", fake_run)

    measurement = probe_target("app.services", TARGET_BUDGETS["app.services"])

    assert len(calls) == PROBE_SAMPLE_COUNT
    assert len({call["db_path"] for call in calls}) == PROBE_SAMPLE_COUNT
    assert measurement["elapsed_ms"] == 2.0
    assert measurement["rss_delta_mb"] == 2.0
    assert [sample["sample_number"] for sample in measurement["samples"]] == [1, 2, 3]


def test_qapplication_attribute_is_set_before_instance_or_creation(monkeypatch):
    events = []
    qt_core = ModuleType("PyQt6.QtCore")
    qt_widgets = ModuleType("PyQt6.QtWidgets")

    class FakeQCoreApplication:
        @staticmethod
        def setAttribute(attribute):
            events.append(("set_attribute", attribute))

    class FakeQApplication:
        @staticmethod
        def instance():
            events.append(("instance", None))
            return None

        def __init__(self, argv):
            events.append(("create", argv))

    share_contexts = object()
    qt_core.QCoreApplication = FakeQCoreApplication
    qt_core.Qt = SimpleNamespace(ApplicationAttribute=SimpleNamespace(AA_ShareOpenGLContexts=share_contexts))
    qt_widgets.QApplication = FakeQApplication
    monkeypatch.setitem(sys.modules, "PyQt6.QtCore", qt_core)
    monkeypatch.setitem(sys.modules, "PyQt6.QtWidgets", qt_widgets)
    monkeypatch.setattr(cold_import_budget.importlib, "import_module", lambda target: events.append(("import", target)))

    measurement = cold_import_budget._run_child_probe("ui.main_window_qt", requires_qapplication=True)

    assert events == [
        ("set_attribute", share_contexts),
        ("instance", None),
        ("create", []),
        ("import", "ui.main_window_qt"),
    ]
    assert measurement["qapplication_created"] is True
