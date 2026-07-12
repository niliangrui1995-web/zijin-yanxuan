# -*- coding: utf-8 -*-
"""Compatibility alias for :mod:`infra.storage.json_cache_repository`."""

from __future__ import annotations

import sys

from infra.storage import json_cache_repository as _repository

sys.modules[__name__] = _repository
