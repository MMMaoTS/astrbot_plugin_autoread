"""插件配置服务。

职责：
- 合并默认值、框架配置、WebUI override
- 提供统一的配置读写接口
- 校验 WebUI 传入的配置 patch
- 持久化 settings_override.json
"""

import json
import os
from pathlib import Path

from astrbot.api import logger

# WebUI 可修改的配置白名单
_SETTINGS_WHITELIST = frozenset({
    # 基础
    "enabled",
    "enable_llm_tools",
    "allow_llm_read_next",
    "default_interval_minutes",
    "worker_tick_seconds",
    "chunk_size",
    "chunk_overlap",
    "auto_share_mode",
    "allow_url_import",
    "memory_backend",
    "reading_persona_prompt",
    # WebUI
    "webui_enabled",
    "webui_upload_enabled",
    "webui_max_upload_mb",
    "webui_allow_book_delete",
    "webui_notes_export_enabled",
    # 旧模型配置 (兼容)
    "reading_model_mode",
    "reading_provider_id",
    "reading_provider_display_name",
    "fallback_to_current_session_provider",
    # 新模型策略
    "reading_model_strategy",
    # cheap provider
    "cheap_provider_id",
    "cheap_provider_display_name",
    # quality provider
    "quality_provider_id",
    "quality_provider_display_name",
    # single provider
    "single_provider_id",
    "single_provider_display_name",
    # model_role 分配
    "chunk_note_model_role",
    "chapter_note_model_role",
    "important_note_model_role",
    "final_review_model_role",
    "memory_note_model_role",
    # 升级策略
    "pro_upgrade_importance_threshold",
    "enable_deeper_review",
    "max_deeper_reviews_per_chapter",
})

# 配置校验规则
_ROLE_OPTIONS = ("cheap", "quality", "current_session", "default")
_SETTINGS_VALIDATORS = {
    "reading_model_mode": lambda v: v in ("current_session", "fixed_provider", "default"),
    "reading_model_strategy": lambda v: v in ("current_session", "fixed_single", "two_stage"),
    "auto_share_mode": lambda v: v in ("none", "daily", "chapter", "every_step", "finish"),
    "memory_backend": lambda v: v in ("none", "angel_memory", "livingmemory"),
    "chunk_note_model_role": lambda v: v in _ROLE_OPTIONS,
    "chapter_note_model_role": lambda v: v in _ROLE_OPTIONS,
    "important_note_model_role": lambda v: v in _ROLE_OPTIONS,
    "final_review_model_role": lambda v: v in _ROLE_OPTIONS,
    "memory_note_model_role": lambda v: v in _ROLE_OPTIONS,
    "webui_max_upload_mb": lambda v: isinstance(v, (int, float)) and 1 <= v <= 100,
    "chunk_size": lambda v: isinstance(v, (int, float)) and 100 <= v <= 50000,
    "chunk_overlap": lambda v: isinstance(v, (int, float)) and 0 <= v <= 5000,
    "default_interval_minutes": lambda v: isinstance(v, (int, float)) and 1 <= v <= 10080,
    "worker_tick_seconds": lambda v: isinstance(v, (int, float)) and 10 <= v <= 3600,
    "reading_provider_id": lambda v: isinstance(v, str) and len(v) <= 200,
    "reading_provider_display_name": lambda v: isinstance(v, str) and len(v) <= 120,
    "cheap_provider_id": lambda v: isinstance(v, str) and len(v) <= 200,
    "cheap_provider_display_name": lambda v: isinstance(v, str) and len(v) <= 120,
    "quality_provider_id": lambda v: isinstance(v, str) and len(v) <= 200,
    "quality_provider_display_name": lambda v: isinstance(v, str) and len(v) <= 120,
    "single_provider_id": lambda v: isinstance(v, str) and len(v) <= 200,
    "single_provider_display_name": lambda v: isinstance(v, str) and len(v) <= 120,
    "reading_persona_prompt": lambda v: isinstance(v, str) and len(v) <= 4000,
    "pro_upgrade_importance_threshold": lambda v: isinstance(v, (int, float)) and 0 <= v <= 1,
    "max_deeper_reviews_per_chapter": lambda v: isinstance(v, (int, float)) and 0 <= v <= 20,
}


class ConfigService:
    """插件配置统一管理。

    优先级：settings_override.json > 框架 AstrBotConfig > _conf_schema 默认值
    """

    def __init__(self, raw_config, data_dir: Path):
        self._raw_config = raw_config  # AstrBotConfig 实例
        self.data_dir = data_dir
        self.override_path = data_dir / "settings_override.json"
        self._override_cache: dict | None = None

    # ------------------------------------------------------------------
    # 覆写文件读写
    # ------------------------------------------------------------------

    def _load_override(self) -> dict:
        if self._override_cache is not None:
            return self._override_cache
        if not self.override_path.exists():
            self._override_cache = {}
            return self._override_cache
        try:
            text = self.override_path.read_text(encoding="utf-8")
            self._override_cache = json.loads(text)
        except (json.JSONDecodeError, OSError):
            self._override_cache = {}
        return self._override_cache

    def _save_override(self, data: dict) -> None:
        tmp = self.override_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.override_path)
        self._override_cache = data

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------

    def get(self, key: str, default=None):
        """同步读取有效配置值（优先级：override > raw_config）。"""
        override = self._load_override()
        if key in override:
            return override[key]
        try:
            return self._raw_config.get(key, default)
        except Exception:
            return default

    async def get_async(self, key: str, default=None):
        return self.get(key, default)

    def get_effective_config(self) -> dict:
        """返回完整有效配置（合并 override + raw_config + 白名单过滤）。"""
        result = {}
        # 从 raw_config 读取所有已知键
        for key in _SETTINGS_WHITELIST:
            result[key] = self.get(key, None)
        return result

    # ------------------------------------------------------------------
    # 配置更新
    # ------------------------------------------------------------------

    async def update_settings(self, patch: dict) -> dict:
        """更新 WebUI 传来的配置 patch。

        返回更新后的完整有效配置。
        """
        validated = await self.validate_settings_patch(patch)
        override = dict(self._load_override())
        override.update(validated)
        self._save_override(override)
        logger.info(f"[AutoRead Config] Updated {len(validated)} settings: {list(validated.keys())}")
        return self.get_effective_config()

    async def validate_settings_patch(self, patch: dict) -> dict:
        """校验配置 patch，返回清洗后的 dict。抛出 ValueError 若校验失败。"""
        cleaned = {}

        for key, value in patch.items():
            if key not in _SETTINGS_WHITELIST:
                raise ValueError(f"不允许修改的配置项: {key}")

            # 类型检查
            validator = _SETTINGS_VALIDATORS.get(key)
            if validator and not validator(value):
                raise ValueError(f"配置项 {key} 的值不合法: {value!r}")

            # 布尔类型强制检查
            schema_bool_keys = {
                "enabled", "enable_llm_tools", "allow_llm_read_next",
                "allow_url_import", "webui_enabled", "webui_upload_enabled",
                "webui_allow_book_delete", "webui_notes_export_enabled",
                "fallback_to_current_session_provider",
                "enable_deeper_review",
            }
            if key in schema_bool_keys and not isinstance(value, bool):
                raise ValueError(f"配置项 {key} 必须是布尔值")

            cleaned[key] = value

        return cleaned
