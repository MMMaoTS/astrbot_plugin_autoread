"""阅读笔记生成。

通过 ModelRouter 决定模型角色，ProviderResolver 解析 provider_id，调用 LLM。
输出统一 ReadingRecord。
"""

import json
import re
from datetime import datetime, timezone, timedelta

from astrbot.api import logger

from ..models.reading_record import (
    new_record,
    model_usage_info,
)


def build_chunk_note_prompt(
    *,
    book_title: str,
    chunk_index: int,
    chunk_total: int,
    chapter_title: str,
    chunk_text: str,
    persona_prompt: str,
) -> str:
    """构建 chunk 阅读笔记 prompt。输出统一字段。"""
    return f"""{persona_prompt}

你正在持续阅读《{book_title}》。
这是你本次实际读到的片段，不是检索资料，也不是全书总结。

【当前进度】
第 {chunk_index + 1} / {chunk_total} 段

【章节】
{chapter_title or "未知章节"}

【本次实际读到的文本】
{chunk_text}

请严格基于上面的文本，输出 JSON，不要输出 Markdown 代码块，不要添加 JSON 之外的解释。

JSON 格式如下:

{{
  "summary": "这一段主要讲了什么，限制在 80 字以内",
  "detail": "你注意到的一个细节、意象、人物、语气或结构，限制在 80 字以内",
  "reflection": "你作为当前人格产生的感受、疑问、联想或态度变化，限制在 140 字以内",
  "share_message": "如果这一段值得之后自然分享给用户，你会如何说，限制在 220 字以内; 如果不值得分享则为空字符串",
  "memory_note": "适合沉淀为长期阅读经历的一句话，限制在 90 字以内",
  "open_questions": ["还不能确定、但值得后续留意的问题"],
  "tags": ["最多 5 个简短分类标签"],
  "keywords": ["最多 5 个文本中出现的关键词或短语，用于后续检索"],
  "importance_score": 0.0,
  "needs_deeper_review": false
}}

判断规则:
1. summary 偏客观，只概括当前片段。
2. detail 选择你真正注意到的一个点，不要泛泛而谈。
3. reflection 要符合当前人格，不要写成普通助手的分析报告。
4. share_message 应像你自然对用户说话，而不是读书报告。
5. memory_note 应适合长期保存，简短、稳定、可回忆。
6. importance_score 范围是 0.0 到 1.0。
7. needs_deeper_review 只有在文本明显复杂、重要、矛盾、情绪强烈或需要更高质量理解时才为 true。

硬性限制:
1. 只能基于本次实际读到的文本片段。
2. 不要假装读过后文。
3. 不要评价整本书。
4. 不要编造剧情、人物关系、作者意图或结局。
5. 不要引用当前片段之外的知识。
6. 如果片段信息不足，可以明确说"这段还不足以判断"。
7. 保持人格表达，但不要牺牲准确性。
8. 你对这本书的理解可以随着后续阅读改变，不需要现在给出最终判断。"""


class NoteWriter:
    """负责调用 LLM 生成阅读笔记。

    模型选择: ModelRouter -> ProviderResolver
    输出格式: 统一 ReadingRecord
    """

    def __init__(self, context, config_service, provider_resolver):
        self.context = context
        self.config_service = config_service
        self.provider_resolver = provider_resolver

    async def write_note(
        self,
        umo,
        book_id: str,
        book_title: str,
        chunk: dict,
        chunk_index: int,
        chunk_total: int,
    ) -> dict:
        """生成 chunk_note ReadingRecord。

        流程: ProviderResolver.resolve_provider_id(stage="chunk_note") -> LLM

        Returns:
            ReadingRecord dict (record_type="chunk_note")
        """
        persona = self.config_service.get("reading_persona_prompt", "")

        prompt = build_chunk_note_prompt(
            book_title=book_title,
            chunk_index=chunk_index,
            chunk_total=chunk_total,
            chapter_title=chunk.get("chapter", "未知章节"),
            chunk_text=chunk.get("text", ""),
            persona_prompt=persona,
        )

        strategy = self.config_service.get("model_strategy", "dual")
        stage_routing = self.config_service.get("enable_stage_routing", False)
        stage = "chunk_note"

        try:
            provider_id = await self.provider_resolver.resolve_provider_id(
                umo=umo,
                stage=stage,
            )
            if not provider_id:
                raise RuntimeError(
                    "No available reading model provider. "
                    "Please configure reading model in plugin settings."
                )

            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
            )
            raw_text = llm_resp.completion_text
            logger.info(
                f"[AutoRead] LLM note: len={len(raw_text)} "
                f"provider={provider_id} stage={stage} strategy={strategy}"
            )

            parsed = self._parse_json(raw_text)
            mu = model_usage_info(
                strategy=strategy,
                provider_id=provider_id,
                stage=stage,
                stage_routing_enabled=stage_routing,
            )

            record = new_record(
                book_id=book_id,
                book_title=book_title,
                record_type="chunk_note",
                source_stage="chunk_note",
                chapter_title=chunk.get("chapter", "未知章节"),
                chunk_index=chunk_index,
                chunk_total=chunk_total,
                chunk_range=(chunk.get("char_start", 0), chunk.get("char_end", 0)),
                summary=parsed.get("summary", ""),
                detail=parsed.get("detail", ""),
                reflection=parsed.get("reflection", ""),
                share_message=parsed.get("share_message", ""),
                memory_note=parsed.get("memory_note", ""),
                open_questions=parsed.get("open_questions", []),
                tags=parsed.get("tags", []),
                keywords=parsed.get("keywords", []),
                importance_score=float(parsed.get("importance_score", 0.0)),
                needs_deeper_review=bool(parsed.get("needs_deeper_review", False)),
                model_usage=mu,
            )
            return record

        except Exception as exc:
            logger.warning(f"[AutoRead] LLM call failed, using fallback: {exc}")
            return self._fallback_record(
                book_id, book_title, chunk, chunk_index, chunk_total, strategy, stage_routing
            )

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json(raw_text: str) -> dict:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{[^{}]*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Cannot parse JSON from LLM response: {raw_text[:200]}")

    @staticmethod
    def _fallback_record(
        book_id: str,
        book_title: str,
        chunk: dict,
        chunk_index: int,
        chunk_total: int,
        strategy: str,
        stage_routing: bool = False,
    ) -> dict:
        text_snippet = chunk.get("text", "")[:200]
        return new_record(
            book_id=book_id,
            book_title=book_title,
            record_type="chunk_note",
            source_stage="chunk_note",
            chapter_title=chunk.get("chapter", "未知章节"),
            chunk_index=chunk_index,
            chunk_total=chunk_total,
            summary=text_snippet,
            detail="",
            reflection="Model did not return valid JSON. This is a raw fallback.",
            share_message="",
            memory_note=text_snippet[:90],
            importance_score=0.0,
            needs_deeper_review=False,
            model_usage=model_usage_info(
                strategy=strategy,
                provider_id="(fallback)",
                stage="chunk_note",
                stage_routing_enabled=stage_routing,
            ),
        )
