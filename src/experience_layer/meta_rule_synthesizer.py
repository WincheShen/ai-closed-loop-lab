"""元规则归纳器 (Meta-Rule Synthesizer)。

每 20 笔平仓触发一次；由 LLM 从 lessons + trade_attributions 中归纳出
"重复出现的教训"和"该避免的场景"，并回写到 persona 的 avoid_setups
或 experience_layer 的 meta_rules 表中。

设计目标:
- 补上"学习了但没归纳"的缺口 — 10条教训单次出现的问题
- 输出格式化的元规则，供 Strategist 直接读取而不需要每次重新总结
- 幂等: 相同输入产生稳定输出，可以重复触发
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from src.central_brain import get_central_brain
from src.infra.logger import get_agent_logger
from src.infra.model_adapter import get_llm

logger = get_agent_logger("experience", "meta_rule_synthesizer")

_MIN_TRADES_TO_TRIGGER = 20  # 累计新增至少 N 笔才触发


_META_RULE_SYSTEM_PROMPT = """\
你是交易系统的元规则归纳者 (Meta-Rule Synthesizer)。

你的任务是从最近的交易归因记录（trade_attributions）和教训（lessons）中，
归纳出**重复出现的失败模式**和**可以避免的坑**，形成 3-5 条可操作的元规则。

## 输出要求

严格按以下 JSON 格式输出（不要多余文字）:

```json
{
  "avoid_patterns": [
    {
      "pattern": "具体的失败模式（如 '在 rebound regime 追高放量突破'）",
      "evidence": "支持证据（如 '3笔亏损平均 -5%'）",
      "lesson": "该避免的具体行为（15字内）"
    }
  ],
  "prefer_patterns": [
    {
      "pattern": "重复出现的成功模式",
      "evidence": "支持证据",
      "action": "应该继续/加强的行为"
    }
  ],
  "summary": "整体判断（30字内），如 '短线放量突破在 rebound 环境失效，应转向前排回踩'"
}
```

## 归纳原则
1. 只归纳有**至少 2 次证据**的模式（单次教训不进元规则）
2. 优先看**策略 × regime × 亏损原因**的三维组合
3. 避免笼统建议，要具体可操作
4. 如果证据不足，返回空数组而不是编造
"""


class MetaRuleSynthesizer:
    """元规则归纳器。"""

    def __init__(self) -> None:
        self.brain = get_central_brain()
        self._ensure_table()

    def _ensure_table(self) -> None:
        """确保 meta_rules 表存在。"""
        conn = self.brain.store._conn()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS meta_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                trades_window_start TEXT,
                trades_window_end TEXT,
                trades_count INTEGER,
                avoid_patterns_json TEXT,
                prefer_patterns_json TEXT,
                summary TEXT,
                active INTEGER DEFAULT 1
            )"""
        )
        conn.commit()

    def should_trigger(self) -> bool:
        """判断是否应该重新归纳元规则。"""
        conn = self.brain.store._conn()
        total_trades = conn.execute(
            "SELECT COUNT(*) FROM trade_attributions"
        ).fetchone()[0]

        last = conn.execute(
            "SELECT trades_count FROM meta_rules WHERE active = 1 "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        last_count = last[0] if last else 0

        return total_trades - last_count >= _MIN_TRADES_TO_TRIGGER

    def synthesize(self, force: bool = False) -> dict[str, Any] | None:
        """执行元规则归纳。

        Args:
            force: 强制重新归纳（忽略 20 笔阈值）

        Returns:
            {avoid_patterns, prefer_patterns, summary} 或 None（数据不足）
        """
        conn = self.brain.store._conn()

        # 1. 拉取所有归因数据
        rows = conn.execute(
            "SELECT symbol, name, strategy_id, entry_regime, outcome, "
            "pnl_pct, holding_days, primary_cause, secondary_causes_json, "
            "lesson, actual_narrative, created_at "
            "FROM trade_attributions "
            "ORDER BY created_at DESC "
            "LIMIT 100"
        ).fetchall()

        if not rows:
            logger.info("无归因数据，跳过元规则归纳")
            return None

        if not force and not self.should_trigger():
            logger.info("累计新增交易不足 %d 笔，跳过元规则归纳", _MIN_TRADES_TO_TRIGGER)
            return None

        # 2. 构建结构化数据摘要供 LLM 分析
        summary_lines = []
        for r in rows:
            summary_lines.append(
                f"- {r['symbol']}({r['name']}) {r['strategy_id']} × {r['entry_regime']} "
                f"| {r['outcome']} pnl={r['pnl_pct']:.1f}% 持仓{r['holding_days']}天 "
                f"| 主因={r['primary_cause']} 次因={r['secondary_causes_json']} "
                f"| 教训: {r['lesson']}"
            )
        summary_text = "\n".join(summary_lines)

        # 3. 计算统计信息辅助 LLM
        stats = self._compute_stats(rows)

        user_msg = f"""\
## 最近 {len(rows)} 笔平仓交易归因

{summary_text}

## 统计概览

{stats}

请从中归纳 3-5 条元规则。
"""

        # 4. 调 LLM
        try:
            llm = get_llm()
            response = llm.invoke([
                {"role": "system", "content": _META_RULE_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ])
            result = self._parse_response(response.content)
        except Exception as e:
            logger.error("元规则归纳 LLM 调用失败: %s", e)
            return None

        if not result:
            logger.warning("LLM 输出无法解析，跳过")
            return None

        # 5. 落库 + 停用旧规则
        conn.execute("UPDATE meta_rules SET active = 0 WHERE active = 1")
        conn.execute(
            "INSERT INTO meta_rules "
            "(created_at, trades_window_start, trades_window_end, trades_count, "
            "avoid_patterns_json, prefer_patterns_json, summary, active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (
                datetime.now().isoformat(),
                rows[-1]["created_at"] if rows else "",
                rows[0]["created_at"] if rows else "",
                len(rows),
                json.dumps(result.get("avoid_patterns", []), ensure_ascii=False),
                json.dumps(result.get("prefer_patterns", []), ensure_ascii=False),
                result.get("summary", ""),
            ),
        )
        conn.commit()

        logger.info(
            "元规则归纳完成: %d 条 avoid, %d 条 prefer | %s",
            len(result.get("avoid_patterns", [])),
            len(result.get("prefer_patterns", [])),
            result.get("summary", ""),
        )
        return result

    def get_active_rules(self) -> dict[str, Any] | None:
        """获取当前生效的元规则。"""
        conn = self.brain.store._conn()
        row = conn.execute(
            "SELECT avoid_patterns_json, prefer_patterns_json, summary, "
            "trades_count, created_at "
            "FROM meta_rules WHERE active = 1 "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return {
            "avoid_patterns": json.loads(row["avoid_patterns_json"] or "[]"),
            "prefer_patterns": json.loads(row["prefer_patterns_json"] or "[]"),
            "summary": row["summary"],
            "trades_count": row["trades_count"],
            "created_at": row["created_at"],
        }

    def generate_prompt_block(self) -> str:
        """生成注入 Strategist prompt 的元规则块。"""
        rules = self.get_active_rules()
        if not rules:
            return ""
        parts = [f"\n## 元规则（近 {rules['trades_count']} 笔归纳）"]
        if rules.get("summary"):
            parts.append(f"  综合判断: {rules['summary']}")
        avoid = rules.get("avoid_patterns", [])
        if avoid:
            parts.append("  应避免的模式:")
            for p in avoid:
                parts.append(f"    - {p.get('pattern', '')}: {p.get('lesson', '')}")
        prefer = rules.get("prefer_patterns", [])
        if prefer:
            parts.append("  应坚持的模式:")
            for p in prefer:
                parts.append(f"    - {p.get('pattern', '')}: {p.get('action', '')}")
        return "\n".join(parts) + "\n"

    def _compute_stats(self, rows: list) -> str:
        """计算辅助统计：策略×regime×outcome 分布。"""
        combos: dict[tuple, dict] = {}
        for r in rows:
            key = (r["strategy_id"] or "unknown", r["entry_regime"] or "unknown")
            if key not in combos:
                combos[key] = {"wins": 0, "losses": 0, "total_pnl": 0.0}
            if r["outcome"] == "win":
                combos[key]["wins"] += 1
            elif r["outcome"] == "loss":
                combos[key]["losses"] += 1
            combos[key]["total_pnl"] += r["pnl_pct"] or 0

        lines = []
        for (sid, regime), stats in sorted(combos.items()):
            total = stats["wins"] + stats["losses"]
            if total == 0:
                continue
            win_rate = stats["wins"] / total * 100 if total else 0
            avg_pnl = stats["total_pnl"] / total
            lines.append(
                f"- {sid} × {regime}: {stats['wins']}W/{stats['losses']}L "
                f"胜率{win_rate:.0f}% 均盈亏{avg_pnl:+.1f}%"
            )
        return "\n".join(lines) if lines else "(无足够数据)"

    def _parse_response(self, content: str) -> dict[str, Any] | None:
        """从 LLM 输出解析 JSON。"""
        content = content.strip()
        # 剥离 markdown code fence
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
        try:
            data = json.loads(content)
            if not isinstance(data, dict):
                return None
            return data
        except json.JSONDecodeError as e:
            logger.warning("元规则 JSON 解析失败: %s | content=%s", e, content[:200])
            return None


_synthesizer_singleton: MetaRuleSynthesizer | None = None


def get_synthesizer() -> MetaRuleSynthesizer:
    """全局单例。"""
    global _synthesizer_singleton
    if _synthesizer_singleton is None:
        _synthesizer_singleton = MetaRuleSynthesizer()
    return _synthesizer_singleton
