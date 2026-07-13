import sys

from domains.earnings import scheduler as _scheduler_module

sys.modules[__name__] = _scheduler_module
