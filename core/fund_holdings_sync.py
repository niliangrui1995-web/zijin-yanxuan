import sys

from domains.fund_holdings import sync as _sync_module

sys.modules[__name__] = _sync_module
