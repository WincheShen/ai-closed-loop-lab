#!/usr/bin/env python3
"""初始化多人格资金账户。

读取 config/trading_persona.yaml 中的所有人格配置，
为每个人格创建对应的资金账户。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.infra.logger import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)

from src.agents.cio.trading_persona import list_personas
from src.central_brain import get_central_brain


def main() -> None:
    """初始化所有人格的资金账户。"""
    brain = get_central_brain()
    store = brain.store

    personas = list_personas()
    if not personas:
        logger.warning("未找到任何人格配置")
        return

    logger.info("找到 %d 个人格配置", len(personas))

    for p in personas:
        persona_id = p["id"]
        persona_name = p["name"]
        capital = p.get("capital", 0)

        if capital <= 0:
            logger.warning("人格 %s (%s) 未配置资金，跳过", persona_id, persona_name)
            continue

        # 检查账户是否已存在
        existing = store.get_account_by_persona(persona_id)
        if existing:
            logger.info(
                "账户已存在: %s (%s) - 资金: %.2f",
                persona_name,
                persona_id,
                existing["capital"],
            )
            continue

        # 创建新账户
        account_id = store.create_account(persona_id, persona_name, capital)
        logger.info(
            "创建账户成功: %s (%s) - 资金: %.2f - 账户ID: %s",
            persona_name,
            persona_id,
            capital,
            account_id,
        )

    # 列出所有账户
    accounts = store.list_accounts()
    logger.info("当前所有账户:")
    for acc in accounts:
        logger.info(
            "  - %s (%s): 资金 %.2f, 可用 %.2f, 总价值 %.2f, 盈亏 %.2f",
            acc["persona_name"],
            acc["persona_id"],
            acc["capital"],
            acc["available_cash"],
            acc["total_value"],
            acc["total_pnl"],
        )


if __name__ == "__main__":
    main()
