import sys

from infra.storage import json_cache_repository as _repository

sys.modules[__name__] = _repository
