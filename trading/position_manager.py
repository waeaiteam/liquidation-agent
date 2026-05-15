from __future__ import annotations

from typing import Any


def true_range(current: list[float], previous_close: float | None) -> float:
    high, low, close = current
    if previous_close is None:
        return high - low
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def atr_from_klines(klines: list[list[Any]], period: int = 14) -> float:
    candles = []
    for item in klines:
        try:
            candles.append([float(item[2]), float(item[3]), float(item[4])])
        except (TypeError, ValueError, IndexError):
            continue
    if len(candles) < period + 1:
        return 0.0
    ranges = []
    previous_close = None
    for candle in candles[-(period + 1):]:
        ranges.append(true_range(candle, previous_close))
        previous_close = candle[2]
    ranges = ranges[-period:]
    return sum(ranges) / len(ranges) if ranges else 0.0


class DynamicPositionRules:
    def initial_stop(self, entry_price: float, atr: float, side: str = "long") -> float:
        entry_price = float(entry_price)
        atr = float(atr or 0)
        if atr <= 0:
            return round(entry_price * 0.92, 8)
        if side.lower() == "short":
            return round(entry_price + 2 * atr, 8)
        return round(max(0.0, entry_price - 2 * atr), 8)

    def trailing_stop(self, current_price: float, atr: float, previous_stop: float, side: str = "long", entry_price: float | None = None) -> float:
        current_price = float(current_price)
        atr = float(atr or 0)
        previous_stop = float(previous_stop or 0)
        entry = float(entry_price or 0)
        multiplier = 3.0
        if atr > 0 and entry > 0:
            if side.lower() == "short":
                profit_atr = (entry - current_price) / atr
            else:
                profit_atr = (current_price - entry) / atr
            if profit_atr >= 3:
                multiplier = 1.5
            elif profit_atr >= 1:
                multiplier = 2.0
        if atr <= 0:
            candidate = current_price * 0.9
        elif side.lower() == "short":
            candidate = current_price + multiplier * atr
            return round(min(previous_stop or candidate, candidate), 8)
        else:
            candidate = current_price - multiplier * atr
        return round(max(previous_stop, candidate), 8)

    def exit_reason(self, position: dict[str, Any], context: dict[str, Any]) -> str:
        price = float(context.get("price") or 0)
        stop = float(position.get("trailing_stop") or position.get("stop_price") or 0)
        if price and stop and price <= stop and str(position.get("side", "long")).lower() == "long":
            return "trailing_stop"
        if float(context.get("fr_current") or 0) >= 0:
            return "fr_flip"
        if bool(context.get("oi_declining")):
            return "oi_drop"
        entry_volume = float(position.get("entry_volume_24h") or 0)
        current_volume = float(context.get("volume_24h") or 0)
        if entry_volume > 0 and current_volume > 0 and current_volume <= entry_volume * 0.3:
            return "volume_dry"
        ai_action = str(context.get("ai_action") or "").lower()
        if ai_action == "close":
            return "ai_decision"
        return ""
