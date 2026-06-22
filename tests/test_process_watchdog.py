from pathlib import Path

from core.process_watchdog import dump_main_thread_stack, log_process_snapshot, memory_bucket_index


class _DummyLogger:
    def __init__(self):
        self.messages = []

    def info(self, message, *args):
        self.messages.append(("info", message % args if args else message))


def test_memory_bucket_index_returns_negative_one_below_threshold():
    assert memory_bucket_index(1399, threshold_mb=1400, step_mb=256) == -1


def test_memory_bucket_index_grows_in_steps():
    assert memory_bucket_index(1400, threshold_mb=1400, step_mb=256) == 0
    assert memory_bucket_index(1657, threshold_mb=1400, step_mb=256) == 1


def test_dump_main_thread_stack_contains_current_test_name():
    stack_text = dump_main_thread_stack()

    assert "test_dump_main_thread_stack_contains_current_test_name" in stack_text


def test_log_process_snapshot_can_write_watchdog_file_directly(tmp_path):
    logger = _DummyLogger()

    log_process_snapshot(
        "main_window.init.begin",
        logger=logger,
        project_root=str(tmp_path),
        direct_watchdog=True,
        extra={"phase": "unit"},
    )

    log_files = list((tmp_path / "data" / "logs").glob("watchdog_*.log"))
    assert len(log_files) == 1
    log_text = Path(log_files[0]).read_text(encoding="utf-8")
    assert logger.messages
    assert "[watchdog] main_window.init.begin" in log_text
    assert "phase=unit" in log_text
