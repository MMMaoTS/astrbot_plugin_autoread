import asyncio
import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path


class ReadingStateStore:
    """管理 state.json 和笔记持久化。

    所有写入操作使用临时文件 + os.replace 实现原子写入。
    state.json 写入受 asyncio.Lock 保护，避免并发损坏。
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.state_path = data_dir / "state.json"
        self.lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _now_iso() -> str:
        tz = timezone(timedelta(hours=8))
        return datetime.now(tz).isoformat()

    @staticmethod
    def _new_id(prefix: str = "book") -> str:
        ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
        short = uuid.uuid4().hex[:6]
        return f"{prefix}_{ts}_{short}"

    def _default_state(self) -> dict:
        return {"version": 1, "sessions": {}, "books": {}}

    # ------------------------------------------------------------------
    # 加载 / 保存
    # ------------------------------------------------------------------

    async def load_state(self) -> dict:
        if not self.state_path.exists():
            return self._default_state()
        try:
            text = self.state_path.read_text(encoding="utf-8")
            return json.loads(text)
        except (json.JSONDecodeError, OSError):
            return self._default_state()

    async def save_state(self, state: dict) -> None:
        async with self.lock:
            tmp_path = self.state_path.with_suffix(".json.tmp")
            tmp_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp_path, self.state_path)

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    async def bind_session(self, umo: str) -> dict:
        state = await self.load_state()
        if umo not in state["sessions"]:
            state["sessions"][umo] = {
                "enabled": True,
                "paused": False,
                "bound_at": self._now_iso(),
                "current_book_id": None,
                "current_book_title": None,
                "current_chunk_index": 0,
                "total_chunks": 0,
                "last_read_at": None,
                "next_read_at": None,
                "reading_interval_minutes": 1440,
                "auto_share_mode": "chapter",
                "last_error": None,
            }
        await self.save_state(state)
        return state["sessions"][umo]

    async def get_session(self, umo: str) -> dict | None:
        state = await self.load_state()
        return state["sessions"].get(umo)

    async def update_session(self, umo: str, patch: dict) -> dict | None:
        state = await self.load_state()
        session = state["sessions"].get(umo)
        if session is None:
            return None
        session.update(patch)
        await self.save_state(state)
        return session

    # ------------------------------------------------------------------
    # Books
    # ------------------------------------------------------------------

    async def register_book(self, book_meta: dict) -> None:
        state = await self.load_state()
        state["books"][book_meta["book_id"]] = book_meta
        await self.save_state(state)

    async def get_book(self, book_id: str) -> dict | None:
        state = await self.load_state()
        return state["books"].get(book_id)

    async def list_books(self) -> list[dict]:
        state = await self.load_state()
        return list(state["books"].values())

    # ------------------------------------------------------------------
    # 阅读进度
    # ------------------------------------------------------------------

    async def start_book(
        self,
        umo: str,
        book_id: str,
        title: str,
        total_chunks: int,
        interval_minutes: int,
        auto_share_mode: str,
    ) -> dict:
        state = await self.load_state()
        # 确保 session 存在
        if umo not in state["sessions"]:
            state["sessions"][umo] = {
                "enabled": True,
                "paused": False,
                "bound_at": self._now_iso(),
                "current_book_id": None,
                "current_book_title": None,
                "current_chunk_index": 0,
                "total_chunks": 0,
                "last_read_at": None,
                "next_read_at": None,
                "reading_interval_minutes": interval_minutes,
                "auto_share_mode": auto_share_mode,
                "last_error": None,
            }
        session = state["sessions"][umo]
        session.update({
            "current_book_id": book_id,
            "current_book_title": title,
            "current_chunk_index": 0,
            "total_chunks": total_chunks,
            "last_read_at": None,
            "next_read_at": self._now_iso(),  # 首次立即可读
            "reading_interval_minutes": interval_minutes,
            "auto_share_mode": auto_share_mode,
            "paused": False,
            "enabled": True,
            "last_error": None,
        })
        await self.save_state(state)
        return session

    async def advance_progress(self, umo: str) -> dict | None:
        state = await self.load_state()
        session = state["sessions"].get(umo)
        if session is None:
            return None
        now = self._now_iso()
        session["current_chunk_index"] += 1
        session["last_read_at"] = now
        interval = session.get("reading_interval_minutes", 1440)
        # 计算下次阅读时间
        dt = datetime.now(timezone(timedelta(hours=8))) + timedelta(minutes=interval)
        session["next_read_at"] = dt.isoformat()
        await self.save_state(state)
        return session

    # ------------------------------------------------------------------
    # Notes
    # ------------------------------------------------------------------

    async def append_note(self, book_id: str, note: dict) -> None:
        notes_path = self.data_dir / "notes" / f"{book_id}.notes.jsonl"
        notes_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(note, ensure_ascii=False) + "\n"
        with open(notes_path, "a", encoding="utf-8") as f:
            f.write(line)

    async def get_recent_notes_for_session(
        self, umo: str, limit: int = 5
    ) -> list[dict]:
        state = await self.load_state()
        session = state["sessions"].get(umo)
        if session is None or not session.get("current_book_id"):
            return []
        book_id = session["current_book_id"]
        notes_path = self.data_dir / "notes" / f"{book_id}.notes.jsonl"
        if not notes_path.exists():
            return []
        # 读取最后 limit 条
        notes: list[dict] = []
        with open(notes_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    notes.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return notes[-limit:]

    # ------------------------------------------------------------------
    # Sessions (批量查询，供 WebUI 使用)
    # ------------------------------------------------------------------

    async def list_sessions(self) -> dict[str, dict]:
        """返回所有 session（key 为原始 umo）。调用方需自行脱敏。"""
        state = await self.load_state()
        return state.get("sessions", {})

    # ------------------------------------------------------------------
    # Notes (跨书查询，供 WebUI 使用)
    # ------------------------------------------------------------------

    async def count_notes_for_book(self, book_id: str) -> int:
        notes_path = self.data_dir / "notes" / f"{book_id}.notes.jsonl"
        if not notes_path.exists():
            return 0
        count = 0
        with open(notes_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count

    async def count_all_notes(self) -> int:
        notes_dir = self.data_dir / "notes"
        if not notes_dir.exists():
            return 0
        total = 0
        for p in notes_dir.glob("*.notes.jsonl"):
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        total += 1
        return total

    async def get_notes_by_book(
        self,
        book_id: str,
        page: int = 1,
        page_size: int = 20,
        keyword: str = "",
    ) -> tuple[list[dict], int]:
        """按 book_id 分页读取笔记。返回 (notes, total)。"""
        notes_path = self.data_dir / "notes" / f"{book_id}.notes.jsonl"
        if not notes_path.exists():
            return [], 0
        all_notes: list[dict] = []
        with open(notes_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    note = json.loads(line)
                    if keyword:
                        kw = keyword.lower()
                        text = json.dumps(note, ensure_ascii=False).lower()
                        if kw not in text:
                            continue
                    all_notes.append(note)
                except json.JSONDecodeError:
                    continue
        total = len(all_notes)
        # 倒序（最新在前）
        all_notes.reverse()
        start = (page - 1) * page_size
        end = start + page_size
        return all_notes[start:end], total

    async def get_all_notes(
        self,
        page: int = 1,
        page_size: int = 20,
        keyword: str = "",
        book_id: str = "",
    ) -> tuple[list[dict], int]:
        """跨所有书籍分页读取笔记。返回 (notes, total)。"""
        notes_dir = self.data_dir / "notes"
        if not notes_dir.exists():
            return [], 0
        all_notes: list[dict] = []
        state = await self.load_state()
        books = state.get("books", {})
        for p in sorted(notes_dir.glob("*.notes.jsonl")):
            bid = p.stem.replace(".notes", "")
            if book_id and bid != book_id:
                continue
            book_title = books.get(bid, {}).get("title", bid)
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        note = json.loads(line)
                        note["book_title"] = book_title
                        if keyword:
                            kw = keyword.lower()
                            text = json.dumps(note, ensure_ascii=False).lower()
                            if kw not in text:
                                continue
                        all_notes.append(note)
                    except json.JSONDecodeError:
                        continue
        # 倒序（最新在前）
        all_notes.sort(key=lambda n: n.get("created_at", ""), reverse=True)
        total = len(all_notes)
        start = (page - 1) * page_size
        end = start + page_size
        return all_notes[start:end], total

    async def get_note_by_id(self, book_id: str, note_id: str) -> dict | None:
        """按 record_id 或旧 note_id 查找单条笔记。"""
        notes_path = self.data_dir / "notes" / f"{book_id}.notes.jsonl"
        if not notes_path.exists():
            return None
        with open(notes_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    note = json.loads(line)
                    if note.get("record_id") == note_id or note.get("note_id") == note_id:
                        return note
                except json.JSONDecodeError:
                    continue
        return None

    async def count_books(self) -> int:
        state = await self.load_state()
        return len(state.get("books", {}))

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    async def stop_session(self, umo: str) -> None:
        state = await self.load_state()
        session = state["sessions"].get(umo)
        if session:
            session["enabled"] = False
            session["current_book_id"] = None
            session["current_book_title"] = None
            session["current_chunk_index"] = 0
            session["total_chunks"] = 0
            session["last_read_at"] = None
            session["next_read_at"] = None
            session["last_error"] = None
            await self.save_state(state)

    async def set_last_error(self, umo: str, error: str) -> None:
        state = await self.load_state()
        session = state["sessions"].get(umo)
        if session:
            session["last_error"] = error
            await self.save_state(state)
