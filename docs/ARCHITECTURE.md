# ARCHITECTURE

AutoRead 采用双入口、单业务核心架构。

## 层级

```text
main.py
  ├── /read commands
  ├── @filter.llm_tool tools
  └── lifecycle: worker start / terminate
        ↓
services/autoread_service.py
        ↓
services/reading_state.py
services/book_loader.py
services/text_chunker.py
services/note_writer.py
services/memory_bridge.py
worker/reading_worker.py
```

## 入口层

入口层 (`main.py`) 只负责事件适配，不包含业务逻辑：

- `/read` 命令组：调试、管理、兜底入口
- `@filter.llm_tool`：自然对话工具入口
- 生命周期管理：worker 启动与销毁

## 业务层

`AutoReadService` 是命令、工具、worker 的唯一业务入口，统一暴露：

- `bind` / `import_book` / `list_books` / `choose_book`
- `start_book` / `read_next_chunk`
- `get_status` / `get_notes`
- `pause` / `resume` / `stop`

## 状态层

`ReadingStateStore` 管理：

- `state.json`：会话状态、书籍元数据
- notes jsonl：阅读笔记追加
- 原子写入与并发锁

## LLM 层

`NoteWriter` 负责：

- 构建阅读笔记 prompt
- 调用当前会话 LLM
- 解析 JSON 响应
- 解析失败时的 fallback

## Worker 层

`ReadingWorker` 只负责定时扫描到期任务并调度 `AutoReadService.read_next_chunk()`，不直接实现阅读逻辑。

## 扩展点

- 书籍来源：扩展 `BookLoader` 或新增 `BookSource` 抽象
- 切片策略：扩展 `TextChunker`（固定大小 / 按章节 / 语义切片）
- 笔记后端：扩展 `MemoryBridge`（local_jsonl / 天使之魂 / LivingMemory）
- 调度器：扩展 `ReadingWorker`（简单轮询 / AstrBot FutureTask / 外部调度）
- 分享策略：`auto_share_mode`（none / every_step / daily / chapter / finish）
- 权限控制：预留 PermissionPolicy 接口
