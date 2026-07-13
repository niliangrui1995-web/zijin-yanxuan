import importlib
import sys

sys.modules[__name__] = importlib.import_module("domains.runtime.domain_events")
