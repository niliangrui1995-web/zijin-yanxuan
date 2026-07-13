import sys

from app.services import scan_engine_facade as _engine_module

sys.modules[__name__] = _engine_module
