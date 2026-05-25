"""Pytest hooks shared by this package's tests.

config.py runs fatal safety validation at import time. When config.yaml disables
paper soak / allows nested real orders / disables paper mode, validation requires
ATHENA_REAL_ORDERS_CONFIRM. Tests import config before load_dotenv() runs, so set
the token here via setdefault (does not override an explicit user env).
"""

from __future__ import annotations

import os
import sys
from types import ModuleType

# Optional runtime dep: stub before data_feeds/news_sentiment_feed import eodhd at collection time.
if "eodhd" not in sys.modules:
    try:
        import eodhd  # noqa: F401
    except ModuleNotFoundError:
        _eodhd_stub = ModuleType("eodhd")

        class _StubEODHDClient:
            def __init__(self, *_args, **_kwargs):
                pass

        _eodhd_stub.APIClient = _StubEODHDClient
        sys.modules["eodhd"] = _eodhd_stub

os.environ.setdefault(
    "ATHENA_REAL_ORDERS_CONFIRM",
    "I_UNDERSTAND_REAL_ORDER_RISK",
)
