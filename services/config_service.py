"""插件配置服务。

统一读写 AstrBotConfig，不再依赖独立的 settings_override.json。
"""

import json
import os
from pathlib import Path

from astrbot.api import logger

# 分组前缀到 schema 字段的映射
_GROUP_PREFIXES = {
    "Basic_Settings": "Basic_Settings",
    "Reading_Settings": "Reading_Settings",
    "Model_Settings": "Model_Settings",
    "WebUI_Settings": "WebUI_Settings",
}

# 分组内的字段白名单
_GROUP_KEYS = {
    "Basic_Settings": frozenset({
        "enabled", "default_interval_minutes", "worker_tick_seconds",
        "auto_share_mode", "enable_llm_tools", "allow_llm_read_next",
    }),
    "Reading_Settings": frozenset({
        "chunk_size", "chunk_overlap", "reading_persona_prompt",
        "max_notes_per_book", "allow_url_import", "allowed_extensions",
        "memory_backend",
    }),
    "Model_Settings": frozenset({
        "reading_model_strategy",
        "cheap_provider_id", "quality_provider_id", "single_provider_id",
        "chunk_note_model_role", "chapter_note_model_role",
        "important_note_model_role", "final_review_model_role",
        "memory_note_model_role",
        "pro_upgrade_importance_threshold", "enable_deeper_review",
        "max_deeper_reviews_per_chapter", "fallback_to_current_session_provider",
        # 旧字段兼容（在 grouped config 下做平铺兼容）
        "reading_model_mode", "reading_provider_id", "reading_provider_display_name",
        "cheap_provider_display_name", "quality_provider_display_name",
        "single_provider_display_name",
    }),
    "WebUI_Settings": frozenset({
        "webui_enabled", "webui_upload_enabled", "webui_max_upload_mb",
        "webui_allow_book_delete", "webui_notes_export_enabled",
    }),
}

# 所有已知的 [group, key] 组合
_ALL_KNOWN = set()
for _g, _ks in _GROUP_KEYS.items():
    for _k in _ks:
        _ALL_KNOWN.add((_g, _k))

# 扁平 key -> 分组
_FLAT_TO_GROUP = {}
for _g, _ks in _GROUP_KEYS.items():
    for _k in _ks:
        _FLAT_TO_GROUP[_k] = _g

_ROLE_OPTIONS = ("cheap", "quality", "current_session", "default")

_VALIDATORS = {
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
    "pro_upgrade_importance_threshold": lambda v: isinstance(v, (int, float)) and 0 <= v <= 1,
    "max_deeper_reviews_per_chapter": lambda v: isinstance(v, (int, float)) and 0 <= v <= 20,
    "reading_persona_prompt": lambda v: isinstance(v, str) and len(v) <= 4000,
}

_BOOL_KEYS = frozenset({
    "enabled", "enable_llm_tools", "allow_llm_read_next",
    "allow_url_import", "webui_enabled", "webui_upload_enabled",
    "webui_allow_book_delete", "webui_notes_export_enabled",
    "fallback_to_current_session_provider", "enable_deeper_review",
})


class ConfigService:
    """插件配置统一管理。

    优先级: AstrBotConfig (唯一主配置源)
    启动时执行一次旧 settings_override.json 迁移。
    """

    def __init__(self, config, data_dir: Path):
        self._config = config  # AstrBotConfig dict 实例
        self.data_dir = data_dir
        self._override_path = data_dir / "settings_override.json"
        # 迁移旧 override（仅一次）
        self._migrate_old_override()

    # ------------------------------------------------------------------
    # 旧配置迁移
    # ------------------------------------------------------------------

    def _migrate_old_override(self) -> None:
        if not self._override_path.exists():
            return
        try:
            old = json.loads(self._override_path.read_text(encoding="utf-8"))
            if not old or not isinstance(old, dict):
                return
            migrated = 0
            for flat_key, value in old.items():
                group = _FLAT_TO_GROUP.get(flat_key)
                if group is None:
                    continue
                # 只在官方 config 为空时才迁移
                existing = self._config.get(group, {})
                if isinstance(existing, dict) and not existing.get(flat_key):
                    existing[flat_key] = value
                    self._config[group] = existing
                    migrated += 1

            if migrated > 0:
                self._config.save_config()
                logger.info(f"[AutoRead Config] Migrated {migrated} keys from old settings_override.json")

            # 重命名旧文件防止重复迁移
            migrated_path = self._override_path.with_suffix(".migrated.json")
            os.rename(str(self._override_path), str(migrated_path))
            logger.info("[AutoRead Config] Renamed old override -> .migrated.json")
        except Exception as exc:
            logger.warning(f"[AutoRead Config] Migration skipped: {exc}")

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------

    def get(self, key: str, default=None):
        """从分组配置读取单个 key。

        自动查找 key 所属分组。例如 get("chunk_size") 会查找 Reading_Settings.chunk_size。
        """
        group = _FLAT_TO_GROUP.get(key)
        if group:
            group_dict = self._config.get(group, {})
            if isinstance(group_dict, dict) and key in group_dict:
                return group_dict[key]
        # fallback: 直接查顶层
        val = self._config.get(key)
        if val is not None:
            return val
        return default

    async def get_async(self, key: str, default=None):
        return self.get(key, default)

    def get_effective_config(self) -> dict:
        """返回当前完整有效配置（分组结构）。"""
        result = {}
        for group, keys in _GROUP_KEYS.items():
            group_dict = self._config.get(group, {})
            if not isinstance(group_dict, dict):
                group_dict = {}
            result[group] = {}
            for key in keys:
                result[group][key] = group_dict.get(key, None)
        return result

    # ------------------------------------------------------------------
    # 配置更新
    # ------------------------------------------------------------------

    async def update_settings(self, patch: dict) -> dict:
        """更新配置 patch（支持分组结构或扁平结构）。

        直接写入 AstrBotConfig 并调用 save_config()。
        """
        validated = await self.validate_settings_patch(patch)
        self._apply_patch(validated)
        self._config.save_config()
        logger.info(f"[AutoRead Config] Saved {len(validated)} setting(s) to config")
        return dict(self._config)

    def _apply_patch(self, patch: dict) -> None:
        for group, items in patch.items():
            if group in _GROUP_KEYS and isinstance(items, dict):
                existing = self._config.get(group, {})
                if not isinstance(existing, dict):
                    existing = {}
                existing.update(items)
                self._config[group] = existing

    async def validate_settings_patch(self, patch: dict) -> dict:
        """校验配置 patch（支持分组结构 {Group: {key: val}} 和扁平结构 {key: val}）。"""
        cleaned_by_group = {}

        for top_key, top_val in patch.items():
            if top_key in _GROUP_KEYS and isinstance(top_val, dict):
                # 分组结构: {Model_Settings: {cheap_provider_id: ...}}
                group = top_key
                cleaned = {}
                for key, value in top_val.items():
                    if key not in _GROUP_KEYS.get(group, frozenset()):
                        raise ValueError(f"不允许修改的配置项: {group}.{key}")
                    self._validate_key(key, value)
                    cleaned[key] = value
                if cleaned:
                    cleaned_by_group[group] = cleaned
            elif _FLAT_TO_GROUP.get(top_key):
                # 扁平 key: {cheap_provider_id: ...}
                group = _FLAT_TO_GROUP[top_key]
                self._validate_key(top_key, top_val)
                cleaned_by_group.setdefault(group, {})[top_key] = top_val
            else:
                raise ValueError(f"未知配置项: {top_key}")

        return cleaned_by_group

    def _validate_key(self, key: str, value) -> None:
        validator = _VALIDATORS.get(key)
        if validator and not validator(value):
            raise ValueError(f"配置项 {key} 的值不合法: {value!r}")
        if key in _BOOL_KEYS and not isinstance(value, bool):
            raise ValueError(f"配置项 {key} 必须是布尔值")
