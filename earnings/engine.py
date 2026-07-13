import sys

from domains.earnings import engine as _engine_module

sys.modules[__name__] = _engine_module
