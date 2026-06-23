"""插件配置服务。以 AstrBotConfig 为唯一主配置源。"""

import json
from pathlib import Path

from astrbot.api import logger

# 分组字段白名单
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
        "model_strategy",
        "reader_provider_id", "thinker_provider_id", "single_provider_id",
        "enable_stage_routing",
        "stage_chunk_note_provider_id",
        "stage_chunk_review_provider_id",
        "stage_chapter_note_provider_id",
        "stage_final_review_provider_id",
        "stage_memory_note_provider_id",
        "stage_user_share_provider_id",
        "enable_deeper_review", "importance_threshold", "max_reviews_per_chapter",
    }),
    "WebUI_Settings": frozenset({
        "webui_enabled", "webui_upload_enabled", "webui_max_upload_mb",
        "webui_allow_book_delete", "webui_notes_export_enabled",
    }),
}

# 扁平 key -> 分组
_FLAT_TO_GROUP = {}
for _g, _ks in _GROUP_KEYS.items():
    for _k in _ks:
        _FLAT_TO_GROUP[_k] = _g

_VALIDATORS = {
    "model_strategy": lambda v: v in ("current_session", "single", "dual"),
    "auto_share_mode": lambda v: v in ("none", "daily", "chapter", "every_step", "finish"),
    "memory_backend": lambda v: v in ("none", "angel_memory", "livingmemory"),
    "webui_max_upload_mb": lambda v: isinstance(v, (int, float)) and 1 <= v <= 100,
    "chunk_size": lambda v: isinstance(v, (int, float)) and 100 <= v <= 50000,
    "chunk_overlap": lambda v: isinstance(v, (int, float)) and 0 <= v <= 5000,
    "default_interval_minutes": lambda v: isinstance(v, (int, float)) and 1 <= v <= 10080,
    "worker_tick_seconds": lambda v: isinstance(v, (int, float)) and 10 <= v <= 3600,
    "importance_threshold": lambda v: isinstance(v, (int, float)) and 0 <= v <= 1,
    "max_reviews_per_chapter": lambda v: isinstance(v, (int, float)) and 0 <= v <= 20,
    "reading_persona_prompt": lambda v: isinstance(v, str) and len(v) <= 4000,
}

_BOOL_KEYS = frozenset({
    "enabled", "enable_llm_tools", "allow_llm_read_next",
    "allow_url_import", "webui_enabled", "webui_upload_enabled",
    "webui_allow_book_delete", "webui_notes_export_enabled",
    "enable_stage_routing", "enable_deeper_review",
})

# 列表类型字段（AstrBot 原生设置面板可能将 list 存为 JSON 字符串）
_LIST_KEYS = frozenset({
    "allowed_extensions",
})


def _normalize_list_value(value):
    """将 JSON 字符串或逗号分隔字符串规范化为 Python list。"""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        # 尝试 JSON 解析（AstrBot 可能将 list 序列化为 JSON 字符串）
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
        # 逗号分隔回退
        return [item.strip() for item in stripped.split(",") if item.strip()]
    return []


def _load_schema_defaults() -> dict[str, dict]:
    """Load grouped defaults from the plugin configuration schema.

    Returns:
        Grouped default values keyed by configuration group name.
    """
    schema_path = Path(__file__).resolve().parents[1] / "_conf_schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"[AutoRead Config] Failed to load schema defaults: {exc}")
        return {}

    defaults = {}
    for group, group_schema in schema.items():
        items = group_schema.get("items", {}) if isinstance(group_schema, dict) else {}
        defaults[group] = {
            key: item.get("default")
            for key, item in items.items()
            if isinstance(item, dict) and "default" in item
        }
    return defaults


_SCHEMA_DEFAULTS = _load_schema_defaults()


class ConfigService:
    """插件配置统一管理。以 AstrBotConfig 为唯一主配置源。"""

    def __init__(self, config):
        self._config = config

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def get(self, key: str, default=None):
        """读取配置值。支持点号路径如 Model_Settings.model_strategy，也支持扁平 key。

        列表类型字段会自动规范化（JSON 字符串 → list）。
        """
        if "." in key:
            parts = key.split(".")
            node = self._config
            for p in parts:
                if isinstance(node, dict):
                    node = node.get(p)
                else:
                    return default
            result = node if node is not None else default
        else:
            group = _FLAT_TO_GROUP.get(key)
            if group:
                group_dict = self._config.get(group, {})
                if isinstance(group_dict, dict) and key in group_dict:
                    result = group_dict[key]
                else:
                    result = default
            else:
                val = self._config.get(key)
                result = val if val is not None else default

        if key in _LIST_KEYS and result is not None:
            return _normalize_list_value(result)
        return result

    async def get_async(self, key: str, default=None):
        return self.get(key, default)

    def get_effective_config(self) -> dict:
        result = {}
        for group, keys in _GROUP_KEYS.items():
            group_dict = self._config.get(group, {})
            if not isinstance(group_dict, dict):
                group_dict = {}
            group_defaults = _SCHEMA_DEFAULTS.get(group, {})
            normalized = {}
            for k in keys:
                v = group_dict.get(k, group_defaults.get(k))
                if k in _LIST_KEYS and v is not None:
                    v = _normalize_list_value(v)
                normalized[k] = v
            result[group] = normalized
        return result

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------

    async def update_settings(self, patch: dict) -> dict:
        validated = await self.validate_settings_patch(patch)
        self._apply_patch(validated)
        self._config.save_config()
        logger.info("[AutoRead Config] Saved settings to config")
        return self.get_effective_config()

    def _apply_patch(self, patch: dict) -> None:
        for group, items in patch.items():
            if group in _GROUP_KEYS and isinstance(items, dict):
                existing = self._config.get(group, {})
                if not isinstance(existing, dict):
                    existing = {}
                existing.update(items)
                self._config[group] = existing

    async def validate_settings_patch(self, patch: dict) -> dict:
        cleaned_by_group = {}
        for top_key, top_val in patch.items():
            if top_key in _GROUP_KEYS and isinstance(top_val, dict):
                group = top_key
                cleaned = {}
                for key, value in top_val.items():
                    if key not in _GROUP_KEYS.get(group, frozenset()):
                        raise ValueError(f"不允许修改的配置项: {group}.{key}")
                    value = self._normalize_value(key, value)
                    self._validate_key(key, value)
                    cleaned[key] = value
                if cleaned:
                    cleaned_by_group[group] = cleaned
            elif _FLAT_TO_GROUP.get(top_key):
                group = _FLAT_TO_GROUP[top_key]
                top_val = self._normalize_value(top_key, top_val)
                self._validate_key(top_key, top_val)
                cleaned_by_group.setdefault(group, {})[top_key] = top_val
            else:
                raise ValueError(f"未知配置项: {top_key}")
        return cleaned_by_group

    @staticmethod
    def _normalize_value(key: str, value):
        """保存前规范化值类型。"""
        if key in _LIST_KEYS:
            return _normalize_list_value(value)
        return value

    def _validate_key(self, key: str, value) -> None:
        validator = _VALIDATORS.get(key)
        if validator and not validator(value):
            raise ValueError(f"配置项 {key} 的值不合法: {value!r}")
        if key in _BOOL_KEYS and not isinstance(value, bool):
            raise ValueError(f"配置项 {key} 必须是布尔值")
