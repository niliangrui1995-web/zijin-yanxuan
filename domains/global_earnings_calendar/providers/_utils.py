# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import queue
import shutil
import tempfile
import threading
from pathlib import Path

from core.logger import get_logger

log = get_logger(__name__)


def _collect_daemon_task_results(tasks, *, max_workers: int, thread_name_prefix: str):
    """Run independent upstream calls without executor shutdown waiting at process exit."""

    if not tasks:
        return []

    pending_tasks = queue.Queue()
    completed_tasks = queue.Queue()
    for task_key, task in tasks:
        pending_tasks.put((task_key, task))

    def _worker() -> None:
        while True:
            try:
                task_key, task = pending_tasks.get_nowait()
            except queue.Empty:
                return
            try:
                completed_tasks.put((task_key, task(), None))
            except (KeyboardInterrupt, SystemExit) as exc:
                # Keep process-control signals observable to the caller thread.
                completed_tasks.put((task_key, None, exc))
            except Exception as exc:  # noqa: BLE001 - isolate independent upstream providers.
                completed_tasks.put((task_key, None, exc))
            finally:
                pending_tasks.task_done()

    worker_count = min(max(1, int(max_workers or 1)), len(tasks))
    for index in range(worker_count):
        threading.Thread(
            target=_worker,
            name=f"{thread_name_prefix}-{index + 1}",
            daemon=True,
        ).start()

    return [completed_tasks.get() for _ in tasks]


def _ensure_ascii_ca_bundle() -> None:
    try:
        import certifi
    except ImportError:
        return
    ca_path = certifi.where()
    try:
        ca_path.encode("ascii")
        return
    except UnicodeEncodeError:
        pass

    target_dir = Path(tempfile.gettempdir()) / "codex_certifi"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "cacert.pem"
    try:
        if not target.is_file() or target.stat().st_size != Path(ca_path).stat().st_size:
            shutil.copyfile(ca_path, target)
    except OSError as exc:
        log.debug(f"[global earnings calendar] unable to prepare ascii CA bundle: {exc}")
        return
    os.environ.setdefault("CURL_CA_BUNDLE", str(target))
    os.environ.setdefault("REQUESTS_CA_BUNDLE", str(target))
