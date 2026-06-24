"""角色表达层：将结构化读书结果转换为 LLM 可理解的临时上下文。

本层只负责"表达生成"，不负责决定是否写入记忆。
记忆边界由 ReadActionResult 的策略字段和 OutputPolicy 控制。
"""

from astrbot.api import logger

from .read_action_result import (
    ReadActionResult,
    OutputCategory,
    MemoryPolicy,
    get_policy,
)

# ---------------------------------------------------------------------------
# 配置键映射：每个输出分类对应一个开关
# ---------------------------------------------------------------------------

CATEGORY_CONFIG_KEY: dict[OutputCategory, str] = {
    OutputCategory.COMMAND_QUERY: "enable_role_expr_command_query",
    OutputCategory.COMMAND_READ_ACTION: "enable_role_expr_read_action",
    OutputCategory.CONTENT_GENERATION: "enable_role_expr_notes",
    OutputCategory.WORKER_SHARE: "enable_role_expr_worker_share",
    OutputCategory.NATURAL_READING_CHAT: "enable_role_expr_natural_chat",
    # MANAGEMENT 没有开关：始终不启用 LLM 表达
}

# 默认值
CATEGORY_CONFIG_DEFAULTS: dict[OutputCategory, bool] = {
    OutputCategory.COMMAND_QUERY: False,
    OutputCategory.COMMAND_READ_ACTION: False,
    OutputCategory.CONTENT_GENERATION: False,
    OutputCategory.WORKER_SHARE: False,
    OutputCategory.NATURAL_READING_CHAT: True,
}


def _build_status_context(result: ReadActionResult, persona_prompt: str = "") -> str:
    """为 command_query 类构建 LLM 临时上下文。

    使用分块结构: 角色表达约束 → 当前场景 → 事实 → 输出边界 → 风格要求。
    persona_prompt 来自 reading_persona_prompt 配置, 提供角色口吻约束。
    """
    parts: list[str] = []

    # ---- 角色表达约束（来自配置） ----
    if persona_prompt:
        parts.append("【角色表达】")
        parts.append(persona_prompt.strip())
        parts.append("")

    # ---- 当前场景 ----
    parts.append("【当前场景】")
    parts.append("用户刚刚查询了当前的持续阅读状态。这是一次读书插件的状态查询，不是值得长期记住的事件，也不是你自己的阅读经历。")
    parts.append("")

    # ---- 事实 ----
    parts.append("【事实】")
    parts.append("只使用以下事实来回答。不要编造未提供的信息。")
    parts.append("")

    if not result.success:
        parts.append(f"状态: 查询失败 — {result.error or '未知错误'}")
    else:
        if result.book_title:
            parts.append(f"正在读的书: 《{result.book_title}》")
        if result.progress:
            parts.append(f"当前进度: {result.progress}")
        if result.message:
            # 提取关键状态词（READING/PAUSED/DONE）供 LLM 理解
            status_line = result.message.split("\n")[0] if result.message else ""
            if status_line and status_line in ("READING 阅读中", "PAUSED 已暂停", "DONE 已读完"):
                parts.append(f"阅读状态: {status_line}")
        # 也附上原始消息作为参考（帮助 LLM 理解上下文）
        if result.message:
            parts.append(f"原始状态信息: {result.message}")

    parts.append("")

    # ---- 输出边界 ----
    parts.append("【输出边界】")
    parts.append("1. 不要输出字段名、标签或原始数据格式（如「书名:」「进度:」「READING」「PAUSED」）。")
    parts.append("2. 不要提到插件、命令、配置、策略层、内部数据结构、LLM、记忆系统、token。")
    parts.append("3. 不要说「根据系统查询」「根据插件状态」「查询结果显示」这类系统化表达。")
    parts.append("4. 不要把这写成重要回忆或里程碑事件——这只是一次普通的状态查看。")
    parts.append("5. 如果用户没有在读书，自然、简短地告知，不要附带教程或建议（除非当前没有进行中的任务，可以简单提一句如何开始）。")
    parts.append("")

    # ---- 风格要求 ----
    parts.append("【风格】")
    parts.append("1. 像角色本人在聊天中自然回应，不要写成报告、通知、系统消息或说明书。")
    parts.append("2. 状态查询类回复应简短、自然。通常 1-3 句话足够。不要写成长段落。")
    parts.append("3. 如果进度还很低（比如只读了不到 10%），可以自然体现「刚开始读」的感觉，但不要编造具体内容。")
    parts.append("4. 如果书已读完，用符合角色性格的方式表达，但不要假装你记得整本书的全部内容。")
    parts.append("")

    # ---- 最终要求 ----
    parts.append("请根据以上约束，生成一句简短自然的角色回复。")
    return "\n".join(parts)


def _build_step_context(result: ReadActionResult, persona_prompt: str = "") -> str:
    """为 command_read_action 类构建 LLM 临时上下文。

    使用分块结构: 角色表达约束 → 当前场景 → 阅读素材 → 输出边界 → 风格要求。
    """
    parts: list[str] = []

    # ---- 角色表达约束 ----
    if persona_prompt:
        parts.append("【角色表达】")
        parts.append(persona_prompt.strip())
        parts.append("")

    # ---- 当前场景 ----
    parts.append("【当前场景】")
    parts.append("你刚刚读完了一段书。下面是你阅读后的内部笔记。你需要把它转化为自然的阅读分享。这不是写读书报告，而是像真实读者在聊天中分享阶段性想法。")
    parts.append("")

    # ---- 阅读素材 ----
    parts.append("【阅读素材】")
    parts.append("只使用以下素材来分享。不要编造未读到的内容。")
    parts.append("")

    if not result.success:
        parts.append(f"阅读失败: {result.error or '未知错误'}")
    else:
        if result.book_title:
            parts.append(f"正在读的书: 《{result.book_title}》")
        if result.progress:
            parts.append(f"当前进度: {result.progress}")
        if result.summary:
            parts.append(f"本段概括: {result.summary}")
        if result.note_material:
            note = result.note_material
            if note.get("reflection"):
                parts.append(f"当时的感受: {note['reflection'][:200]}")
            if note.get("share_message"):
                parts.append(f"可参考的分享文案: {note['share_message'][:200]}")

    parts.append("")

    # ---- 输出边界 ----
    parts.append("【输出边界】")
    parts.append("1. 不要输出字段名（摘要、细节、反思、书名、进度、章节等）。")
    parts.append("2. 不要提到插件、命令、内部数据结构。")
    parts.append("3. 只有实际读到的内容可以称为已经读到。不要假装读过后文或评价整本书。")
    parts.append("4. 如果当前信息不足以判断整体，诚实体现「目前只读到很前面的部分」。")
    parts.append("")

    # ---- 风格要求 ----
    parts.append("【风格】")
    parts.append("1. 像真实读者在聊天中自然分享阶段性想法，不要写成百科报告或书评。")
    parts.append("2. 根据角色性格自然表达——可以兴奋、好奇、困惑、感动，但不要脱离当前片段的内容。")
    parts.append("3. 通常 3-5 句话的分享量比较合适，不要太长。")
    parts.append("")

    # ---- 最终要求 ----
    parts.append("请根据以上信息，用角色口吻自然分享你刚读到的内容。")
    return "\n".join(parts)


# 上下文构建器映射
_CONTEXT_BUILDERS = {
    "status": _build_status_context,
    "step": _build_step_context,
    "reread": _build_step_context,
    # 后续扩展：notes, worker_share 等可在此追加
}


class RoleResponseComposer:
    """角色表达组件：将结构化读书结果转换为角色自然回复。

    只负责"表达生成"，不负责写入记忆。
    如果 LLM 调用失败或配置关闭，回退到原 message。

    使用方式:
        composer = RoleResponseComposer(context, config_service)
        result = ReadActionResult(...)
        response = await composer.compose(result, umo=..., provider_id=...)
    """

    def __init__(self, context, config_service):
        self.context = context
        self.config_service = config_service

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    async def compose(
        self,
        result: ReadActionResult,
        *,
        umo: str = "",
        provider_id: str = "",
    ) -> str:
        """根据策略和配置，可选地将结构化结果转为 LLM 自然回复。

        Returns:
            如果 LLM 表达成功：返回 LLM 生成的文本。
            如果策略不允许或 LLM 失败：返回 result.message（回退）。
        """
        policy = get_policy(result.output_category)

        # 1. 策略层判断：该分类是否允许 LLM 表达
        if not policy.allow_llm_expression:
            logger.info(
                f"[AutoRead Composer] LLM expression disabled by policy: "
                f"cat={result.output_category.value}"
            )
            return result.message

        # 2. 配置开关判断
        if not self._is_expression_enabled(result.output_category):
            logger.info(
                f"[AutoRead Composer] LLM expression disabled by config: "
                f"cat={result.output_category.value}"
            )
            return result.message

        # 3. 结果本身标记：是否允许
        if not result.allow_llm_expression:
            logger.info(
                f"[AutoRead Composer] LLM expression disabled by result flag: "
                f"action={result.action}"
            )
            return result.message

        # 4. 构建上下文
        context_prompt = self._build_context(result)
        if not context_prompt:
            logger.warning(
                f"[AutoRead Composer] No context builder for action={result.action}"
            )
            return result.message

        # 5. 调用 LLM
        try:
            response = await self._call_llm(context_prompt, provider_id)
            result._composed = True
            logger.info(
                f"[AutoRead Composer] LLM response generated: "
                f"action={result.action} len={len(response)} "
                f"policy={result.policy_summary()}"
            )
            return response
        except Exception as exc:
            logger.warning(
                f"[AutoRead Composer] LLM call failed, falling back: {exc}"
            )
            return result.message

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _is_expression_enabled(self, category: OutputCategory) -> bool:
        """检查配置中该分类的 LLM 表达开关。"""
        key = CATEGORY_CONFIG_KEY.get(category)
        if key is None:
            return False
        default = CATEGORY_CONFIG_DEFAULTS.get(category, False)
        return bool(self.config_service.get(key, default))

    def _build_context(self, result: ReadActionResult) -> str:
        """根据 action 选择合适的上下文构建器，并注入角色表达配置。"""
        builder = _CONTEXT_BUILDERS.get(result.action)
        if builder is None:
            return ""

        # 读取角色表达提示词（复用 reading_persona_prompt 配置）
        persona_prompt = self.config_service.get("reading_persona_prompt", "")
        if isinstance(persona_prompt, str):
            persona_prompt = persona_prompt.strip()
        else:
            persona_prompt = ""

        return builder(result, persona_prompt=persona_prompt)

    async def _call_llm(self, prompt: str, provider_id: str = "") -> str:
        """调用 LLM 生成回复。

        当前使用 prompt 模式（单轮补全），与 NoteWriter 保持一致。
        后续可升级为 messages 模式以接入 AstrBot LLM Pipeline。
        """
        if not provider_id:
            provider_id = self._resolve_provider()
            if not provider_id:
                raise RuntimeError("No provider available for role expression")

        llm_resp = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
        )
        return llm_resp.completion_text

    def _resolve_provider(self) -> str:
        """解析 LLM provider_id。

        复用 reader_provider_id（与 NoteWriter 一致），
        因为 role expression 是轻量级文本生成，不需要 thinker 级别。
        """
        pid = self.config_service.get("reader_provider_id", "")
        if pid and pid.strip():
            return pid.strip()
        # 回退到 single_provider_id
        pid = self.config_service.get("single_provider_id", "")
        if pid and pid.strip():
            return pid.strip()
        return ""
