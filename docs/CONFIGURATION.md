# CONFIGURATION

所有配置项在 AstrBot WebUI 中可视化修改，无需编辑代码。配置文件为 `_conf_schema.json`。

## 配置项

### 基础配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enabled` | bool | true | 是否启用插件 |
| `enable_llm_tools` | bool | true | 是否启用自然对话工具入口 |
| `enabled_umos` | list | **`[]`** | 启用自然读书能力的 UMO 列表。**默认空列表 = 不启用自然化能力**。仅列表中的会话/对象会启用 LLM Tool 等自然读书能力。`/read` 兜底命令不受此限制。多个 UMO 以逗号或换行分隔。UMO 是能力生效范围，不是书籍所有权边界。 |
| `auto_import_uploaded_books` | bool | **false** | 上传文件自动入库开关。开启后，在 `enabled_umos` 命中的会话中上传 txt/md 文件时自动导入到角色书架。**默认关闭**。导入后默认不主动回复。需 `enabled_umos` 同时命中才生效。属于书架更新事件，不是对话回复事件。 |

### 阅读控制

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `allow_llm_read_next` | bool | true | 是否允许 LLM 主动推进阅读 |
| `max_tool_read_steps_per_turn` | int | 1 | 单轮对话最多推进次数（预留） |
| `default_interval_minutes` | int | 1440 | 默认阅读间隔（分钟） |
| `worker_tick_seconds` | int | 60 | 后台 worker 扫描间隔（秒） |

### 切片配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `chunk_size` | int | 1800 | 每次阅读的最大字符数 |
| `chunk_overlap` | int | 120 | 切片重叠字符数 |
| `max_notes_per_book` | int | 200 | 每本书最多保留笔记条数 |

### 角色表达配置（LLM Expression）

> **⚠️ 验证阶段**：以下配置项大部分默认关闭，当前处于功能验证阶段。LLM 表达不等于长期记忆写入，详见 [记忆边界与输出分级策略](../../../../autoread.md#20-记忆边界与输出分级策略)。

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enable_role_expr_command_query` | bool | **false** | 控制 `/read status`、`/read list`、`/read progress` 等命令查询类输出是否通过 LLM 进行临时角色化表达。**默认关闭**。开启后可能增加 token 消耗，需关注记忆插件是否采集命令类回复。 |
| `enable_role_expr_read_action` | bool | **false** | 控制 `/read step`、`/read reread` 等命令型阅读动作是否通过 LLM 进行角色化表达。**默认关闭**。当前处于验证阶段，不应在未完成验证前开启。 |
| `enable_role_expr_notes` | bool | **false** | 控制读书笔记/摘要类内容是否通过 LLM 进行角色化表达。**默认关闭**。开启前需确认笔记生成与记忆边界。 |
| `enable_role_expr_worker_share` | bool | **false** | 控制 Worker 主动分享是否通过 LLM 进行角色化表达。**默认关闭**。开启前需确认平台流水、短期历史与长期记忆边界。 |
| `enable_role_expr_natural_chat` | bool | **true** | 控制自然语言读书交互是否允许角色表达。**默认开启**。不等于自动写入长期记忆——记忆是否写入仍由记忆插件策略决定。 |

**使用建议**：

1. 命令查询类（`command_query`）默认关闭，建议仅在测试环境中开启。
2. 开启 `enable_role_expr_command_query` 后，下一步应测试 `/read status`，验证 LLM 表达是否正常、记忆插件是否产生噪声。
3. 阅读动作类（`read_action`）、笔记类（`notes`）、Worker 分享类（`worker_share`）当前不应开启，等待后续迁移验证。
4. 如果开启后发现记忆污染（angel_memory 等插件采集了命令类回复），应先关闭对应开关，参考文档中的记忆边界说明排查。

### 分享与记忆

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `auto_share_mode` | string | chapter | 主动分享模式 |
| `memory_backend` | string | none | 长期记忆后端 |

### 导入控制

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `allow_url_import` | bool | false | 是否允许 URL 导入（暂未实现） |
| `allowed_extensions` | list | [".txt", ".md"] | 允许导入的扩展名 |

### 提示词

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `reading_persona_prompt` | text | （见默认值） | 阅读笔记生成时的额外提示词 |

### WebUI 功能开关

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `webui_enabled` | bool | true | 是否启用 WebUI 管理页面 |
| `webui_upload_enabled` | bool | true | 是否允许 WebUI 上传书籍 |
| `webui_max_upload_mb` | int | 10 | WebUI 上传文件大小上限（MB） |
| `webui_delete_enabled` | bool | **false** | 允许 WebUI 删除书籍和笔记。**危险操作，默认关闭，前后端双重校验** |
| `webui_last_error_ttl_minutes` | int | 30 | 最后错误在 WebUI 中的显示时长（分钟），超时自动隐藏 |
| `webui_notes_export_enabled` | bool | true | 是否允许 WebUI 导出笔记 |

## auto_share_mode 取值

- `none`：永不主动分享，只保存笔记
- `every_step`：每读一段都分享
- `daily`：每日首次阅读后分享（暂按 chapter 处理）
- `chapter`：note.should_share 为 true 时分享
- `finish`：读完整本书后分享（暂按 chapter 处理）

## memory_backend 取值

- `none`：不写入外部记忆（默认）
- `angel_memory`：写入天使之魂插件（暂未实现）
- `livingmemory`：写入 LivingMemory 插件（暂未实现）
