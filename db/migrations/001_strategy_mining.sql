-- =====================================================================
-- Strategy Mining schema
-- Purpose: 公众号策略逆向工程 —— 抓文章、抽操作、回填行情、归纳策略、回测
-- Target DB: PostgreSQL 15+
-- Created: 2026-04-28
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS strategy_mining;
SET search_path TO strategy_mining, public;

-- ---------------------------------------------------------------------
-- 1. wechat_articles —— 公众号原文存储
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wechat_articles (
    id              BIGSERIAL   PRIMARY KEY,
    article_url     TEXT        NOT NULL UNIQUE,
    msg_id          TEXT,
    biz             TEXT,                    -- 公众号 __biz 参数
    account_name    TEXT,
    title           TEXT        NOT NULL,
    author          TEXT,
    publish_time    TIMESTAMPTZ,
    content_html    TEXT,
    content_text    TEXT,
    content_md      TEXT,
    word_count      INTEGER,
    fetch_method    TEXT,                    -- playwright / manual / api
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_meta        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wechat_articles_account
    ON wechat_articles (account_name, publish_time DESC);
CREATE INDEX IF NOT EXISTS idx_wechat_articles_publish
    ON wechat_articles (publish_time DESC);

-- ---------------------------------------------------------------------
-- 2. extracted_trades —— 从文章抽取的买卖操作
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS extracted_trades (
    id                  BIGSERIAL   PRIMARY KEY,
    article_id          BIGINT      NOT NULL REFERENCES wechat_articles(id) ON DELETE CASCADE,
    action              TEXT        NOT NULL,   -- buy / sell / add / reduce / hold / watch
    symbol              TEXT,                   -- e.g. 600519
    exchange            TEXT,                   -- SH / SZ / BJ / HK / US
    stock_name          TEXT,
    trade_date          DATE,                   -- 估计的实际操作日期（可能 != publish_time）
    trade_price         NUMERIC(14,4),
    price_low           NUMERIC(14,4),
    price_high          NUMERIC(14,4),
    position_pct        NUMERIC(6,2),           -- 仓位百分比 0-100
    quantity            BIGINT,
    confidence          NUMERIC(4,3),           -- 0.000 - 1.000
    raw_quote           TEXT,                   -- 原文片段
    extraction_method   TEXT,                   -- regex / llm / manual
    extractor_version   TEXT,
    reviewed            BOOLEAN     NOT NULL DEFAULT FALSE,
    reviewed_by         TEXT,
    reviewed_at         TIMESTAMPTZ,
    notes               TEXT,
    extra               JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_extracted_trades_article
    ON extracted_trades (article_id);
CREATE INDEX IF NOT EXISTS idx_extracted_trades_symbol_date
    ON extracted_trades (symbol, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_extracted_trades_action
    ON extracted_trades (action);

-- ---------------------------------------------------------------------
-- 3. market_snapshots —— 操作时点行情 + 技术指标 + 板块/大盘
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_snapshots (
    id                      BIGSERIAL   PRIMARY KEY,
    symbol                  TEXT        NOT NULL,
    exchange                TEXT,
    snapshot_date           DATE        NOT NULL,
    -- OHLCV
    open_price              NUMERIC(14,4),
    high_price              NUMERIC(14,4),
    low_price               NUMERIC(14,4),
    close_price             NUMERIC(14,4),
    prev_close              NUMERIC(14,4),
    volume                  BIGINT,
    amount                  NUMERIC(20,2),
    turnover_rate           NUMERIC(8,4),
    pct_change              NUMERIC(8,4),
    -- 技术指标
    kdj_k                   NUMERIC(8,4),
    kdj_d                   NUMERIC(8,4),
    kdj_j                   NUMERIC(8,4),
    macd_diff               NUMERIC(10,4),
    macd_dea                NUMERIC(10,4),
    macd_hist               NUMERIC(10,4),
    boll_up                 NUMERIC(14,4),
    boll_mid                NUMERIC(14,4),
    boll_low                NUMERIC(14,4),
    ma5                     NUMERIC(14,4),
    ma10                    NUMERIC(14,4),
    ma20                    NUMERIC(14,4),
    ma60                    NUMERIC(14,4),
    rsi6                    NUMERIC(8,4),
    rsi12                   NUMERIC(8,4),
    rsi24                   NUMERIC(8,4),
    -- 板块
    sector_name             TEXT,
    sector_pct_change       NUMERIC(8,4),
    sector_rank             INTEGER,
    hot_sectors             JSONB,              -- 当日 top N 热门板块
    -- 大盘
    market_index_sh         NUMERIC(14,4),
    market_index_sh_pct     NUMERIC(8,4),
    market_index_sz         NUMERIC(14,4),
    market_index_sz_pct     NUMERIC(8,4),
    market_index_cyb        NUMERIC(14,4),
    market_index_cyb_pct    NUMERIC(8,4),
    extra                   JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_market_snapshots_date
    ON market_snapshots (snapshot_date DESC);

-- ---------------------------------------------------------------------
-- 4. inferred_strategies —— LLM 归纳的策略规则
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS inferred_strategies (
    id                  BIGSERIAL   PRIMARY KEY,
    account_name        TEXT        NOT NULL,
    run_id              TEXT        NOT NULL,   -- 一次归纳运行的 UUID
    llm_model           TEXT,
    sample_size         INTEGER,
    sample_period_start DATE,
    sample_period_end   DATE,
    description         TEXT,                   -- 自然语言策略描述
    rules_json          JSONB,                  -- 结构化规则（可喂回测引擎）
    tags                TEXT[],                 -- 价值/趋势/热点/低估…
    confidence          NUMERIC(4,3),
    raw_llm_output      TEXT,
    prompt_version      TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_inferred_strategies_account
    ON inferred_strategies (account_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inferred_strategies_run
    ON inferred_strategies (run_id);

-- ---------------------------------------------------------------------
-- 5. backtest_runs —— 回测结果
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS backtest_runs (
    id                      BIGSERIAL   PRIMARY KEY,
    strategy_id             BIGINT      NOT NULL REFERENCES inferred_strategies(id) ON DELETE CASCADE,
    engine                  TEXT,                   -- vectorbt / backtrader
    period_start            DATE        NOT NULL,
    period_end              DATE        NOT NULL,
    universe                TEXT,                   -- 股票池描述
    benchmark               TEXT,                   -- e.g. 000300.SH
    initial_capital         NUMERIC(20,2),
    -- 业绩指标
    total_return            NUMERIC(10,4),
    annual_return           NUMERIC(10,4),
    max_drawdown            NUMERIC(10,4),
    sharpe_ratio            NUMERIC(10,4),
    sortino_ratio           NUMERIC(10,4),
    win_rate                NUMERIC(6,4),
    trade_count             INTEGER,
    alpha_vs_benchmark      NUMERIC(10,4),
    hit_rate_vs_author      NUMERIC(6,4),           -- 与原作者命中率对比
    -- 详细数据
    equity_curve            JSONB,                  -- 净值曲线
    trades_log              JSONB,
    params                  JSONB,
    report_path             TEXT,
    notes                   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy
    ON backtest_runs (strategy_id, created_at DESC);

-- ---------------------------------------------------------------------
-- updated_at 自动维护触发器
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION strategy_mining.touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_wechat_articles_updated_at ON wechat_articles;
CREATE TRIGGER trg_wechat_articles_updated_at
    BEFORE UPDATE ON wechat_articles
    FOR EACH ROW EXECUTE FUNCTION strategy_mining.touch_updated_at();

DROP TRIGGER IF EXISTS trg_extracted_trades_updated_at ON extracted_trades;
CREATE TRIGGER trg_extracted_trades_updated_at
    BEFORE UPDATE ON extracted_trades
    FOR EACH ROW EXECUTE FUNCTION strategy_mining.touch_updated_at();
