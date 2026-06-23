"""模型路由器。

只负责阶段决策与阈值判断，不直接读取 provider_id。
provider_id 由 ProviderResolver 按 stage 解析。
"""

from astrbot.api import logger


class ModelRouter:
    """阶段路由决策。

    - 判断当前阶段是否需要复核
    - 判断 importance_score 是否触发 deeper_review
    """

    def __init__(self, config_service):
        self.config_service = config_service

    async def should_deeper_review(
        self,
        *,
        stage: str,
        importance_score: float = 0.0,
        needs_deeper_review: bool = False,
        user_requested_deep_view: bool = False,
    ) -> bool:
        """判断是否需要使用复核阶段 (chunk_review)。

        满足以下任一条件返回 True:
        - 用户主动要求深入看法
        - needs_deeper_review 为 true 且重要性超过阈值且启用复核
        """
        if user_requested_deep_view:
            return True

        if not self.config_service.get("enable_deeper_review", True):
            return False

        if not needs_deeper_review:
            return False

        threshold = float(self.config_service.get("importance_threshold", 0.75))
        if importance_score < threshold:
            logger.info(
                f"[AutoRead Router] Importance {importance_score:.2f} below threshold {threshold}, skip review"
            )
            return False

        logger.info(f"[AutoRead Router] Triggering deeper review: importance={importance_score:.2f}")
        return True
