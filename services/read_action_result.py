"""B-first 最小分层骨架：结构化读书结果、输出分类与策略。

本模块不依赖 AstrBot 运行时上下文，只定义数据结构和策略映射。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# 输出分类
# ---------------------------------------------------------------------------

class OutputCategory(str, Enum):
    """读书插件输出的六种分类。

    不与插件命令一一对应：同一个 /read 命令在不同上下文中可能属于不同分类。
    """

    MANAGEMENT = "management"
    """管理类：导入、删除、绑定、配置修改、WebUI 管理操作等。"""

    COMMAND_QUERY = "command_query"
    """命令查询类：/read status、/read list、/read progress、/read notes 查看等。"""

    COMMAND_READ_ACTION = "command_read_action"
    """命令型阅读动作：/read step、/read reread 等。"""

    NATURAL_READING_CHAT = "natural_reading_chat"
    """自然语言读书交互：通过 LLM Tool 或直接对话触发的读书行为。"""

    CONTENT_GENERATION = "content_generation"
    """内容生成类：读书笔记、章节摘要、阅读感想。"""

    WORKER_SHARE = "worker_share"
    """Worker 主动分享：定时阅读后自动分享。"""


# ---------------------------------------------------------------------------
# 记忆/输出策略
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MemoryPolicy:
    """单条输出的各层写入策略。

    不可变：策略由分类决定，不应在运行时修改。
    """

    allow_direct_output: bool = True
    """是否允许插件直出（跳过 LLM）。"""

    allow_llm_expression: bool = False
    """是否允许 LLM 临时表达（不等于写入记忆）。"""

    allow_platform_history: bool = False
    """是否允许写入平台消息流水。"""

    allow_short_history: bool = False
    """是否允许写入短期对话历史。"""

    allow_long_memory_candidate: bool = False
    """是否允许作为长期记忆候选。"""

    reason: str = ""
    """策略理由，用于日志和文档。"""


# ---------------------------------------------------------------------------
# 默认策略表
# ---------------------------------------------------------------------------

POLICY_MAP: dict[OutputCategory, MemoryPolicy] = {
    OutputCategory.MANAGEMENT: MemoryPolicy(
        allow_direct_output=True,
        allow_llm_expression=False,
        allow_platform_history=False,
        allow_short_history=False,
        allow_long_memory_candidate=False,
        reason="管理操作确认不需要角色表达，直出即可，不污染记忆。",
    ),
    OutputCategory.COMMAND_QUERY: MemoryPolicy(
        allow_direct_output=True,
        allow_llm_expression=True,
        allow_platform_history=False,
        allow_short_history=False,
        allow_long_memory_candidate=False,
        reason=(
            "状态查询的回复不是角色经历。LLM 仅作为临时角色化表达器，"
            "不写入短期历史或长期记忆。"
        ),
    ),
    OutputCategory.COMMAND_READ_ACTION: MemoryPolicy(
        allow_direct_output=False,
        allow_llm_expression=True,
        allow_platform_history=True,
        allow_short_history=True,
        allow_long_memory_candidate=False,
        reason=(
            "角色确实读了内容。允许 LLM 表达和短期历史，"
            "但默认不直接进入长期记忆（需筛选）。"
        ),
    ),
    OutputCategory.NATURAL_READING_CHAT: MemoryPolicy(
        allow_direct_output=False,
        allow_llm_expression=True,
        allow_platform_history=True,
        allow_short_history=True,
        allow_long_memory_candidate=True,
        reason=(
            "自然对话交互，标准路径。长期记忆候选仅当包含"
            "用户偏好、角色感想、关系变化、重要共读事件时。"
        ),
    ),
    OutputCategory.CONTENT_GENERATION: MemoryPolicy(
        allow_direct_output=False,
        allow_llm_expression=True,
        allow_platform_history=True,
        allow_short_history=True,
        allow_long_memory_candidate=True,
        reason=(
            "笔记/摘要/感想的结构化素材不直接入记忆，"
            "角色化表达后的精炼结论才可作为记忆候选。"
        ),
    ),
    OutputCategory.WORKER_SHARE: MemoryPolicy(
        allow_direct_output=False,
        allow_llm_expression=True,
        allow_platform_history=True,
        allow_short_history=True,
        allow_long_memory_candidate=False,
        reason=(
            "主动分享类似自然对话，但频率高需防记忆泛滥。"
            "默认不写长期记忆，仅当用户回复后通过自然对话路径评估。"
        ),
    ),
}


def get_policy(category: OutputCategory) -> MemoryPolicy:
    """获取分类对应的默认策略。"""
    return POLICY_MAP.get(category, POLICY_MAP[OutputCategory.MANAGEMENT])


# ---------------------------------------------------------------------------
# 结构化读书结果
# ---------------------------------------------------------------------------

@dataclass
class ReadActionResult:
    """统一读书行为的结构化结果。

    各 handler 将业务结果填入本结构，然后交给 RoleResponseComposer
    和策略层统一处理输出，而不是各自拼 prompt 或直出文本。
    """

    # ---- 行为标识 ----
    action: str = ""
    """当前行为类型：status, step, reread, notes, worker_share, list, progress 等。"""

    success: bool = True
    """操作是否成功。"""

    # ---- 业务数据（可选，按 action 类型选择性填充） ----
    message: str = ""
    """原始业务消息或短文本。LLM 表达失败时作为回退文本。"""

    book_title: Optional[str] = None
    """当前书名。"""

    progress: Optional[str] = None
    """阅读进度描述。"""

    segment_text: Optional[str] = None
    """当前阅读片段原文（仅 step/reread 时有值）。"""

    summary: Optional[str] = None
    """摘要或概括文本。"""

    note_material: Optional[dict] = None
    """笔记素材（ReadingRecord dict）。"""

    error: Optional[str] = None
    """错误信息。"""

    # ---- 策略控制 ----
    output_category: OutputCategory = OutputCategory.MANAGEMENT
    """输出分类，决定后续各层的写入策略。"""

    allow_llm_expression: bool = True
    """是否允许 LLM 临时表达。可由调用方覆写策略默认值。"""

    allow_short_history: bool = False
    """是否允许短期对话历史。"""

    allow_long_memory_candidate: bool = False
    """是否允许长期记忆候选。"""

    # ---- 调试信息 ----
    debug_info: Optional[str] = None
    """调试信息，不应暴露给普通用户。"""

    # ---- 内部标记 ----
    _composed: bool = field(default=False, repr=False)
    """标记是否已经过 RoleResponseComposer 处理。"""

    def apply_policy(self):
        """根据 output_category 填充策略默认值（仅在显式未设置时覆盖）。"""
        policy = get_policy(self.output_category)
        if self.allow_llm_expression is True and not policy.allow_llm_expression:
            self.allow_llm_expression = False
        if not self.allow_short_history:
            self.allow_short_history = policy.allow_short_history
        if not self.allow_long_memory_candidate:
            self.allow_long_memory_candidate = policy.allow_long_memory_candidate
        return self

    def policy_summary(self) -> str:
        """一行策略摘要，用于日志。"""
        parts = [
            f"cat={self.output_category.value}",
            f"llm={self.allow_llm_expression}",
            f"short_hist={self.allow_short_history}",
            f"long_mem={self.allow_long_memory_candidate}",
        ]
        return " ".join(parts)
