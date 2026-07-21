from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts import kline_webengine_pool_soak as pool_soak


class _FakePage:
    def renderProcessPid(self) -> int:
        return 4242


class _FakeBrowser:
    def __init__(self) -> None:
        self._page = _FakePage()

    def page(self) -> _FakePage:
        return self._page


class _FakeChart:
    def __init__(self) -> None:
        self.browser = _FakeBrowser()


class _FakeManager:
    def __init__(self, chart: _FakeChart) -> None:
        self._charts: list[_FakeChart] = []
        self._idle_chart: _FakeChart | None = chart
        self._reclaiming_chart = None
        self._prewarm_window = None

    @property
    def active_count(self) -> int:
        return len(self._charts)

    @property
    def managed_webengine_keeper_count(self) -> int:
        return int(self._idle_chart is not None)

    @property
    def managed_webengine_keeper_ready(self) -> bool:
        return self._idle_chart is not None

    def runtime_health_snapshot(self) -> dict[str, int]:
        return {
            "browser_count": 1,
            "page_count": 1,
            "active_window_count": len(self._charts),
            "keeper_count": self.managed_webengine_keeper_count,
        }


class _FakeHarness:
    def __init__(self, _args) -> None:
        self.chart = _FakeChart()
        self.manager = _FakeManager(self.chart)
        self.shutdown_called = False

    def prepare(self, *, timeout_ms: int) -> dict:
        return {"status": "ok", "timeout_ms": timeout_ms}

    def open_chart(self, _args, label: str):
        assert self.manager._idle_chart is self.chart
        self.manager._idle_chart = None
        self.manager._charts = [self.chart]
        return self.chart, {
            "label": label,
            "browser_ready": True,
            "chart_ready": True,
            "load_events": [True],
        }

    def close_chart(self, chart, *, timeout_ms: int) -> bool:
        assert timeout_ms > 0
        assert chart is self.chart
        self.manager._charts = []
        self.manager._idle_chart = chart
        return True

    def process_events(self) -> None:
        return None

    def provider_evidence(self) -> dict:
        return {
            "status": "ok",
            "mode": "production-local",
            "local_only": True,
            "network_access_enabled": False,
        }

    def shutdown(self) -> dict:
        self.shutdown_called = True
        self.manager._idle_chart = None
        return {
            "manager_shutdown": {
                "clean": True,
                "active_windows": 0,
                "managed_keepers": 0,
            },
            "lifecycle_shutdown": {"post_close": {"webengine_child_count": 0}},
        }


def _stable_process_sample() -> dict:
    return {
        "scope": "probe_root_and_recursive_descendants_only",
        "root_pid": 100,
        "process_count": 3,
        "qtwebengine_process_count": 2,
        "rss_mb": 200.0,
        "qtwebengine_rss_mb": 80.0,
        "processes": [],
    }


def _args(*extra: str):
    return pool_soak._parse_args(
        [
            "--cycles",
            "2",
            "--minimum-cycles",
            "2",
            "--warmup-cycles",
            "1",
            "--stability-window",
            "2",
            "--segment-size",
            "1",
            *extra,
        ]
    )


def test_defaults_define_strict_native_hundred_cycle_soak():
    args = pool_soak._parse_args([])

    assert args.cycles == 100
    assert args.minimum_cycles == 100
    assert args.warmup_cycles == 2
    assert args.stability_window == 20
    assert args.segment_size == 10
    assert args.provider_mode == "production-local"
    assert args.max_unique_physical_windows == 1
    assert args.max_qtwebengine_process_growth == 0


def test_run_soak_reuses_one_physical_browser_and_page_and_builds_segments():
    report = pool_soak.run_soak(
        _args(),
        harness_factory=_FakeHarness,
        process_sampler=_stable_process_sample,
    )

    assert report["status"] == "ok"
    assert report["budget"] == {"status": "ok", "failures": []}
    assert report["data_provider"]["network_access_enabled"] is False
    assert len(report["warmup_cycles"]) == 1
    assert len(report["cycles"]) == 2
    assert len(report["segments"]) == 2
    assert all(segment["max_physical_window_count"] == 1 for segment in report["segments"])
    assert report["trend"]["tree_rss_growth_mb"] == 0.0
    opened = [cycle["samples"][1] for cycle in report["cycles"]]
    assert len({sample["identities"]["physical_window_id"] for sample in opened}) == 1
    assert len({sample["identities"]["browser_id"] for sample in opened}) == 1
    assert len({sample["identities"]["page_id"] for sample in opened}) == 1
    assert {sample["identities"]["render_process_pid"] for sample in opened} == {4242}


def test_budget_fails_on_renderer_loss_identity_churn_and_resource_growth():
    report = pool_soak.run_soak(
        _args(),
        harness_factory=_FakeHarness,
        process_sampler=_stable_process_sample,
    )
    second_open = report["cycles"][1]["samples"][1]
    second_open["identities"]["page_id"] += 1
    second_open["identities"]["render_process_pid"] = 0
    report["cycles"][1]["samples"][2]["process_tree"]["rss_mb"] = 300.0

    failures = pool_soak.evaluate_budget(
        report,
        pool_soak.SoakBudgets(),
        minimum_cycles=2,
        stability_window=2,
    )
    checks = {failure["check"] for failure in failures}

    assert "reuse.page_id" in checks
    assert "renderer.pid" in checks
    assert "resources.tree_rss_growth_mb" in checks
    assert "resources.tail_rss_range_mb" in checks


def test_owned_process_sampler_reads_only_root_and_recursive_children(monkeypatch):
    class FakeProcess:
        def __init__(self, pid: int, name: str, rss: int, children=()) -> None:
            self.pid = pid
            self._name = name
            self._rss = rss
            self._children = list(children)

        def children(self, *, recursive: bool):
            assert recursive is True
            return list(self._children)

        def name(self) -> str:
            return self._name

        def memory_info(self):
            return SimpleNamespace(rss=self._rss)

    renderer = FakeProcess(102, "QtWebEngineProcess.exe", 30 * pool_soak.MB)
    helper = FakeProcess(103, "helper.exe", 10 * pool_soak.MB)
    root = FakeProcess(101, "python.exe", 100 * pool_soak.MB, (renderer, helper))
    looked_up: list[int] = []

    def process_lookup(pid: int):
        looked_up.append(pid)
        return root

    monkeypatch.setattr(pool_soak.psutil, "Process", process_lookup)

    sample = pool_soak.collect_owned_process_tree(101)

    assert looked_up == [101]
    assert sample["root_pid"] == 101
    assert sample["process_count"] == 3
    assert sample["qtwebengine_process_count"] == 1
    assert sample["rss_mb"] == 140.0
    assert sample["scope"] == "probe_root_and_recursive_descendants_only"


def test_main_writes_json_and_returns_nonzero_on_failed_budget(monkeypatch, tmp_path: Path):
    output = tmp_path / "soak.json"
    monkeypatch.setattr(
        pool_soak,
        "run_soak",
        lambda _args: {"status": "fail", "budget": {"status": "fail", "failures": []}},
    )

    exit_code = pool_soak.main(["--output", str(output)])

    assert exit_code == 1
    assert json_load(output)["status"] == "fail"


def test_configuration_rejects_stability_window_larger_than_cycle_count():
    args = _args("--stability-window", "3")

    report = pool_soak.run_soak(
        args,
        harness_factory=_FakeHarness,
        process_sampler=_stable_process_sample,
    )

    assert report["status"] == "fail"
    assert report["error"]["type"] == "ValueError"
    assert report["data_provider"]["status"] == "not_started"


def test_shutdown_evidence_fails_closed_when_keeper_or_webengine_child_remains():
    report = {
        "shutdown": {
            "manager_shutdown": {
                "clean": False,
                "active_windows": 1,
                "managed_keepers": 1,
            },
            "lifecycle_shutdown": {"post_close": {"webengine_child_count": 1}},
        }
    }

    failures = pool_soak._shutdown_budget_failures(report)

    assert {failure["check"] for failure in failures} == {
        "shutdown.manager_clean",
        "shutdown.active_windows",
        "shutdown.managed_keepers",
        "shutdown.webengine_children",
    }


def json_load(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))
