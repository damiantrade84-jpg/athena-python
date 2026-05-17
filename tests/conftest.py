"""Pytest hooks shared by this package's tests.

config.py runs fatal safety validation at import time. When config.yaml disables
paper soak / allows nested real orders / disables paper mode, validation requires
ATHENA_REAL_ORDERS_CONFIRM. Tests import config before load_dotenv() runs, so set
the token here via setdefault (does not override an explicit user env).
"""

from __future__ import annotations

import os

os.environ.setdefault(
    "ATHENA_REAL_ORDERS_CONFIRM",
    "I_UNDERSTAND_REAL_ORDER_RISK",
)
