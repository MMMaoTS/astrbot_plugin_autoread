# CONFIGURATION

所有配置项在 AstrBot WebUI 中可视化修改，无需编辑代码。配置文件为 `_conf_schema.json`。

## 配置项

### 基础配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enabled` | bool | true | 是否启用插件 |
| `enable_llm_tools` | bool | true | 是否启用自然对话工具入口 |

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
