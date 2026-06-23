# USAGE

AutoRead 提供两种使用入口：`/read` 命令（手动）和自然对话工具调用（自动）。

## 准备书籍

将 txt 或 md 文件放入插件运行数据目录：

```text
AstrBot/data/plugin_data/astrbot_plugin_autoread/books/
```

## 命令入口

### /read ping

检查插件是否正常加载。

```
/read ping
```

### /read bind

绑定当前会话。用于后续后台主动分享。

```
/read bind
```

### /read import

导入一本本地书籍。

```
/read import mybook.txt
```

文件须位于 `plugin_data/astrbot_plugin_autoread/books/` 下，仅支持 `.txt` 和 `.md` 扩展名。

### /read list

列出所有已导入的书籍。

```
/read list
```

### /read choose

根据偏好推荐一本书（不自动开始阅读）。

```
/read choose
/read choose 科幻
```

### /read start

开始持续阅读一本书。

```
/read start <book_id>
```

`book_id` 来自 `/read list` 的输出。

### /read step

手动推进阅读：读取下一段文本，调用 LLM 生成阅读笔记，保存并推进进度。

```
/read step
```

### /read status

查看当前阅读状态，包括书名、进度、暂停状态、时间信息。

```
/read status
```

### /read notes

查看最近的阅读笔记。

```
/read notes
/read notes 10
```

### /read pause / resume / stop

```
/read pause    暂停后台阅读
/read resume   恢复后台阅读
/read stop     停止当前阅读（历史笔记保留）
```

## 自然对话入口

自然对话工具调用需要模型支持 Function Calling。支持的模型包括 GPT-5.x、Gemini 3.x、Claude 4.x、Deepseek v3.2/deepseek-chat、Qwen 3.x 等。

示例对话：

```
用户：你自己挑一本感兴趣的书读吧。
AI：调用 autoread_list_books → autoread_choose_book → autoread_start_book

用户：你现在继续读一点。
AI：调用 autoread_read_next → 分享阅读心得

用户：你最近读到哪里了？
AI：调用 autoread_get_status → 返回进度信息
```

## 主动分享

插件在后台定时推进阅读后，可在支持主动消息的平台（aiocqhttp、satori）上主动分享阅读进展。

分享策略由配置项 `auto_share_mode` 控制：

- `none`：不主动分享
- `every_step`：每读完一段都分享
- `chapter`：仅在 note.should_share 为 true 时分享
- `daily` / `finish`：后续版本细化

主动分享失败时，笔记不丢失，错误记录在 `last_error` 中，可通过 `/read notes` 查看笔记。
