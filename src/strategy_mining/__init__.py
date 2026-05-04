"""Strategy Mining — 公众号策略逆向工程。

模块职责：
1. 采集：从微信公众号抓取历史文章（URL 列表 + 正文）
2. 抽取：从文章正文识别买卖操作（symbol / action / date / price）
3. 行情回填：对每条操作回填当日 OHLCV / KDJ / MACD / BOLL / 板块 / 大盘
4. 策略归纳：用 LLM 从样本归纳策略规则
5. 回测验证：用 vectorbt 验证规则是否能复现作者操作

数据存储：PostgreSQL schema=`strategy_mining`
建表脚本：db/migrations/001_strategy_mining.sql
"""
