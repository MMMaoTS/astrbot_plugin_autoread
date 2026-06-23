# ARCHITECTURE

AutoRead 采用多入口、单业务核心架构。

## 层级

```text
main.py
  ├── /read commands              # 命令入口
  ├── @filter.llm_tool tools      # 自然语言工具入口
  ├── lifecycle: worker start / terminate
  └── WebUI API registration
        ↓
services/autoread_service.py
        ↓
repositories/reading_state_repository.py   # state.json 持久化
services/book_loader.py                    # 书籍导入
services/text_chunker.py                   # 文本切片
services/note_writer.py                    # LLM 笔记生成
services/memory_bridge.py                  # 外部记忆桥接
services/backup_service.py                 # 备份导入/导出/管理
core/page_api.py                           # WebUI API 路由
core/page_service.py                       # WebUI 业务编排
core/config_service.py                     # 配置管理
worker/reading_worker.py                   # 后台轮询
```

## 入口层

入口层 (`main.py`) 只负责事件适配，不包含业务逻辑：

- `/read` 命令组：调试、管理、兜底入口
- `@filter.llm_tool`：自然对话工具入口（9 个工具：list_books, choose_book, start_book, read_next, get_status, get_notes, pause, resume, stop）
- 生命周期管理：worker 启动与销毁
- WebUI API 注册：通过 `context.register_web_api` 注册 30 条路由

## 业务层

`AutoReadService` 是命令、工具、worker 的唯一业务入口，统一暴露：

- `bind` / `import_book` / `list_books` / `choose_book`
- `start_book` / `read_next_chunk`
- `get_status` / `get_notes`
- `pause` / `resume` / `stop`

## WebUI 层

`AutoReadWebUIAPI` (`core/page_api.py`) 注册所有 WebUI API 路由，`WebUIService` (`core/page_service.py`) 编排业务逻辑。支持：

- 概览、书籍管理、笔记管理、阅读任务管理
- 设置读写、provider 列表
- 备份导入/导出/管理（列表、下载、上传、恢复、删除）
- 错误管理（查看、清除、TTL 过期）
- 删除功能（受 `webui_delete_enabled` 开关控制）
- 状态查询（capabilities/config）

## 状态层

`ReadingStateStore` 管理：

- `state.json`：会话状态、书籍元数据
- notes jsonl：阅读笔记追加与原子删除
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
