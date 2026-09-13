"""Google Trends via pytrends (0 credits). Search-demand confirmation for context keywords."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

TrendsSeries = dict[str, list[int]]


class TrendsProvider(Protocol):
    def fetch(self, keywords: list[str], timeframe: str) -> TrendsSeries: ...


@dataclass
class TrendSignal:
    keyword: str
    latest: int
    average: float
    momentum: float  # latest / average, > 1 means rising

    @property
    def label(self) -> str:
        if self.momentum >= 1.25:
            return "rising"
        if self.momentum <= 0.75:
            return "falling"
        return "steady"


class PytrendsProvider:
    def __init__(self, hl: str = "en-US", tz: int = 0) -> None:
        from pytrends.request import TrendReq

        self._client = TrendReq(hl=hl, tz=tz)

    def fetch(self, keywords: list[str], timeframe: str) -> TrendsSeries:
        self._client.build_payload(keywords[:5], timeframe=timeframe)
        frame = self._client.interest_over_time()
        if frame is None or frame.empty:
            return {}
        return {kw: [int(v) for v in frame[kw].tolist()] for kw in keywords[:5] if kw in frame}


class FakeTrendsProvider:
    def __init__(self, series: TrendsSeries | None = None, fail: bool = False) -> None:
        self.series = series or {}
        self.fail = fail

    def fetch(self, keywords: list[str], timeframe: str) -> TrendsSeries:
        if self.fail:
            raise RuntimeError("trends unavailable")
        return {kw: self.series.get(kw, [50] * 8) for kw in keywords}


class FreeTrends:
    def __init__(self, provider: TrendsProvider | Callable[[], TrendsProvider]) -> None:
        self._provider = provider
        self.last_status: str = "ok"

    def signals(self, keywords: list[str], timeframe: str = "today 1-m") -> list[TrendSignal]:
        keywords = [k for k in keywords if k][:5]
        if not keywords:
            return []
        try:
            provider = self._provider() if callable(self._provider) and not hasattr(self._provider, "fetch") else self._provider
            series = provider.fetch(keywords, timeframe)  # type: ignore[union-attr]
        except Exception:
            self.last_status = "unknown"
            return []
        self.last_status = "ok"
        out: list[TrendSignal] = []
        for keyword, values in series.items():
            if not values:
                continue
            latest = int(values[-1])
            average = sum(values) / len(values)
            momentum = (latest / average) if average else 0.0
            out.append(TrendSignal(keyword=keyword, latest=latest, average=round(average, 1), momentum=round(momentum, 2)))
        return out
