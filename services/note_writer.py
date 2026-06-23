"""阅读笔记生成 —— 调用当前会话 LLM，解析 JSON 响应。"""

import json
import re
from datetime import datetime, timezone, timedelta

from astrbot.api import logger


def build_reading_note_prompt(
    *,
    book_title: str,
    chunk_index: int,
    total_chunks: int,
    chapter: str,
    chunk_text: str,
    persona_prompt: str,
) -> str:
    return f"""{persona_prompt}

你正在持续阅读《{book_title}》。
这是你本次实际读到的片段，不是检索资料，也不是全书总结。

【当前进度】
第 {chunk_index + 1} / {total_chunks} 段

【章节】
{chapter or "未知章节"}

【本次阅读文本】
{chunk_text}

请严格基于上面的文本，输出 JSON，不要输出 Markdown 代码块。

JSON 格式：
{{
  "summary": "这一段主要讲了什么，限制在 80 字以内",
  "detail": "你注意到的一个细节，限制在 80 字以内",
  "reflection": "你自己的感受、疑问或想法，限制在 120 字以内",
  "should_share": true,
  "share_message": "如果主动分享给用户，你会怎么自然地说，限制在 200 字以内",
  "memory_note": "适合长期保存的一句话读书笔记，限制在 80 字以内"
}}

硬性限制：
1. 不要假装读过后文。
2. 不要评价整本书。
3. 不要引用当前片段之外的内容。
4. 如果片段信息不足，可以说"这段还不足以判断"。
5. should_share 只有在这段确实有值得分享的观察时才为 true。"""


class NoteWriter:
    """负责调用 LLM 为当前 chunk 生成结构化阅读笔记。"""

    def __init__(self, context, config):
        self.context = context
        self.config = config

    async def write_note(
        self,
        umo,
        book_id: str,
        book_title: str,
        chunk: dict,
        chunk_index: int,
        total_chunks: int,
    ) -> dict:
        """调用 LLM 生成阅读笔记，返回结构化 dict。JSON 解析失败时有 fallback。"""
        persona = self.config.get(
            "reading_persona_prompt",
            "你正在进行持续阅读，而不是临时检索问答。请像真实读者一样记录理解、疑问、情绪和观点变化。",
        )

        prompt = build_reading_note_prompt(
            book_title=book_title,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            chapter=chunk.get("chapter", "未知章节"),
            chunk_text=chunk.get("text", ""),
            persona_prompt=persona,
        )

        try:
            provider_id = await self.context.get_current_chat_provider_id(umo=umo)
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
            )
            raw_text = llm_resp.completion_text
            logger.info(f"[AutoRead] LLM note response length: {len(raw_text)}")

            note = self._parse_json(raw_text)
            note["note_id"] = self._new_note_id()
            note["book_id"] = book_id
            note["chunk_index"] = chunk_index
            note["chapter"] = chunk.get("chapter", "未知章节")
            note["created_at"] = self._now_iso()

            return note

        except Exception as exc:
            logger.warning(f"[AutoRead] LLM call failed, using fallback note: {exc}")
            return self._fallback_note(book_id, chunk, chunk_index)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json(raw_text: str) -> dict:
        """尝试从 LLM 返回文本中提取 JSON。"""
        # 去掉可能的 markdown 代码块
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # 尝试匹配第一个 JSON 对象
        match = re.search(r"\{[^{}]*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        # 完全失败，raise 让外层 fallback
        raise ValueError(f"无法从 LLM 响应中解析 JSON: {raw_text[:200]}")

    @staticmethod
    def _fallback_note(book_id: str, chunk: dict, chunk_index: int) -> dict:
        text_snippet = chunk.get("text", "")[:200]
        return {
            "note_id": NoteWriter._new_note_id(),
            "book_id": book_id,
            "chunk_index": chunk_index,
            "chapter": chunk.get("chapter", "未知章节"),
            "summary": text_snippet,
            "detail": "",
            "reflection": "模型没有返回合法 JSON，本次仅保存原始阅读反馈。",
            "should_share": False,
            "share_message": "",
            "memory_note": text_snippet[:120],
            "created_at": NoteWriter._now_iso(),
        }

    @staticmethod
    def _new_note_id() -> str:
        import uuid
        ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
        short = uuid.uuid4().hex[:6]
        return f"note_{ts}_{short}"

    @staticmethod
    def _now_iso() -> str:
        tz = timezone(timedelta(hours=8))
        return datetime.now(tz).isoformat()
