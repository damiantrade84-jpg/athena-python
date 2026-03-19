"""orderbook_metrics.py — Microstructure signals from order book and trade data.

Computes order book imbalance, liquidity wall detection, orderflow delta,
and liquidity pressure. All outputs are normalized to [-1, 1].
"""

import logging
from typing import List, Dict, Tuple

log = logging.getLogger("sentinel")


def _normalize_to_range(value: float, min_val: float, max_val: float) -> float:
    """Clamp and normalize a value to [-1, 1] given expected bounds."""
    if max_val == min_val:
        return 0.0
    # First clamp to bounds
    clamped = max(min_val, min(max_val, value))
    # Normalize to [0, 1]
    norm = (clamped - min_val) / (max_val - min_val)
    # Scale to [-1, 1]
    return 2.0 * norm - 1.0


def order_book_imbalance(
    bids: List[Tuple[float, float]], asks: List[Tuple[float, float]]
) -> float:
    """Compute order book imbalance: (bid_vol - ask_vol) / (bid_vol + ask_vol).
    Returns value in [-1, 1]; positive = bid-heavy, negative = ask-heavy."""
    bid_vol = sum(size for _, size in bids)
    ask_vol = sum(size for _, size in asks)
    total = bid_vol + ask_vol
    if total == 0:
        return 0.0
    return (bid_vol - ask_vol) / total


def liquidity_wall_detection(
    bids: List[Tuple[float, float]],
    asks: List[Tuple[float, float]],
    current_price: float,
    threshold_multiplier: float = 5.0,
) -> float:
    """Detect large limit orders (>threshold_multiplier x average level size) and return distance to nearest wall.
    Returns normalized distance in [-1, 1] where -1 = wall far below price, 1 = wall far above price."""
    if not bids or not asks or current_price <= 0:
        return 0.0
    # Compute average level size
    all_sizes = [size for _, size in bids + asks]
    if not all_sizes:
        return 0.0
    avg_size = sum(all_sizes) / len(all_sizes)
    wall_threshold = avg_size * threshold_multiplier
    # Find walls on both sides
    wall_prices = []
    for price, size in bids + asks:
        if size >= wall_threshold:
            wall_prices.append(price)
    if not wall_prices:
        return 0.0
    # Find nearest wall to current price
    nearest_wall = min(wall_prices, key=lambda p: abs(p - current_price))
    distance_pct = abs(nearest_wall - current_price) / current_price
    # Normalize distance: assume max meaningful distance = 5%
    # Sign: positive if wall above price, negative if below
    sign = 1 if nearest_wall > current_price else -1
    return sign * _normalize_to_range(distance_pct, 0.0, 0.05)


def orderflow_delta(market_buy_volume: float, market_sell_volume: float) -> float:
    """Compute orderflow delta: market_buy - market_sell.
    Returns normalized delta in [-1, 1]."""
    raw_delta = market_buy_volume - market_sell_volume
    total_volume = market_buy_volume + market_sell_volume
    if total_volume == 0:
        return 0.0
    # Normalize to [-1, 1] by dividing by total volume
    return raw_delta / total_volume


def liquidity_pressure(imbalance: float, normalized_delta: float) -> float:
    """Compute liquidity pressure as weighted sum of imbalance and normalized delta.
    Returns value in [-1, 1]."""
    # Equal weighting; clamp to [-1, 1] just in case
    pressure = 0.5 * imbalance + 0.5 * normalized_delta
    return max(-1.0, min(1.0, pressure))


def compute_microstructure_signals(
    bids: List[Tuple[float, float]],
    asks: List[Tuple[float, float]],
    current_price: float,
    market_buy_volume: float = 0.0,
    market_sell_volume: float = 0.0,
    liquidity_wall_threshold: float = 5.0,
) -> Dict[str, float]:
    """Compute all microstructure signals with normalized outputs.
    Args:
        bids: list of (price, size) tuples for bid levels
        asks: list of (price, size) tuples for ask levels
        current_price: current mid price
        market_buy_volume: total market buy volume in the interval
        market_sell_volume: total market sell volume in the interval
    Returns:
        Dict with normalized signals in [-1, 1]:
            - order_book_imbalance
            - liquidity_wall_detection
            - orderflow_delta
            - liquidity_pressure
    """
    # Compute individual signals
    imb = order_book_imbalance(bids, asks)
    wall_dist = liquidity_wall_detection(
        bids, asks, current_price, threshold_multiplier=liquidity_wall_threshold
    )
    delta = orderflow_delta(market_buy_volume, market_sell_volume)
    pressure = liquidity_pressure(imb, delta)
    return {
        "order_book_imbalance": imb,
        "liquidity_wall_detection": wall_dist,
        "orderflow_delta": delta,
        "liquidity_pressure": pressure,
    }
