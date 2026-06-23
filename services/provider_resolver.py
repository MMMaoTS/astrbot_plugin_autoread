"""模型 Provider 解析器。

根据 model_role (cheap/quality/current_session/default) 和 stage 决定 provider_id。
兼容旧 reading_model_mode / reading_provider_id 配置。
"""

from astrbot.api import logger


class ProviderResolver:
    """统一的 provider 决策层。

    入口模块不应自行决定模型，必须通过本 Resolver。
    """

    def __init__(self, context, config_service):
        self.context = context
        self.config_service = config_service

    # ==================================================================
    # 主入口
    # ==================================================================

    async def resolve_provider_id(
        self,
        *,
        umo: str | None = None,
        model_role: str = "current_session",
        stage: str = "chunk_note",
    ) -> str | None:
        """根据 model_role 解析 provider_id。

        Args:
            umo: 当前 unified_msg_origin。仅 current_session 需要。
            model_role: cheap / quality / current_session / default
            stage: 阅读阶段，用于日志和兼容 fallback

        Returns:
            provider_id 字符串，或 None 表示无可用模型。
        """
        strategy = self.config_service.get("reading_model_strategy", "two_stage")

        # ---- current_session ----
        if model_role == "current_session":
            return await self._resolve_current_session(umo)

        # ---- default ----
        if model_role == "default":
            return await self._resolve_default()

        # ---- cheap / quality under two_stage ----
        if strategy == "two_stage":
            if model_role == "cheap":
                pid = self.config_service.get("cheap_provider_id", "")
                if pid.strip():
                    logger.info(f"[AutoRead Provider] cheap provider: {pid} (stage={stage})")
                    return pid.strip()
                # fallback: 尝试旧字段，再尝试 current_session
                pid = self.config_service.get("reading_provider_id", "")
                if pid.strip():
                    logger.info(f"[AutoRead Provider] cheap fallback to reading_provider_id: {pid}")
                    return pid.strip()
                return await self._resolve_current_session(umo)

            if model_role == "quality":
                pid = self.config_service.get("quality_provider_id", "")
                if pid.strip():
                    logger.info(f"[AutoRead Provider] quality provider: {pid} (stage={stage})")
                    return pid.strip()
                # fallback: 尝试旧字段
                pid = self.config_service.get("reading_provider_id", "")
                if pid.strip():
                    logger.info(f"[AutoRead Provider] quality fallback to reading_provider_id: {pid}")
                    return pid.strip()
                return await self._resolve_current_session(umo)

        # ---- fixed_single ----
        if strategy == "fixed_single":
            pid = self.config_service.get("single_provider_id", "")
            if not pid.strip():
                pid = self.config_service.get("reading_provider_id", "")
            if pid.strip():
                logger.info(f"[AutoRead Provider] single provider: {pid}")
                return pid.strip()
            return await self._resolve_current_session(umo)

        # ---- current_session strategy (legacy) ----
        if strategy == "current_session":
            return await self._resolve_current_session(umo)

        # fallback
        return await self._resolve_current_session(umo)

    # ==================================================================
    # 内部
    # ==================================================================

    async def _resolve_current_session(self, umo: str | None) -> str | None:
        if umo:
            try:
                pid = await self.context.get_current_chat_provider_id(umo=umo)
                if pid:
                    logger.info(f"[AutoRead Provider] Current session provider: {pid}")
                    return pid
            except Exception as exc:
                logger.warning(f"[AutoRead Provider] Failed to get current session provider: {exc}")

        if self.config_service.get("fallback_to_current_session_provider", True):
            return await self._resolve_default()

        raise RuntimeError(
            "No current session provider available and fallback is disabled."
        )

    async def _resolve_default(self) -> str | None:
        for method_name in ("get_default_provider_id", "get_default_chat_provider_id"):
            method = getattr(self.context, method_name, None)
            if callable(method):
                try:
                    pid = await method()
                    if pid:
                        logger.info(f"[AutoRead Provider] Default provider via {method_name}: {pid}")
                        return pid
                except Exception as exc:
                    logger.debug(f"[AutoRead Provider] {method_name} failed: {exc}")
        return None

    # ==================================================================
    # Provider 列表
    # ==================================================================

    async def list_providers(self) -> list[dict]:
        for method_name in ("list_providers", "get_providers", "get_all_providers"):
            method = getattr(self.context, method_name, None)
            if callable(method):
                try:
                    result = await method()
                    if isinstance(result, list):
                        items = []
                        for p in result:
                            if isinstance(p, dict):
                                items.append({
                                    "provider_id": p.get("provider_id", p.get("id", p.get("name", ""))),
                                    "display_name": p.get("display_name", p.get("name", p.get("provider_id", ""))),
                                    "type": p.get("type", p.get("provider_type", "chat")),
                                    "available": p.get("available", p.get("enabled", True)),
                                })
                        if items:
                            logger.info(f"[AutoRead Provider] Listed {len(items)} providers")
                            return items
                except Exception as exc:
                    logger.debug(f"[AutoRead Provider] {method_name} failed: {exc}")
        return []
