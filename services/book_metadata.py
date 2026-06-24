"""书籍元数据生成与搜索服务（P1-3 规则版）。

本模块不依赖 AstrBot 运行时，只做纯数据处理。
alias_model 预留扩展点，当前仅规则生成。
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# 搜索结果
# ---------------------------------------------------------------------------

@dataclass
class BookSearchResult:
    """结构化搜索结果，不直接回复用户。"""

    book_id: str = ""
    title: str = ""
    display_name: str = ""
    author: str = ""
    original_filename: str = ""
    score: float = 0.0
    match_type: str = ""          # exact_title | exact_alias | substring_alias | author | ...
    matched_field: str = ""       # title | aliases | author | ...
    matched_value: str = ""       # 匹配到的具体值
    total_chunks: int = 0
    source_type: str = ""


# ---------------------------------------------------------------------------
# 键归一化
# ---------------------------------------------------------------------------

def normalize_search_key(text: str) -> str:
    """归一化文本用于搜索匹配。

    去空格、去标点、统一小写。
    """
    if not text:
        return ""
    s = text.strip().lower()
    s = re.sub(r"[_\-\s—–·•,，。.《》「」『』\"'():：；;!！?？]+", "", s)
    return s


# ---------------------------------------------------------------------------
# 文件名解析
# ---------------------------------------------------------------------------

# 常见分隔符（书名与作者之间）
_TITLE_AUTHOR_SEPS = re.compile(r"[_\-—–]+")


def _parse_title_author(stem: str) -> tuple[str, str]:
    """从 file stem 尝试拆分 title 和 author。

    规则：
    - "_"、"-"、"——"、"–" 前后视为两个片段
    - 较长的片段倾向为书名
    - 较短的片段倾向为作者
    - 如果只有一个片段，author 为空
    """
    parts = _TITLE_AUTHOR_SEPS.split(stem)
    # 过滤空段
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return stem, ""

    if len(parts) == 1:
        return parts[0], ""

    # 2 片段：典型格式「书名_作者」
    if len(parts) == 2:
        return parts[0], parts[1]

    # 3+ 片段：第一段为书名，其余合并为作者
    title = parts[0]
    author = " ".join(parts[1:])
    return title, author


def _build_aliases(title: str, author: str, stem: str) -> list[str]:
    """从已拆分的字段构建别名列表。"""
    aliases: list[str] = []
    seen: set[str] = set()

    def _add(s: str) -> None:
        s = s.strip()
        if s and s not in seen:
            seen.add(s)
            aliases.append(s)

    _add(title)
    _add(stem)
    if author:
        _add(f"{title} {author}")
        _add(f"{author} {title}")
        _add(author)

    # 也加回 stem 的变体
    _add(stem.replace("_", " "))
    _add(stem.replace("-", " "))

    return aliases


def _build_normalized_keys(
    title: str, author: str, aliases: list[str], stem: str
) -> list[str]:
    """从已有字段构建归一化搜索键。"""
    sources = [title, author, stem]
    sources.extend(aliases)
    keys: list[str] = []
    seen: set[str] = set()
    for src in sources:
        k = normalize_search_key(src)
        if k and k not in seen:
            seen.add(k)
            keys.append(k)
    return keys


# ---------------------------------------------------------------------------
# 元数据构建
# ---------------------------------------------------------------------------

def build_book_metadata(
    filename: str,
    *,
    original_title: str = "",
) -> dict:
    """从文件名构建完整书籍元数据。

    Args:
        filename: 原始文件名（如 小王子_圣埃克苏佩里.txt）
        original_title: 旧 book 中已有的 title 字段（可能等于 stem）

    Returns:
        包含 title/author/display_name/aliases/normalized_keys 等字段的 dict。
    """
    path = Path(filename)
    original_filename = path.name
    stem = path.stem

    # 尝试拆分
    title, author = _parse_title_author(stem)

    # 如果已有 old title 且不等于 stem，以 old title 为准
    if original_title and original_title != stem:
        title = original_title

    display_name = title if title else stem
    aliases = _build_aliases(title, author, stem)
    normalized_keys = _build_normalized_keys(title, author, aliases, stem)

    return {
        "original_filename": original_filename,
        "file_stem": stem,
        "title": title,
        "author": author,
        "display_name": display_name,
        "aliases": aliases,
        "normalized_keys": normalized_keys,
    }


def normalize_book_meta(book: dict) -> dict:
    """对已有 book dict 进行元数据补全。

    只补缺失字段，不覆盖已有手动编辑字段。
    但对旧版本遗留的 filename-derived title，安全修正为规范 title。
    可重复调用。
    """
    # 如果没有 original_filename，尝试从 source_path 推断
    if not book.get("original_filename"):
        src = book.get("source_path", "")
        if src:
            book["original_filename"] = Path(src).name

    if not book.get("file_stem"):
        fname = book.get("original_filename", "")
        if fname:
            book["file_stem"] = Path(fname).stem

    stem = book.get("file_stem", "") or book.get("title", "")
    old_title = book.get("title", "")

    # 生成元数据
    meta = build_book_metadata(
        filename=book.get("original_filename", stem + ".txt"),
        original_title=old_title,
    )

    # legacy title 检测：如果旧 title 等于 file_stem 且包含分隔符，
    # 说明是从文件名自动派生的，可以安全修正为规范 title
    _needs_title_fix = (
        old_title
        and stem
        and old_title == stem
        and _TITLE_AUTHOR_SEPS.search(stem)
    )
    if _needs_title_fix:
        book["title"] = meta["title"]
        # 将旧 title（原始 stem）加入 aliases
        aliases: list = book.setdefault("aliases", [])
        if old_title not in aliases:
            aliases.append(old_title)

    # 补缺失字段（不覆盖已有手动编辑字段）
    for key in (
        "original_filename", "file_stem", "author",
        "display_name", "aliases", "normalized_keys",
    ):
        if not book.get(key) and meta.get(key):
            book[key] = meta[key]
    # title 如未被 legacy 修正且为空，用 meta 补
    if not book.get("title"):
        book["title"] = meta.get("title", "")

    # 确保 aliases 至少包含 title 和 file_stem
    aliases: list = book.setdefault("aliases", [])
    for item in (book.get("title", ""), book.get("file_stem", "")):
        if item and item not in aliases:
            aliases.append(item)

    # 确保 normalized_keys
    if not book.get("normalized_keys"):
        nk = _build_normalized_keys(
            book.get("title", ""),
            book.get("author", ""),
            book.get("aliases", []),
            book.get("file_stem", ""),
        )
        book["normalized_keys"] = nk

    return book


def display_title(book: dict) -> str:
    """获取优先展示书名。"""
    return book.get("display_name") or book.get("title") or book.get("file_stem", "?")


def display_author(book: dict) -> str:
    """获取展示作者，为空时返回空字符串。"""
    return book.get("author", "")


def format_book_list_item(book: dict) -> str:
    """格式化单本书的列表项。

    优先使用 display_name 和 author。
    格式: [book_id] 《display_name》——author — N 段 (type)
    无 author 时: [book_id] 《display_name》 — N 段 (type)
    """
    dname = display_title(book)
    author = display_author(book)
    chunks = book.get("total_chunks", "?")
    stype = book.get("source_type", "unknown")

    title_part = f"《{dname}》"
    if author:
        title_part += f"——{author}"

    return (
        f"  [{book['book_id']}] {title_part} "
        f"— {chunks} 段 "
        f"({stype})"
    )


# ---------------------------------------------------------------------------
# 搜索
# ---------------------------------------------------------------------------

def search_books(query: str, books: list[dict]) -> list[BookSearchResult]:
    """在书籍列表中搜索。

    Args:
        query: 用户搜索词
        books: 书籍 dict 列表（每个 dict 至少含 book_id）

    Returns:
        按 score 降序排列的搜索结果列表。
    """
    if not query or not query.strip():
        return []

    q = query.strip()
    q_norm = normalize_search_key(q)
    results: list[BookSearchResult] = []

    for b in books:
        # 确保元数据完整
        normalize_book_meta(b)

        matches: list[BookSearchResult] = []

        # 1. 精确 title 匹配
        title = (b.get("title") or "").strip()
        if title.lower() == q.lower():
            matches.append(_make_result(b, score=1.0, mtype="exact_title",
                                         field="title", value=title))

        # 2. 精确 display_name 匹配
        dname = (b.get("display_name") or "").strip()
        if dname and dname.lower() == q.lower():
            matches.append(_make_result(b, score=0.95, mtype="exact_display_name",
                                         field="display_name", value=dname))

        # 3. aliases 精确匹配
        for alias in b.get("aliases", []):
            if alias.strip().lower() == q.lower():
                matches.append(_make_result(b, score=0.9, mtype="exact_alias",
                                             field="aliases", value=alias))
                break

        # 4. aliases 子串匹配
        for alias in b.get("aliases", []):
            if q.lower() in alias.strip().lower():
                matches.append(_make_result(b, score=0.7, mtype="substring_alias",
                                             field="aliases", value=alias))
                break

        # 5. file_stem / original_filename 匹配
        stem = (b.get("file_stem") or "").strip().lower()
        fname = (b.get("original_filename") or "").strip().lower()
        if q.lower() in stem or q.lower() in fname:
            matches.append(_make_result(b, score=0.5, mtype="filename",
                                         field="file_stem", value=stem or fname))

        # 6. author 匹配
        author = (b.get("author") or "").strip()
        if author and q.lower() in author.lower():
            matches.append(_make_result(b, score=0.6, mtype="author",
                                         field="author", value=author))

        # 7. normalized_keys 匹配
        for nk in b.get("normalized_keys", []):
            if q_norm in nk:
                matches.append(_make_result(b, score=0.4, mtype="normalized_key",
                                             field="normalized_keys", value=nk))
                break

        # 8. 简单模糊匹配（子串在 title/display_name/stem 的任意位置）
        if not matches and q_norm:
            haystack = normalize_search_key(
                title + dname + stem + fname + author
            )
            if q_norm in haystack:
                matches.append(_make_result(b, score=0.2, mtype="fuzzy",
                                             field="combined", value=q))

        if matches:
            results.append(max(matches, key=lambda m: m.score))

    results.sort(key=lambda r: r.score, reverse=True)
    return results


def _make_result(
    book: dict, *, score: float, mtype: str, field: str, value: str
) -> BookSearchResult:
    return BookSearchResult(
        book_id=book.get("book_id", ""),
        title=book.get("title", ""),
        display_name=display_title(book),
        author=display_author(book),
        original_filename=book.get("original_filename", ""),
        score=score,
        match_type=mtype,
        matched_field=field,
        matched_value=value,
        total_chunks=book.get("total_chunks", 0),
        source_type=book.get("source_type", "unknown"),
    )
