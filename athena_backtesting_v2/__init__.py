"""Athena Engine A/B point-in-time backtesting platform.

This package intentionally owns no trading strategy rules.  It consumes the
production Engine A and Engine B decision contracts and owns only immutable
historical data, causal replay, simulation, validation, jobs, and reporting.
"""

from .constants import PLATFORM_VERSION

__all__ = ["PLATFORM_VERSION"]

