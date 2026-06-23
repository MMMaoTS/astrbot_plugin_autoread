# USAGE

AutoRead 提供三种使用入口：`/read` 命令（手动）、自然对话工具调用（自动）和 WebUI 管理页面。

## 准备书籍

将 txt 或 md 文件放入插件运行数据目录：

```text
AstrBot/data/plugin_data/astrbot_plugin_autoread/books/
```

也可通过 WebUI 上传书籍。

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

自然对话工具调用需要模型支持 Function Calling。

可用的工具：`autoread_list_books`、`autoread_choose_book`、`autoread_start_book`、`autoread_read_next`、`autoread_get_status`、`autoread_get_notes`、`autoread_pause`、`autoread_resume`、`autoread_stop`。

示例对话：

```
用户：你自己挑一本感兴趣的书读吧。
AI：调用 autoread_list_books → autoread_choose_book → autoread_start_book

用户：你现在继续读一点。
AI：调用 autoread_read_next → 分享阅读心得

用户：你最近读到哪里了？
AI：调用 autoread_get_status → 返回进度信息
```

### 自然语言不能做的事

- **不能删除笔记**。删除是管理操作，只能在 WebUI 中执行。
- **不能手工新增/编辑笔记**。笔记只能由阅读过程生成。
- 角色可以建议、提醒、引导用户到 WebUI 操作，但不能在聊天中直接执行。

当用户要求删除笔记时，角色应自然说明边界：

> "这类删除我不能直接在聊天里替你操作，还是要在管理页里确认一下会比较安全。不过如果你觉得这条笔记写得不好，我可以重新读这一段，再整理一版新的。"

## WebUI 管理页面

通过 AstrBot Dashboard → 插件管理 → AutoRead → 管理页面进入。

详细说明见 [WEBUI.md](WEBUI.md)。主要功能：

- 总览：书籍数、笔记数、活跃任务、最后错误
- 书籍管理：上传、查看、删除（需开启开关）
- 笔记管理：查看、搜索、删除（需开启开关）
- 阅读任务：查看、取消、清理历史
- 设置：所有配置项可视化修改
- 备份管理：导出、导入、服务器备份查看/下载/恢复/删除

## 主动分享

插件在后台定时推进阅读后，可在支持主动消息的平台（aiocqhttp、satori）上主动分享阅读进展。

分享策略由配置项 `auto_share_mode` 控制：

- `none`：不主动分享
- `every_step`：每读完一段都分享
- `chapter`：仅在 note.should_share 为 true 时分享
- `daily` / `finish`：后续版本细化

主动分享失败时，笔记不丢失，错误记录在 `last_error` 中。错误可通过 WebUI 查看和手动清除，超过 TTL 后自动隐藏。
