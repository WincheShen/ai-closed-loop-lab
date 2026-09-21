from src.stock_analyzer.detectors.bottom_reversal import detect as detect_bottom_reversal
from src.stock_analyzer.detectors.hot_sector_pullback import detect as detect_hot_sector_pullback
from src.stock_analyzer.detectors.types import BarContext, DetectorHit
from src.stock_analyzer.detectors.ultra_short_momentum import detect as detect_ultra_short_momentum
from src.stock_analyzer.detectors.value_investing import detect as detect_value_investing

DETECTORS = {
    "hot_sector_pullback": detect_hot_sector_pullback,
    "bottom_reversal": detect_bottom_reversal,
    "value_investing": detect_value_investing,
    "ultra_short_momentum": detect_ultra_short_momentum,
}

__all__ = ["BarContext", "DetectorHit", "DETECTORS"]
