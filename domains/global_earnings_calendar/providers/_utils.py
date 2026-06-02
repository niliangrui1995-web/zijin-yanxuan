# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from core.logger import get_logger

log = get_logger(__name__)


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
