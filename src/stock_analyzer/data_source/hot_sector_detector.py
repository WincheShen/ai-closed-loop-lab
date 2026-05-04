"""热点板块识别。

Phase 1 简化逻辑：综合涨幅 + 成交额 + 主力净流入打分。
后续可演化为多日趋势 + 资金持续度 + 龙头股集中度。
"""
from __future__ import annotations

from dataclasses import dataclass

from .akshare_client import MarketSnapshot, SectorQuote


@dataclass
class SectorScore:
    sector: SectorQuote
    score: float
    rank: int


class HotSectorDetector:
    def __init__(
        self,
        weight_change_pct: float = 0.5,
        weight_turnover: float = 0.2,
        weight_main_fund: float = 0.3,
    ):
        self.w_pct = weight_change_pct
        self.w_to = weight_turnover
        self.w_mf = weight_main_fund

    def detect(self, snapshot: MarketSnapshot, top_k: int = 5) -> list[SectorScore]:
        if not snapshot.sectors:
            return []

        max_to = max((s.turnover for s in snapshot.sectors), default=1.0) or 1.0
        max_mf = max((abs(s.main_fund_net_inflow) for s in snapshot.sectors), default=1.0) or 1.0

        scored: list[SectorScore] = []
        for sec in snapshot.sectors:
            norm_pct = sec.change_pct / 10.0  # ±10% → ±1
            norm_to = sec.turnover / max_to
            norm_mf = sec.main_fund_net_inflow / max_mf
            score = (
                self.w_pct * norm_pct
                + self.w_to * norm_to
                + self.w_mf * norm_mf
            )
            scored.append(SectorScore(sector=sec, score=score, rank=0))

        scored.sort(key=lambda x: x.score, reverse=True)
        for i, s in enumerate(scored[:top_k]):
            s.rank = i + 1
        return scored[:top_k]
