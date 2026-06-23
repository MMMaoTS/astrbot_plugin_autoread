import json
import re
from pathlib import Path

from astrbot.api import logger


# 章节识别正则
CHAPTER_PATTERNS = [
    re.compile(r"^第[一二三四五六七八九十百千万\d]+[章节回卷部].*$"),
    re.compile(r"^Chapter\s+\d+.*$", re.IGNORECASE),
    re.compile(r"^#{1,3}\s+.+$"),
]


class TextChunker:
    """负责将长文本切片为固定大小的 chunk，并检测章节标题。"""

    def __init__(self, chunk_size: int = 1800, chunk_overlap: int = 120):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split(self, text: str) -> list[dict]:
        """将文本切分为 chunks，每 chunk 包含 index、chapter、text、char_start、char_end。"""
        if not text:
            return []

        lines = text.split("\n")
        chunks: list[dict] = []
        current_chapter = "未知章节"
        buffer = ""
        char_start = 0
        idx = 0

        for line in lines:
            # 检测章节标题
            detected = self._detect_chapter(line)
            if detected:
                # 保存之前积累的 buffer
                if buffer.strip():
                    for chunk_data in self._emit_chunks(
                        buffer, char_start, current_chapter, idx
                    ):
                        chunks.append(chunk_data)
                        idx += 1
                    char_start += len(buffer)
                buffer = ""
                current_chapter = detected

            buffer += line + "\n"

            # 当 buffer 超过 chunk_size 时切分
            while len(buffer) >= self.chunk_size:
                cut_point = self.chunk_size
                # 尝试在句号、换行处切分
                for sep in ("。\n", "。", "\n", "，", " "):
                    pos = buffer.rfind(sep, 0, self.chunk_size)
                    if pos > self.chunk_size // 2:
                        cut_point = pos + len(sep)
                        break

                chunk_text = buffer[:cut_point].strip()
                if chunk_text:
                    chunks.append({
                        "index": idx,
                        "chapter": current_chapter,
                        "text": chunk_text,
                        "char_start": char_start,
                        "char_end": char_start + len(chunk_text),
                    })
                    idx += 1
                    char_start += cut_point - self.chunk_overlap

                # 保留 overlap 部分
                overlap_start = max(0, cut_point - self.chunk_overlap)
                buffer = buffer[overlap_start:]

        # 处理剩余 buffer
        if buffer.strip():
            chunks.append({
                "index": idx,
                "chapter": current_chapter,
                "text": buffer.strip(),
                "char_start": char_start,
                "char_end": char_start + len(buffer.strip()),
            })

        logger.info(f"[AutoRead] Chunked text into {len(chunks)} chunks")
        return chunks

    async def save_chunks(self, path: Path, chunks: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(chunks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_chapter(line: str) -> str | None:
        stripped = line.strip()
        if not stripped:
            return None
        for pat in CHAPTER_PATTERNS:
            if pat.match(stripped):
                return stripped
        return None

    @staticmethod
    def _emit_chunks(
        buffer: str, offset: int, chapter: str, start_idx: int
    ) -> list[dict]:
        """发送 buffer 中剩余的文本作为最后一个 chunk（不再切分）。"""
        text = buffer.strip()
        if not text:
            return []
        return [{
            "index": start_idx,
            "chapter": chapter,
            "text": text,
            "char_start": offset,
            "char_end": offset + len(text),
        }]
