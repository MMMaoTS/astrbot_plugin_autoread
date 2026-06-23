"""统一阅读记录 Schema。

所有 reading record (chunk_note, chunk_review, chapter_note, book_note, final_review, memory_note)
使用统一字段。不同 record_type 允许部分字段为空。

同时提供旧格式兼容读取 normalize_record()。
"""

import uuid
from datetime import datetime, timezone, timedelta

# record_type 允许值
RECORD_TYPES = frozenset({
    "chunk_note",
    "chunk_review",
    "chapter_note",
    "book_note",
    "final_review",
    "memory_note",
})

# 阅读阶段
STAGES = frozenset({
    "chunk_note",
    "chunk_review",
    "chapter_note",
    "book_note",
    "user_visible_share",
    "final_review",
    "memory_note",
})

# 统一字段 key 常量（所有 record 共用）
FIELD_SUMMARY = "summary"
FIELD_DETAIL = "detail"
FIELD_REFLECTION = "reflection"
FIELD_SHARE_MESSAGE = "share_message"
FIELD_MEMORY_NOTE = "memory_note"
FIELD_OPEN_QUESTIONS = "open_questions"
FIELD_TAGS = "tags"
FIELD_IMPORTANCE_SCORE = "importance_score"
FIELD_NEEDS_DEEPER_REVIEW = "needs_deeper_review"
FIELD_DEEPER_REVIEW_DONE = "deeper_review_done"
FIELD_PARENT_RECORD_IDS = "parent_record_ids"
FIELD_CHILD_RECORD_IDS = "child_record_ids"
FIELD_MODEL_USAGE = "model_usage"


def _new_id(prefix: str = "record") -> str:
    ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:6]
    return f"{prefix}_{ts}_{short}"


def _now_iso() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat()


def new_record(
    *,
    book_id: str,
    book_title: str = "",
    record_type: str = "chunk_note",
    source_stage: str = "",
    chapter_title: str = "",
    chunk_index: int = 0,
    chunk_total: int = 0,
    chunk_range: tuple | None = None,
    summary: str = "",
    detail: str = "",
    reflection: str = "",
    share_message: str = "",
    memory_note: str = "",
    open_questions: list | None = None,
    tags: list | None = None,
    importance_score: float = 0.0,
    needs_deeper_review: bool = False,
    deeper_review_done: bool = False,
    parent_record_ids: list | None = None,
    child_record_ids: list | None = None,
    model_usage: dict | None = None,
    extra: dict | None = None,
) -> dict:
    """构建统一 ReadingRecord dict。"""
    if chunk_range is None:
        chunk_range = [0, 0]
    now = _now_iso()
    return {
        "schema_version": 1,
        "record_id": _new_id("record"),
        "record_type": record_type,
        "book_id": book_id,
        "book_title": book_title,
        "chapter_id": "",
        "chapter_title": chapter_title,
        "chunk_index": chunk_index,
        "chunk_total": chunk_total,
        "chunk_range": list(chunk_range),
        "source_stage": source_stage or record_type,
        "summary": summary,
        "detail": detail,
        "reflection": reflection,
        "share_message": share_message,
        "memory_note": memory_note,
        "open_questions": list(open_questions or []),
        "tags": list(tags or []),
        "importance_score": float(importance_score),
        "needs_deeper_review": bool(needs_deeper_review),
        "deeper_review_done": bool(deeper_review_done),
        "parent_record_ids": list(parent_record_ids or []),
        "child_record_ids": list(child_record_ids or []),
        "model_usage": dict(model_usage or {}),
        "created_at": now,
        "updated_at": now,
        "extra": dict(extra or {}),
    }


def model_usage_info(
    *,
    strategy: str = "dual",
    provider_id: str = "",
    stage: str = "chunk_note",
    stage_routing_enabled: bool = False,
) -> dict:
    return {
        "strategy": strategy,
        "provider_id": provider_id,
        "stage": stage,
        "stage_routing_enabled": stage_routing_enabled,
    }


def normalize_record(raw: dict) -> dict:
    """将旧格式 note 或新格式 record 统一为 ReadingRecord 结构。

    旧格式映射:
      note_id -> record_id
      chapter -> chapter_title
      should_share -> extra.should_share

    如果已经是新格式（含 schema_version / record_id），直接返回。
    对旧格式缺失字段填充默认值。
    """
    # 已经是新格式
    if "schema_version" in raw and "record_id" in raw:
        # 确保所有字段都存在
        result = dict(raw)
        result.setdefault("schema_version", 1)
        result.setdefault("record_type", raw.get("record_type", "chunk_note"))
        result.setdefault("book_title", raw.get("book_title", ""))
        result.setdefault("chapter_id", raw.get("chapter_id", ""))
        result.setdefault("chapter_title", raw.get("chapter_title", raw.get("chapter", "")))
        result.setdefault("chunk_index", raw.get("chunk_index", 0))
        result.setdefault("chunk_total", raw.get("chunk_total", raw.get("total_chunks", 0)))
        result.setdefault("chunk_range", raw.get("chunk_range", [0, 0]))
        result.setdefault("source_stage", raw.get("source_stage", raw.get("record_type", "chunk_note")))
        result.setdefault("summary", raw.get("summary", ""))
        result.setdefault("detail", raw.get("detail", ""))
        result.setdefault("reflection", raw.get("reflection", ""))
        result.setdefault("share_message", raw.get("share_message", ""))
        result.setdefault("memory_note", raw.get("memory_note", ""))
        result.setdefault("open_questions", raw.get("open_questions", []))
        result.setdefault("tags", raw.get("tags", []))
        result.setdefault("importance_score", raw.get("importance_score", 0.0))
        result.setdefault("needs_deeper_review", raw.get("needs_deeper_review", False))
        result.setdefault("deeper_review_done", raw.get("deeper_review_done", False))
        result.setdefault("parent_record_ids", raw.get("parent_record_ids", []))
        result.setdefault("child_record_ids", raw.get("child_record_ids", []))
        result.setdefault("model_usage", raw.get("model_usage", {}))
        result.setdefault("created_at", raw.get("created_at", ""))
        result.setdefault("updated_at", raw.get("updated_at", raw.get("created_at", "")))
        result.setdefault("extra", raw.get("extra", {}))
        return result

    # 旧格式 -> 新格式映射
    record_id = raw.get("record_id", raw.get("note_id", _new_id("record")))
    chapter = raw.get("chapter_title", raw.get("chapter", ""))

    # 推断 record_type
    record_type = raw.get("record_type", "chunk_note")
    if record_type not in RECORD_TYPES:
        record_type = "chunk_note"

    now = _now_iso()
    created = raw.get("created_at", now)

    should_share = raw.get("should_share", None)

    return {
        "schema_version": 1,
        "record_id": record_id,
        "record_type": record_type,
        "book_id": raw.get("book_id", ""),
        "book_title": raw.get("book_title", ""),
        "chapter_id": raw.get("chapter_id", ""),
        "chapter_title": chapter,
        "chunk_index": raw.get("chunk_index", 0),
        "chunk_total": raw.get("chunk_total", raw.get("total_chunks", 0)),
        "chunk_range": raw.get("chunk_range", [0, 0]),
        "source_stage": raw.get("source_stage", record_type),
        "summary": raw.get("summary", ""),
        "detail": raw.get("detail", ""),
        "reflection": raw.get("reflection", ""),
        "share_message": raw.get("share_message", ""),
        "memory_note": raw.get("memory_note", ""),
        "open_questions": raw.get("open_questions", []),
        "tags": raw.get("tags", []),
        "importance_score": float(raw.get("importance_score", 0.0)),
        "needs_deeper_review": bool(raw.get("needs_deeper_review", False)),
        "deeper_review_done": bool(raw.get("deeper_review_done", False)),
        "parent_record_ids": raw.get("parent_record_ids", []),
        "child_record_ids": raw.get("child_record_ids", []),
        "model_usage": raw.get("model_usage", {}),
        "created_at": created,
        "updated_at": raw.get("updated_at", created),
        "extra": raw.get("extra", {"should_share": should_share} if should_share is not None else {}),
    }
