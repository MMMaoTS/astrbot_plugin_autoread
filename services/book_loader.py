import re
from pathlib import Path
from dataclasses import dataclass

from astrbot.api import logger


@dataclass
class ImportedBook:
    meta: dict
    text: str


class BookLoader:
    """负责从 plugin_data/books/ 读取本地 txt/md 文本。"""

    def __init__(self, data_dir: Path, allowed_extensions: list[str] | None = None):
        self.data_dir = data_dir
        self.books_dir = data_dir / "books"
        self.allowed_extensions = allowed_extensions or [".txt", ".md"]

    async def import_local_book(self, filename: str) -> ImportedBook:
        """导入本地书籍。

        安全约束：
        - filename 不允许包含绝对路径
        - filename 不允许路径穿越（../）
        - 只读取 books_dir 下的文件
        - 检查扩展名
        - 尝试 utf-8 和 utf-8-sig 解码
        """
        # 安全检查
        if filename.startswith("/") or ".." in filename:
            raise ValueError("不允许路径穿越或绝对路径")

        file_path = self.books_dir / filename
        resolved = file_path.resolve()
        books_dir_resolved = self.books_dir.resolve()

        if not str(resolved).startswith(str(books_dir_resolved)):
            raise ValueError("文件不在允许的书籍目录内")

        if not resolved.exists():
            raise FileNotFoundError(f"文件不存在: {filename}")

        suffix = resolved.suffix.lower()
        if suffix not in self.allowed_extensions:
            raise ValueError(
                f"不支持的扩展名 {suffix}，允许: {', '.join(self.allowed_extensions)}"
            )

        # 读取文本
        raw_bytes = resolved.read_bytes()
        text = self._decode(raw_bytes)

        # 标准化换行
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        if not text.strip():
            raise ValueError("文件内容为空")

        # 构建 meta
        raw_title = resolved.stem
        book_id = self._generate_book_id()

        # P1-3: 生成规则版元数据
        from .book_metadata import build_book_metadata
        metadata = build_book_metadata(resolved.name, original_title=raw_title)

        meta = {
            "book_id": book_id,
            "title": metadata["title"],
            "source_type": "local",
            "source_path": f"books/{resolved.name}",
            "chunks_path": f"chunks/{book_id}.chunks.json",
            "notes_path": f"notes/{book_id}.notes.jsonl",
            "created_at": self._now_iso(),
            "total_chars": len(text),
            "total_chunks": 0,
            # P1-3 新增元数据字段
            "original_filename": metadata["original_filename"],
            "file_stem": metadata["file_stem"],
            "author": metadata["author"],
            "display_name": metadata["display_name"],
            "aliases": metadata["aliases"],
            "normalized_keys": metadata["normalized_keys"],
        }

        logger.info(f"[AutoRead] Imported book: {title} (id={book_id}, chars={len(text)})")
        return ImportedBook(meta=meta, text=text)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _decode(raw_bytes: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
            try:
                return raw_bytes.decode(encoding)
            except (UnicodeDecodeError, UnicodeError):
                continue
        raise ValueError("无法解码文件内容")

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone, timedelta
        tz = timezone(timedelta(hours=8))
        return datetime.now(tz).isoformat()

    @staticmethod
    def _generate_book_id() -> str:
        import uuid
        from datetime import datetime, timezone, timedelta
        ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
        short = uuid.uuid4().hex[:6]
        return f"book_{ts}_{short}"
