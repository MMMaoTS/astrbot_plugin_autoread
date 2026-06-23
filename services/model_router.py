"""模型路由器。

根据阅读阶段、重要性评分、是否需要深入复核等条件，决定使用 cheap 还是 quality 模型。
不直接调用模型 — 只返回 model_role 字符串。
"""

from astrbot.api import logger

# 默认阶段 -> 模型角色映射
_DEFAULT_STAGE_ROLE = {
    "chunk_note":             "cheap",
    "chunk_review":           "quality",
    "chapter_note":           "cheap",
    "important_chapter_note": "quality",
    "book_note":              "quality",
    "user_visible_share":     "quality",
    "final_review":           "quality",
    "memory_note":            "quality",
}


class ModelRouter:
    """统一模型路由决策。

    入口模块不应自行决定模型角色，必须通过本 Router。
    """

    def __init__(self, config_service):
        self.config_service = config_service

    async def select_model_role(
        self,
        *,
        stage: str,
        importance_score: float | None = None,
        needs_deeper_review: bool | None = None,
        user_requested_deep_view: bool = False,
    ) -> str:
        """根据 context 返回应使用的 model_role。

        返回 "cheap" 或 "quality"。
        """
        strategy = self.config_service.get("reading_model_strategy", "two_stage")

        # 单模型策略：总是用质量模型
        if strategy == "current_session":
            return "current_session"
        if strategy == "fixed_single":
            return "quality"

        # two_stage 策略：按阶段 + 条件决定
        # 用户主动要求深入看法
        if user_requested_deep_view:
            logger.info(f"[AutoRead Router] User requested deep view, using quality for {stage}")
            return "quality"

        # chunks_review 总是 quality
        if stage == "chunk_review":
            return "quality"

        # 重要章节 / 全书总结 / final_review -> quality
        if stage in ("important_chapter_note", "book_note", "final_review", "memory_note"):
            role = self.config_service.get("important_note_model_role", "quality")
            logger.info(f"[AutoRead Router] Stage {stage} -> {role}")
            return role

        # chunk_note 条件升级
        if stage == "chunk_note":
            # 条件 1: needs_deeper_review
            if needs_deeper_review:
                threshold = float(self.config_service.get("pro_upgrade_importance_threshold", 0.75))
                if importance_score is not None and importance_score >= threshold:
                    if self.config_service.get("enable_deeper_review", True):
                        logger.info(
                            f"[AutoRead Router] Upgrading chunk_note to quality: "
                            f"importance={importance_score:.2f} >= {threshold}"
                        )
                        return "quality"

            # 默认 cheap
            return self.config_service.get("chunk_note_model_role", "cheap")

        # chapter_note
        if stage == "chapter_note":
            return self.config_service.get("chapter_note_model_role", "cheap")

        # user_visible_share
        if stage == "user_visible_share":
            return "quality"

        # 其他
        role = _DEFAULT_STAGE_ROLE.get(stage, "cheap")
        logger.info(f"[AutoRead Router] Stage {stage} -> {role} (default)")
        return role
