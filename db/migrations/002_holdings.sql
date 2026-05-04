-- =====================================================================
-- Strategy Mining — Holdings (截图 → 持仓快照 → 推断交易)
-- Purpose: 从每日持仓截图重建交易历史 + 支撑风格分析
-- Target DB: PostgreSQL 15+
-- Created: 2026-04-30
-- =====================================================================

SET search_path TO strategy_mining, public;

-- ---------------------------------------------------------------------
-- 6. trader_profile —— 被研究的交易员画像（一人一行）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trader_profile (
    id                      BIGSERIAL   PRIMARY KEY,
    trader_alias            TEXT        NOT NULL UNIQUE,   -- '二池'
    related_public_account  TEXT,                           -- '股海贼W实盘记录'
    related_taoguba_id      TEXT,                           -- '二池'
    core_principles         JSONB,                          -- ['保本金', '等风来', '控回撤']
    stock_selection_style   JSONB,                          -- 选股偏好结构化
    position_mgmt_style     JSONB,                          -- 仓位/止盈/止损规则
    memoir_texts            TEXT[],                         -- 原始语录多段
    account_sessions        JSONB,                          -- [{broker, suffix, date_range, perf}, ...]
    analysis_generated_at   TIMESTAMPTZ,
    analysis_llm_model      TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------
-- 7. holding_snapshots —— 每张截图一行（账户级聚合）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS holding_snapshots (
    id                      BIGSERIAL   PRIMARY KEY,
    trader_alias            TEXT        NOT NULL,
    trade_date              DATE        NOT NULL,
    page_type               TEXT        NOT NULL,           -- position_page / performance_page / other
    broker_name             TEXT,                            -- '中信证券' / '湘财证券'
    account_suffix          TEXT,                            -- '3575' / '6678'
    account_session_id      TEXT,                            -- '{broker}_{suffix}' hashed period id
    total_assets            NUMERIC(18,2),
    total_pnl               NUMERIC(18,2),
    total_pnl_today         NUMERIC(18,2),
    total_pnl_today_pct     NUMERIC(8,4),
    market_value            NUMERIC(18,2),
    available_cash          NUMERIC(18,2),
    withdrawable_cash       NUMERIC(18,2),
    position_pct            NUMERIC(6,4),                    -- 0-1
    holding_count           INTEGER,                         -- 含 0/0 行
    active_holding_count    INTEGER,                         -- 不含 0/0
    source_image_path       TEXT        NOT NULL,            -- 复制后的路径 data/strategy_mining/holdings/*
    source_image_sha256     TEXT        NOT NULL,
    raw_vlm_json            JSONB       NOT NULL,
    vlm_model               TEXT,
    vlm_confidence          NUMERIC(4,3),
    needs_review            BOOLEAN     NOT NULL DEFAULT FALSE,
    notes                   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (trader_alias, trade_date, account_suffix)
);
CREATE INDEX IF NOT EXISTS idx_holding_snapshots_date
    ON holding_snapshots (trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_holding_snapshots_session
    ON holding_snapshots (account_session_id, trade_date);

-- ---------------------------------------------------------------------
-- 8. holding_items —— 持仓明细（每只股票一行，含 0/0 卖出证据行）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS holding_items (
    id                          BIGSERIAL   PRIMARY KEY,
    snapshot_id                 BIGINT      NOT NULL REFERENCES holding_snapshots(id) ON DELETE CASCADE,
    trade_date                  DATE        NOT NULL,
    account_session_id          TEXT,
    row_index                   INTEGER     NOT NULL,         -- 截图中的行位置
    -- 姓名：可见片段 + 解析后全名
    stock_name_visible          TEXT,                          -- '广东华'
    stock_name_obfuscation      TEXT,                          -- manual_paint / auto_blur / none
    stock_name_full             TEXT,                          -- '广东华特气体'
    symbol                      TEXT,                          -- '688268'
    exchange                    TEXT,                          -- 'SH'/'SZ'/'BJ'
    match_confidence            NUMERIC(4,3),                  -- 0-1
    match_method                TEXT,                          -- exact / prefix+price / fuzzy / manual
    -- 数值
    market_value                NUMERIC(18,2),
    pnl_amount                  NUMERIC(18,2),
    pnl_pct                     NUMERIC(8,4),
    position_qty                BIGINT,                        -- 持仓
    available_qty               BIGINT,                        -- 可用
    cost_price                  NUMERIC(14,4),
    current_price               NUMERIC(14,4),
    is_zero_position            BOOLEAN     NOT NULL DEFAULT FALSE,  -- 0/0 已卖出
    needs_review                BOOLEAN     NOT NULL DEFAULT FALSE,
    notes                       TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (snapshot_id, row_index)
);
CREATE INDEX IF NOT EXISTS idx_holding_items_symbol_date
    ON holding_items (symbol, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_holding_items_session
    ON holding_items (account_session_id, trade_date);

-- ---------------------------------------------------------------------
-- 9. holding_trades —— 由日差推断的交易事件
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS holding_trades (
    id                          BIGSERIAL   PRIMARY KEY,
    trader_alias                TEXT        NOT NULL,
    trade_date                  DATE        NOT NULL,          -- 推断发生日 = t
    prev_trade_date             DATE,                           -- t-1（观测上一日）
    gap_days                    INTEGER,                        -- 相隔观测天数
    is_inter_period             BOOLEAN     NOT NULL DEFAULT FALSE,
    account_session_id          TEXT,
    account_session_id_prev     TEXT,
    symbol                      TEXT,
    stock_name_full             TEXT,
    event_type                  TEXT        NOT NULL,           -- buy_open / add / reduce / sell_close / re_enter / broker_transition / unknown
    prev_qty                    BIGINT,
    new_qty                     BIGINT,
    delta_qty                   BIGINT,
    prev_cost                   NUMERIC(14,4),
    new_cost                    NUMERIC(14,4),
    trade_price_estimate        NUMERIC(14,4),                  -- 用当日 close 估算
    price_estimate_source       TEXT,                           -- 'akshare_close' / 'snapshot_current_price'
    proceeds_estimate           NUMERIC(18,2),                  -- delta_qty * trade_price_estimate
    realized_pnl                NUMERIC(18,2),                  -- 仅 sell/reduce/close
    holding_days_at_close       INTEGER,
    derivation_method           TEXT        NOT NULL DEFAULT 'snapshot_diff',
    confidence                  NUMERIC(4,3),
    notes                       TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_holding_trades_trader_date
    ON holding_trades (trader_alias, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_holding_trades_symbol
    ON holding_trades (symbol, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_holding_trades_event
    ON holding_trades (event_type);

-- ---------------------------------------------------------------------
-- 自动 updated_at 触发器
-- ---------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_trader_profile_updated_at ON trader_profile;
CREATE TRIGGER trg_trader_profile_updated_at
    BEFORE UPDATE ON trader_profile
    FOR EACH ROW EXECUTE FUNCTION strategy_mining.touch_updated_at();

DROP TRIGGER IF EXISTS trg_holding_snapshots_updated_at ON holding_snapshots;
CREATE TRIGGER trg_holding_snapshots_updated_at
    BEFORE UPDATE ON holding_snapshots
    FOR EACH ROW EXECUTE FUNCTION strategy_mining.touch_updated_at();
