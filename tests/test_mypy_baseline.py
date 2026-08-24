# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from collections import Counter

from scripts import mypy_baseline


def _fingerprint(path: str = "ui/window.py", message: str = "missing attribute"):
    return mypy_baseline.DiagnosticFingerprint(path, "error", "attr-defined", message)


def test_mypy_json_normalizes_paths_sorts_and_counts_duplicates(tmp_path):
    output = "\n".join(
        (
            json.dumps({"file": "ui\\window.py", "severity": "error", "code": "attr-defined", "message": "z"}),
            json.dumps({"file": "./app/service.py", "severity": "error", "code": "arg-type", "message": "a"}),
            json.dumps(
                {"file": "ui/parts/../window.py", "severity": "error", "code": "attr-defined", "message": "z"}
            ),
        )
    )

    diagnostics = mypy_baseline.parse_mypy_json_lines(output, repo_root=tmp_path)
    document = mypy_baseline.baseline_document(diagnostics, mypy_version="mypy 2.3.0", reason="initial")

    assert diagnostics[_fingerprint(message="z")] == 2
    entries = document["diagnostics"]
    assert isinstance(entries, list)
    assert [item["path"] for item in entries] == ["app/service.py", "ui/window.py"]
    assert document["diagnostic_count"] == 3


def test_mypy_baseline_allows_reductions_but_rejects_new_diagnostics():
    old = _fingerprint(message="old")
    new = _fingerprint(message="new")
    baseline = Counter({old: 2})

    added, resolved = mypy_baseline.compare_diagnostics(baseline, Counter({old: 1}))
    assert not added
    assert resolved == Counter({old: 1})

    added, resolved = mypy_baseline.compare_diagnostics(baseline, Counter({old: 1, new: 1}))
    assert added == Counter({new: 1})
    assert resolved == Counter({old: 1})


def test_mypy_baseline_round_trip_is_canonical(tmp_path):
    path = tmp_path / "mypy.json"
    diagnostics = Counter({_fingerprint("ui/b.py", "b"): 1, _fingerprint("ui/a.py", "a"): 2})

    mypy_baseline.write_baseline(
        path,
        diagnostics,
        mypy_version="mypy 2.3.0 (compiled: yes)",
        reason="ticket-123",
    )

    version, loaded = mypy_baseline.load_baseline(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert version == "mypy 2.3.0 (compiled: yes)"
    assert loaded == diagnostics
    assert payload["update_reason"] == "ticket-123"
    assert [entry["path"] for entry in payload["diagnostics"]] == ["ui/a.py", "ui/b.py"]


def test_mypy_baseline_cli_requires_reason_for_update(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        mypy_baseline,
        "collect_mypy_diagnostics",
        lambda: ("2.3.0", Counter()),
    )

    result = mypy_baseline.main(["--baseline", str(tmp_path / "baseline.json"), "--update"])

    assert result == 2
    assert "--reason is required" in capsys.readouterr().out


def test_mypy_baseline_cli_reports_new_diagnostic(monkeypatch, tmp_path, capsys):
    baseline_path = tmp_path / "baseline.json"
    mypy_baseline.write_baseline(
        baseline_path,
        Counter({_fingerprint(message="old"): 1}),
        mypy_version="mypy 2.3.0 (compiled: yes)",
        reason="initial",
    )
    monkeypatch.setattr(
        mypy_baseline,
        "collect_mypy_diagnostics",
        lambda: ("mypy 2.3.0 (compiled: yes)", Counter({_fingerprint(message="new"): 1})),
    )

    result = mypy_baseline.main(["--baseline", str(baseline_path)])

    assert result == 1
    output = capsys.readouterr().out
    assert "new=1" in output
    assert "NEW ui/window.py: [attr-defined] new" in output


def test_mypy_baseline_cli_requires_resolved_diagnostics_to_be_ratcheted(
    monkeypatch, tmp_path, capsys
):
    baseline_path = tmp_path / "baseline.json"
    old = _fingerprint(message="old")
    mypy_baseline.write_baseline(
        baseline_path,
        Counter({old: 2}),
        mypy_version="mypy 2.3.0 (compiled: yes)",
        reason="initial",
    )
    monkeypatch.setattr(
        mypy_baseline,
        "collect_mypy_diagnostics",
        lambda: ("mypy 2.3.0 (compiled: yes)", Counter({old: 1})),
    )

    result = mypy_baseline.main(["--baseline", str(baseline_path)])

    assert result == 1
    output = capsys.readouterr().out
    assert "resolved=1" in output
    assert "baseline is stale" in output


def test_mypy_baseline_update_requires_explicit_allow_new(monkeypatch, tmp_path, capsys):
    baseline_path = tmp_path / "baseline.json"
    mypy_baseline.write_baseline(
        baseline_path,
        Counter({_fingerprint(message="old"): 1}),
        mypy_version="mypy 2.3.0 (compiled: yes)",
        reason="initial",
    )
    monkeypatch.setattr(
        mypy_baseline,
        "collect_mypy_diagnostics",
        lambda: ("mypy 2.3.0 (compiled: yes)", Counter({_fingerprint(message="new"): 1})),
    )

    refused = mypy_baseline.main(
        ["--baseline", str(baseline_path), "--update", "--reason", "reviewed-change"]
    )
    accepted = mypy_baseline.main(
        [
            "--baseline",
            str(baseline_path),
            "--update",
            "--allow-new",
            "--reason",
            "reviewed-change",
        ]
    )

    assert refused == 1
    assert accepted == 0
    assert "update refused" in capsys.readouterr().out
    _version, diagnostics = mypy_baseline.load_baseline(baseline_path)
    assert diagnostics == Counter({_fingerprint(message="new"): 1})


def test_initial_mypy_baseline_does_not_absorb_untracked_file_diagnostics(
    monkeypatch, tmp_path, capsys
):
    baseline_path = tmp_path / "baseline.json"
    current = Counter({_fingerprint(message="new-code-error"): 1})
    monkeypatch.setattr(
        mypy_baseline,
        "collect_mypy_diagnostics",
        lambda: ("mypy 2.3.0 (compiled: yes)", current),
    )
    monkeypatch.setattr(mypy_baseline, "git_untracked_repo_paths", lambda: frozenset({"ui/window.py"}))

    result = mypy_baseline.main(
        ["--baseline", str(baseline_path), "--update", "--reason", "initial-history"]
    )

    assert result == 1
    assert not baseline_path.exists()
    assert "untracked files contain diagnostics" in capsys.readouterr().out


def test_mypy_worker_cleanup_is_narrow(tmp_path):
    worker = tmp_path / ".mypy_worker.123.0.json"
    survivor = tmp_path / "mypy_worker.123.0.json"
    worker.write_text("{}", encoding="utf-8")
    survivor.write_text("{}", encoding="utf-8")

    mypy_baseline.remove_mypy_worker_shards(repo_root=tmp_path)

    assert not worker.exists()
    assert survivor.exists()


def test_mypy_baseline_rejects_paths_outside_repository(tmp_path):
    outside = tmp_path.parent / "outside.py"

    try:
        mypy_baseline.normalize_repo_path(str(outside.resolve()), repo_root=tmp_path)
    except ValueError as exc:
        assert "outside the repository" in str(exc)
    else:
        raise AssertionError("outside-repository path was accepted")


def test_mypy_baseline_keeps_ui_diagnostics_in_the_no_new_diagnostics_ratchet():
    ui_fingerprint = _fingerprint("ui/components/example.py", "ui regression")
    core_fingerprint = _fingerprint("core/example.py", "core regression")
    diagnostics = Counter({ui_fingerprint: 2, core_fingerprint: 1})

    assert mypy_baseline.MYPY_TARGETS == (
        "ui/kline_pool_state.py",
        "ui/kline_typing.py",
        "ui/kline_window_recovery.py",
    )
    assert mypy_baseline.DEFAULT_BASELINE.name == "mypy_ui_baseline.json"
    assert mypy_baseline.MYPY_RATCHET_PREFIXES == ("ui/",)
    assert mypy_baseline.diagnostic_count_for_prefix(diagnostics, "ui/") == 2
