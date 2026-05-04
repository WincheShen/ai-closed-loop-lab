"""全局配置管理 — 从 .env 和 YAML 加载配置。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# 加载 .env
load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Config:
    """单例配置对象，运行期间只加载一次。"""

    _instance: Config | None = None
    _data: dict[str, Any]

    def __new__(cls) -> Config:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = cls._load()
        return cls._instance

    @classmethod
    def _load(cls) -> dict[str, Any]:
        """从 .env 和 config/*.yaml 加载配置。"""
        data: dict[str, Any] = {
            # --- LLM ---
            "openai_api_key": os.getenv("OPENAI_API_KEY"),
            "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY"),
            "google_api_key": os.getenv("GOOGLE_API_KEY"),
            "azure_endpoint": os.getenv("AZURE_OPENAI_ENDPOINT"),
            "azure_api_key": os.getenv("AZURE_OPENAI_API_KEY"),
            "azure_api_version": os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
            "default_llm_provider": os.getenv("DEFAULT_LLM_PROVIDER", "openai"),
            "deep_think_model": os.getenv("DEEP_THINK_MODEL", "gpt-4o"),
            "quick_think_model": os.getenv("QUICK_THINK_MODEL", "gpt-4o-mini"),
            # --- Trading ---
            "trading_mode": os.getenv("TRADING_MODE", "mock"),
            "broker_api_url": os.getenv("BROKER_API_URL"),
            "broker_api_key": os.getenv("BROKER_API_KEY"),
            "broker_secret": os.getenv("BROKER_SECRET"),
            "broker_account": os.getenv("BROKER_ACCOUNT"),
            "initial_capital": float(os.getenv("INITIAL_CAPITAL", "300000")),
            "max_position_pct": float(os.getenv("MAX_POSITION_PCT", "0.10")),
            "default_stop_loss_pct": float(os.getenv("DEFAULT_STOP_LOSS_PCT", "0.05")),
            # --- Market Data ---
            "tushare_token": os.getenv("TUSHARE_TOKEN"),
            "eastmoney_token": os.getenv("EASTMONEY_TOKEN"),
            # --- Social Media ---
            "xhs_skills_dir": os.getenv("XHS_SKILLS_DIR", "vendor/xiaohongshu-skills"),
            # --- Storage ---
            "db_path": os.getenv("DB_PATH", str(_PROJECT_ROOT / "data" / "central_brain.db")),
            "vector_db_path": os.getenv(
                "VECTOR_DB_PATH", str(_PROJECT_ROOT / "data" / "vector_memory.db")
            ),
            "data_dir": os.getenv("DATA_DIR", str(_PROJECT_ROOT / "data")),
            "results_dir": os.getenv("RESULTS_DIR", str(_PROJECT_ROOT / "reports")),
            # --- System ---
            "log_level": os.getenv("LOG_LEVEL", "INFO"),
            "timezone": os.getenv("TIMEZONE", "Asia/Shanghai"),
        }

        # 加载 YAML 配置（覆盖 .env）
        config_dir = _PROJECT_ROOT / "config"
        for yaml_file in sorted(config_dir.glob("*.yaml")):
            with open(yaml_file, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f) or {}
                data.update(yaml_data)

        return data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def to_dict(self) -> dict[str, Any]:
        return self._data.copy()


def cfg() -> Config:
    """获取全局配置实例的便捷函数。"""
    return Config()
