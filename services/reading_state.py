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
