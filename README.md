# astrbot_plugin_autoread

AutoRead 是一个面向虚拟角色的 AstrBot 持续阅读插件。它不是阅读问答工具，而是一个带进度、笔记和后台任务的阅读状态机。

## 功能

- 支持导入本地 txt/md 文本
- 支持文本切片
- 支持保存和设置阅读进度
- 支持手动 `/read step` 继续阅读、`/read reread` 重新阅读
- 支持自然对话触发 LLM Tool（继续阅读、重新阅读、查看状态等）
- 支持调用当前会话模型生成阶段性读书笔记
- 支持后台定时阅读
- 支持主动分享阅读进展
- 为天使之魂 / LivingMemory 预留桥接点
- **WebUI 管理页面**：书籍管理、笔记查看与删除、阅读任务管理、设置同步、备份管理
- **WebUI 删除功能**（默认关闭，需开启 `webui_delete_enabled`，前后端双重校验）
- **服务器备份管理**：查看、下载、上传、恢复、删除备份文件
- **最后错误管理**：自动过期（TTL 可配置）+ 手动清除
- 自然语言不能删除笔记（管理操作仅限 WebUI）

## 第一版不支持

- 自动全网找书
- 盗版书源抓取
- EPUB/PDF/DOCX 解析
- 多书并行
- 强依赖其他记忆插件

## 安装

将本插件放入 AstrBot 的插件目录：

```text
AstrBot/data/plugins/astrbot_plugin_autoread/
```

重启 AstrBot 或在 WebUI 中热重载插件。

## 使用方式

### 准备书籍

把 txt/md 文件放入：

```text
AstrBot/data/plugin_data/astrbot_plugin_autoread/books/
```

也可通过 WebUI 上传书籍。

### 命令入口

**继续阅读与进度**：

```text
/read ping                 检查插件是否正常加载
/read bind                 绑定当前会话
/read import <文件名>       导入书籍
/read list                 列出已导入书籍
/read start <book_id>      开始阅读
/read step                 继续阅读下一段（推进主进度）
/read status               查看阅读进度
/read notes [条数]          查看最近笔记
/read pause                暂停后台阅读
/read resume               恢复后台阅读
/read stop                 停止当前阅读
```

**重新阅读（不推进主进度）**：

```text
/read reread --note record_xxx              按笔记 ID 重读对应段
/read reread --book book_xxx --from 35% --to 40%
/read reread --book book_xxx --from-index 5 --to-index 5
/read reread --help                         查看帮助
```

**设置阅读进度（不读取内容）**：

```text
/read progress                             查看当前进度
/read progress set --book book_xxx --percent 35%
/read progress set --book book_xxx --index 10
```

### 自然对话入口

自然对话工具调用依赖模型支持 Function Calling。可用工具：`autoread_list_books`、`autoread_choose_book`、`autoread_start_book`、`autoread_read_next`、`autoread_get_status`、`autoread_get_notes`、`autoread_pause`、`autoread_resume`、`autoread_stop`。

```text
你自己挑一本感兴趣的书读吧。         → 选书并开始阅读
你现在继续读一点。                   → 继续阅读下一段
你最近读到哪里了？                   → 查看进度
重新读一下第三章。                   → 重新阅读指定范围（不推进主进度）
把进度调到 35%。                    → 设置阅读进度（不读取内容）
```

### 重要边界

- **继续阅读**（`/read step`）：从主进度读取下一段，推进进度。
- **重新阅读**（`/read reread`）：读取指定范围，不推进主进度，不删除旧笔记。
- **设置进度**（`/read progress set`）：修改进度指针，不读取内容，不创建笔记。
- **删除笔记**：只能通过 WebUI 执行，不能在聊天中触发。

### WebUI 管理页面

通过 AstrBot Dashboard → 插件管理 → AutoRead → 管理页面进入。详见 [WEBUI.md](docs/WEBUI.md)。

主要功能：总览、书籍管理（上传/查看/删除）、笔记管理（查看/搜索/删除）、阅读任务（查看/取消/清理）、设置（所有配置项）、备份管理（导出/导入/列表/下载/恢复/删除）。

## 配置

所有配置项通过 AstrBot WebUI 可视化修改，详见 `_conf_schema.json` 和 [配置说明](docs/CONFIGURATION.md)。

主要配置项：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| enabled | 是否启用插件 | true |
| enable_llm_tools | 是否启用自然对话工具 | true |
| chunk_size | 每次阅读最大字符数 | 1800 |
| default_interval_minutes | 默认阅读间隔（分钟） | 1440 |
| auto_share_mode | 主动分享模式 | chapter |
| webui_delete_enabled | 允许 WebUI 删除数据（默认关闭） | false |
| webui_last_error_ttl_minutes | 最后错误显示时长（分钟） | 30 |

## 重要说明

- **删除笔记只能通过 WebUI 管理页面执行**，不能在自然语言聊天中触发。
- 删除是永久操作，但可通过备份恢复找回。
- 插件不提供手工新增/编辑笔记的入口。重新阅读是重新生成内容的方式。
- 导出备份会先保存到服务器，再弹出结果框供选择下载或仅保存。
- 从服务器备份恢复时会自动解析，不需要手动预览。

## 文档

- [架构说明](docs/ARCHITECTURE.md)
- [使用指南](docs/USAGE.md)
- [配置说明](docs/CONFIGURATION.md)
- [WebUI 说明](docs/WEBUI.md)
- [备份说明](docs/BACKUP.md)
- [开发指南](docs/DEVELOPMENT.md)
- [测试指南](docs/TESTING.md)

## 注意事项

1. 第一版仅支持 txt/md 格式的本地文本文件
2. 阅读笔记由 LLM 生成，质量取决于当前会话模型
3. 主动消息需要平台支持（aiocqhttp / satori 支持较好）
4. 运行数据保存在 `AstrBot/data/plugin_data/astrbot_plugin_autoread/`，不要手动修改
5. 重新阅读（reread）不推进主进度；继续阅读（step）推进主进度；设置进度（progress set）不读取内容

## 许可

GNU Affero General Public License v3.0

## AI 使用说明

本项目在开发过程中使用了 AI 辅助工具，用于代码生成、架构设计迭代、文档编写和代码审查。所有 AI 生成的内容均经过人工审查和验证。

具体使用场景包括：

- 插件骨架搭建与模块分层设计
- 服务层（AutoReadService、NoteWriter、ReadingStateStore 等）代码生成
- WebUI 管理页面的前后端实现
- 双模型分工阅读架构（ModelRouter + ProviderResolver）的设计与实现
- 统一阅读记录 Schema（ReadingRecord）的设计与旧格式兼容
- 配置管理模块（ConfigService）与 WebUI 设置页同步
- 公开文档的撰写与维护

AI 工具未直接接触：真实运行数据、测试书籍全文、用户聊天记录、API 密钥、服务器信息或任何生产环境敏感数据。
