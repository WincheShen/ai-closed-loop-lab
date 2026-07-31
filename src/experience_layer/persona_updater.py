"""PersonaUpdater — MetaRuleSynthesizer 结果自动回写 persona.yaml。

每周日 MetaRuleSynthesizer 归纳后调用 apply_meta_rules()：
- avoid_patterns[].lesson → persona.avoid_setups (追加去重)
- prefer_patterns[].action → persona.preferred_setups (追加去重)
- 自动备份 + 校验 + 缓存失效

安全设计:
- 单次最多追加 5 条 avoid / 3 条 prefer（防止 LLM 一次性注入过多）
- avoid_setups 总数上限 15 条（超过则淘汰最早追加的）
- 写前备份 .yaml.bak
- 写后 yaml.safe_load 校验
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_PERSONA_PATH = Path("config/trading_persona.yaml")
_MAX_AVOID_APPEND = 5
_MAX_PREFER_APPEND = 3
_MAX_AVOID_TOTAL = 15
_MAX_PREFER_TOTAL = 12


class PersonaUpdater:
    """自动将 MetaRuleSynthesizer 归纳结果回写到 persona.yaml。"""

    def __init__(self, persona_path: Path | str | None = None) -> None:
        self.persona_path = Path(persona_path) if persona_path else _PERSONA_PATH

    def apply_meta_rules(self, rules: dict[str, Any], persona_id: str | None = None) -> dict[str, Any]:
        """将元规则结果回写到指定人格的 avoid_setups / preferred_setups。

        Args:
            rules: MetaRuleSynthesizer.synthesize() 返回的 dict
            persona_id: 目标人格 ID（None = 第一个人格）

        Returns:
            {"updated": bool, "avoid_added": [...], "prefer_added": [...]}
        """
        result = {"updated": False, "avoid_added": [], "prefer_added": []}

        if not rules:
            return result

        avoid_patterns = rules.get("avoid_patterns", [])
        prefer_patterns = rules.get("prefer_patterns", [])
        if not avoid_patterns and not prefer_patterns:
            return result

        # 1. 备份
        self._backup()

        # 2. 加载当前 YAML
        data = self._load_yaml()
        if not data:
            logger.error("[PersonaUpdater] 无法加载 persona.yaml")
            return result

        # 3. 定位目标 persona
        persona = self._find_persona(data, persona_id)
        if not persona:
            logger.error("[PersonaUpdater] 未找到人格: %s", persona_id)
            return result

        # 4. 追加 avoid_setups（去重 + 限制数量）
        existing_avoid = set(persona.get("avoid_setups", []))
        new_avoid = []
        for p in avoid_patterns[:_MAX_AVOID_APPEND]:
            lesson = p.get("lesson", "").strip()
            if lesson and lesson not in existing_avoid:
                new_avoid.append(lesson)
                existing_avoid.add(lesson)

        if new_avoid:
            if "avoid_setups" not in persona:
                persona["avoid_setups"] = []
            persona["avoid_setups"].extend(new_avoid)
            # 超出上限时淘汰最早追加的（保留前 4 条原始 + 截断）
            if len(persona["avoid_setups"]) > _MAX_AVOID_TOTAL:
                persona["avoid_setups"] = persona["avoid_setups"][:_MAX_AVOID_TOTAL]
            result["avoid_added"] = new_avoid

        # 5. 追加 preferred_setups（去重 + 限制数量）
        existing_prefer = set(persona.get("preferred_setups", []))
        new_prefer = []
        for p in prefer_patterns[:_MAX_PREFER_APPEND]:
            action = p.get("action", "").strip()
            if action and action not in existing_prefer:
                new_prefer.append(action)
                existing_prefer.add(action)

        if new_prefer:
            if "preferred_setups" not in persona:
                persona["preferred_setups"] = []
            persona["preferred_setups"].extend(new_prefer)
            if len(persona["preferred_setups"]) > _MAX_PREFER_TOTAL:
                persona["preferred_setups"] = persona["preferred_setups"][:_MAX_PREFER_TOTAL]
            result["prefer_added"] = new_prefer

        # 6. 无变更则提前退出
        if not new_avoid and not new_prefer:
            logger.info("[PersonaUpdater] 无新增规则（全部已存在），跳过写入")
            return result

        # 7. 写回 YAML + 校验
        if not self._save_yaml(data):
            logger.error("[PersonaUpdater] YAML 写入/校验失败，已恢复备份")
            self._restore_backup()
            return result

        # 8. 缓存失效
        self._invalidate_cache(persona_id)

        result["updated"] = True
        logger.info(
            "[PersonaUpdater] 回写完成: +%d avoid, +%d prefer | persona=%s",
            len(new_avoid), len(new_prefer), persona_id or "default",
        )
        return result

    def _backup(self) -> None:
        """创建 .yaml.bak 备份。"""
        bak_path = self.persona_path.with_suffix(".yaml.bak")
        try:
            shutil.copy2(self.persona_path, bak_path)
        except Exception as e:
            logger.warning("[PersonaUpdater] 备份失败: %s", e)

    def _restore_backup(self) -> None:
        """从备份恢复。"""
        bak_path = self.persona_path.with_suffix(".yaml.bak")
        try:
            if bak_path.exists():
                shutil.copy2(bak_path, self.persona_path)
                logger.info("[PersonaUpdater] 已从备份恢复")
        except Exception as e:
            logger.error("[PersonaUpdater] 恢复备份失败: %s", e)

    def _load_yaml(self) -> dict | None:
        """加载 YAML 文件。"""
        try:
            with open(self.persona_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error("[PersonaUpdater] YAML 加载失败: %s", e)
            return None

    def _save_yaml(self, data: dict) -> bool:
        """写入 YAML 并校验语法。"""
        try:
            content = yaml.dump(
                data,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
                width=120,
            )
            # 校验：重新 safe_load 确保不损坏
            yaml.safe_load(content)
            with open(self.persona_path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except Exception as e:
            logger.error("[PersonaUpdater] YAML 写入/校验失败: %s", e)
            return False

    def _find_persona(self, data: dict, persona_id: str | None) -> dict | None:
        """在 YAML 数据中定位目标 persona dict。"""
        personas = data.get("personas", [])
        if not personas:
            return None
        if persona_id is None:
            return personas[0]
        for p in personas:
            if p.get("id") == persona_id:
                return p
        return None

    def _invalidate_cache(self, persona_id: str | None) -> None:
        """使 trading_persona 缓存失效。"""
        try:
            from src.agents.cio.trading_persona import get_persona
            get_persona(persona_id=persona_id, reload=True)
            logger.info("[PersonaUpdater] persona 缓存已刷新")
        except Exception as e:
            logger.warning("[PersonaUpdater] 缓存刷新失败 (下次启动自动修复): %s", e)
