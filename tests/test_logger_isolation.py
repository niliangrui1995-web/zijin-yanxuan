import logging.handlers
import os
from datetime import datetime
from pathlib import Path

from core.logger import get_logger


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def test_pytest_file_logging_uses_test_log_dir():
    marker = "pytest logger isolation probe"
    logger = get_logger("tests.logger_isolation")
    logger.warning(marker)

    test_log_dir = Path(os.environ["VCP_HUNTER_LOG_DIR"]).resolve()
    project_log_dir = Path(__file__).resolve().parents[1] / "data" / "logs"
    handlers = [
        handler
        for handler in logger.handlers
        if isinstance(handler, logging.handlers.RotatingFileHandler)
    ]

    assert handlers
    for handler in handlers:
        handler.flush()

    log_files = [Path(handler.baseFilename).resolve() for handler in handlers]
    assert all(path.parent == test_log_dir for path in log_files)
    assert all(not _is_relative_to(path, project_log_dir) for path in log_files)
    assert any(marker in path.read_text(encoding="utf-8") for path in log_files)

    project_log = project_log_dir / f"vcp_{datetime.now().strftime('%Y%m%d')}.log"
    if project_log.exists():
        assert marker not in project_log.read_text(encoding="utf-8")
