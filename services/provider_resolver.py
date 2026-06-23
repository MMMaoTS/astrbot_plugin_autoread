"""模型 Provider 解析器。

根据 model_strategy 和 stage 决定实际使用的 provider_id。
不做旧字段回退，不依赖 display_name。
"""

from astrbot.api import logger


class ProviderResolver:
    """统一的 provider 决策层。"""

    def __init__(self, context, config_service):
        self.context = context
        self.config_service = config_service

    async def resolve_provider_id(
        self,
        *,
        umo: str | None = None,
        stage: str = "chunk_note",
    ) -> str | None:
        """根据 model_strategy + stage 解析 provider_id。

        返回 provider_id 或 None。
        """
        strategy = self.config_service.get("model_strategy", "dual")

        # ---- current_session ----
        if strategy == "current_session":
            return await self._resolve_current_session(umo)

        # ---- single ----
        if strategy == "single":
            pid = self.config_service.get("single_provider_id", "")
            if pid.strip():
                return pid.strip()
            logger.error("[AutoRead Provider] single_provider_id is empty")
            return None

        # ---- dual (默认) ----
        return self._resolve_dual(stage, umo)

    # ------------------------------------------------------------------
    # dual 策略
    # ------------------------------------------------------------------

    def _resolve_dual(self, stage: str, umo: str | None) -> str | None:
        enable_stage = self.config_service.get("enable_stage_routing", False)

        # 自定义阶段路由
        if enable_stage:
            stage_key = f"stage_{stage}_provider_id"
            pid = self.config_service.get(stage_key, "")
            if pid.strip():
                logger.info(f"[AutoRead Provider] stage={stage} custom provider: {pid}")
                return pid.strip()

        # 默认分工：reader vs thinker
        reader_stages = {"chunk_note"}
        thinker_stages = {
            "chunk_review", "chapter_note", "final_review",
            "memory_note", "user_visible_share",
        }

        if stage in reader_stages:
            pid = self.config_service.get("reader_provider_id", "")
            if not pid.strip():
                logger.error("[AutoRead Provider] reader_provider_id is empty")
                return None
            logger.info(f"[AutoRead Provider] reader provider: {pid} (stage={stage})")
            return pid.strip()

        if stage in thinker_stages:
            pid = self.config_service.get("thinker_provider_id", "")
            if not pid.strip():
                logger.error("[AutoRead Provider] thinker_provider_id is empty")
                return None
            logger.info(f"[AutoRead Provider] thinker provider: {pid} (stage={stage})")
            return pid.strip()

        # fallback for unknown stage
        pid = self.config_service.get("reader_provider_id", "")
        if pid.strip():
            return pid.strip()
        return None

    # ------------------------------------------------------------------
    # current_session
    # ------------------------------------------------------------------

    async def _resolve_current_session(self, umo: str | None) -> str | None:
        if umo:
            try:
                pid = await self.context.get_current_chat_provider_id(umo=umo)
                if pid:
                    logger.info(f"[AutoRead Provider] Current session provider: {pid}")
                    return pid
            except Exception as exc:
                logger.warning(f"[AutoRead Provider] Current session error: {exc}")
        return None

    # ------------------------------------------------------------------
    # Provider 列表
    # ------------------------------------------------------------------

    async def list_providers(self) -> list[dict]:
        for method_name in ("list_providers", "get_providers", "get_all_providers"):
            method = getattr(self.context, method_name, None)
            if callable(method):
                try:
                    result = await method()
                    if isinstance(result, list):
                        return [
                            {
                                "provider_id": p.get("provider_id", p.get("id", p.get("name", ""))),
                                "display_name": p.get("display_name", p.get("name", "")),
                                "type": p.get("type", "chat"),
                                "available": p.get("available", True),
                            }
                            for p in result if isinstance(p, dict)
                        ]
                except Exception:
                    pass
        return []
