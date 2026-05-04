"""图片脱敏（占位实现）。

Phase 1 目标：
- 接收原始图片，输出脱敏图片（高斯模糊覆盖股票代码/名称区域）
- 当前实现：整体加水印 + 关键区域 OCR 后模糊（OCR 待 Phase 2）

依赖：
- Pillow（必装）
- 真正的 OCR 模糊待 Phase 2 接入 PaddleOCR / EasyOCR

API 设计与真实实现对齐，方便后续替换。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RedactResult:
    output_path: Path
    redacted_regions: int
    method: str            # "blur_all" | "ocr_blur" | "noop"
    notes: str = ""


class ImageRedactor:
    """股票代码/名称脱敏器。"""

    def __init__(self, blur_radius: int = 18):
        self.blur_radius = blur_radius
        try:
            from PIL import Image, ImageFilter  # noqa: F401
            self._pil_available = True
        except ImportError:
            logger.warning("Pillow 未安装，ImageRedactor 仅做文件复制")
            self._pil_available = False

    def redact(
        self,
        input_path: Path | str,
        output_path: Path | str,
        regions: Optional[list[tuple[int, int, int, int]]] = None,
    ) -> RedactResult:
        """脱敏图片。

        Args:
            regions: 待模糊区域列表 [(x1,y1,x2,y2), ...]，
                     若为空则对整图打雾化（Phase 1 默认行为）。

        Phase 2 升级：自动 OCR 找出 6 位股票代码 / 中文股票名称。
        """
        input_path = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not self._pil_available:
            output_path.write_bytes(input_path.read_bytes())
            return RedactResult(
                output_path=output_path,
                redacted_regions=0,
                method="noop",
                notes="Pillow not available",
            )

        from PIL import Image, ImageFilter

        with Image.open(input_path) as img:
            img = img.convert("RGB")
            if regions:
                # 局部模糊
                for box in regions:
                    crop = img.crop(box).filter(ImageFilter.GaussianBlur(self.blur_radius))
                    img.paste(crop, box)
                method = "ocr_blur"
                count = len(regions)
            else:
                # Phase 1 占位：对底部 1/3 区域整体模糊
                # （持仓截图中股票代码/名称多在中下部）
                w, h = img.size
                box = (0, int(h * 0.55), w, h)
                crop = img.crop(box).filter(ImageFilter.GaussianBlur(self.blur_radius))
                img.paste(crop, box)
                method = "blur_all"
                count = 1

            img.save(output_path)

        return RedactResult(
            output_path=output_path,
            redacted_regions=count,
            method=method,
            notes="Phase 1 placeholder, OCR-based redaction待 Phase 2",
        )
