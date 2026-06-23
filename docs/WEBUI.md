# WEBUI

AutoRead WebUI 管理页面用于可视化管理书籍、阅读任务、阅读笔记、插件设置和备份恢复。

## 功能概述

- 查看总览：书籍数、笔记数、活跃阅读任务数和最近错误（带 TTL 自动过期）。
- 上传并导入本地文本书籍，上传提示会随配置动态更新。
- 浏览已导入书籍，支持删除（需开启 `webui_delete_enabled`）。
- 浏览活跃阅读任务，支持取消任务和清理历史记录。
- 浏览、搜索和查看阅读笔记，支持删除（需开启 `webui_delete_enabled`）。
- 查看并修改插件配置，保存后写回 AstrBot 原生插件配置。
- 查看可用 provider，并将 provider_id 应用到阅读模型、思考模型或单模型配置。
- 导出书籍、笔记或完整备份，并以合并模式导入备份。
- 手动清除最后错误记录。

## 页面导航

页面顶部有标签导航：

- **总览**：插件概览。
- **书籍**：上传和管理书籍。
- **阅读任务**：当前阅读会话状态。
- **笔记**：浏览和搜索阅读笔记，只读。
- **设置**：查看和修改插件配置。
- **备份恢复**：导出备份、预览备份、合并导入和查看导入历史。

## 如何打开

1. 进入 AstrBot WebUI。
2. 进入「插件管理」。
3. 找到 `astrbot_plugin_autoread`。
4. 点击进入插件详情页。
5. 打开「AutoRead 管理」页面入口。

## 书籍管理

### 上传书籍

1. 点击「选择文件」，选择本地文本文件。
2. 点击「上传并导入」。
3. 上传完成后自动切片并注册到书籍列表。

支持的文件类型来自 `Reading_Settings.allowed_extensions`，默认 `.txt`、`.md`。

单个文件大小限制来自 `WebUI_Settings.webui_max_upload_mb`，默认 10 MB。

上传功能可通过 `WebUI_Settings.webui_upload_enabled` 关闭。

### 查看书籍

书籍列表展示书名、book_id、字符数、切片数、笔记数、是否活跃、阅读进度和创建时间。

点击「详情」可查看书籍元数据和活跃会话信息。

### 删除书籍

删除功能默认关闭。开启方式：设置页勾选「允许 WebUI 删除数据」并保存。

- 删除开关关闭时：删除按钮灰色禁用，tooltip 提示"删除功能未启用"。
- 删除开关开启时：点击删除按钮 → 自定义确认弹窗 → 确认后删除。
- 如果书籍仍有关联笔记，后端拒绝删除并提示先删除笔记。
- 删除操作不可逆，请谨慎使用。

## 当前阅读任务

阅读任务页展示当前正在进行的阅读任务，包括：

- 脱敏会话 ID，不暴露真实 `unified_msg_origin`。
- 当前书名。
- 阅读进度。
- 暂停或活跃状态。
- 最近阅读时间和下次阅读时间。

### 任务管理

- **取消任务**：点击「取消」→ 确认弹窗 → 调用后端停止该 session 的阅读。
- **清理已完成**：点击「清理已完成/已取消」→ 确认弹窗 → 移除所有已结束的任务记录。
- 取消操作不会强制中断正在执行的 LLM 调用；已取消的 session 在 worker 下次 tick 时不再触发。

## 笔记查看

### 笔记列表

- 可按书籍筛选。
- 可按关键词搜索。
- 分页浏览。
- 展示时间、书名、章节、段索引、记录类型、模型、重要性和摘要。

### 笔记详情

点击「查看」可阅读单条笔记的完整内容，包括：

- 摘要、细节、感想。
- 长期记忆摘要。
- 分享文案。
- 待解问题。
- record_id、book_id、章节、段索引、provider、创建时间等元数据。

### 删除笔记

删除功能默认关闭，受 `webui_delete_enabled` 控制。

- 删除开关关闭时：删除按钮灰色禁用。
- 删除开关开启时：点击删除 → 自定义确认弹窗 → 确认后从 JSONL 文件中移除对应记录。
- 删除操作不可逆。

## 错误管理

Overview 页显示最近一次错误（来自任意 session 的 `last_error` 字段）。

- **自动过期**：错误带时间戳，超过 `webui_last_error_ttl_minutes`（默认 30 分钟）后自动隐藏。
- **手动清除**：点击错误卡片中的「清除错误」→ 确认弹窗 → 后端清空所有 session 的 `last_error`。
- 清除操作不可逆，但不会影响已持久化的笔记和书籍数据。

## 确认弹窗

插件 WebUI 使用自定义确认弹窗（非浏览器原生 `window.confirm`），在 AstrBot 插件 iframe 内可靠显示。

- 确认：点击「确认」按钮。
- 取消：点击「取消」按钮、按 Esc、或点击遮罩区域。
- 弹窗 z-index 足够高，不会被详情面板遮挡。

## 状态与能力查询

页面初始化时调用 `GET /status` 获取后端能力：

```json
{
  "capabilities": {
    "delete_books": false,
    "delete_notes": false,
    "manage_tasks": true,
    "clear_error": true
  },
  "config": {
    "webui_delete_enabled": false,
    "webui_last_error_ttl_minutes": 30
  }
}
```

前端根据 `capabilities` 渲染按钮状态。删除功能默认关闭，需在设置中开启。

## 备份恢复

### 导出备份（新流程）

1. 点击「导出书籍」「导出笔记」或「导出完整备份」。
2. 后端生成备份文件并保存到服务器 `backups/` 目录。
3. 弹出「备份已生成」信息框，展示文件名、大小、时间、书籍/笔记/任务数量。
4. 用户选择：**下载备份**（立即下载）、**仅保存到服务器**（关闭弹窗，文件保留）、**关闭**（同保存）。
5. 导出不自动触发浏览器下载。备份列表自动刷新。

备份恢复页支持三类导出：

- 书籍备份：包含书籍原文、切片和 `book_index.json`。
- 笔记备份：包含笔记 JSONL 和 `records_index.json`。
- 完整备份：包含书籍、切片、笔记和只读状态快照。

## 服务器备份管理

备份 tab 新增「服务器备份管理」区域，用于管理服务器上的备份文件。

### 查看备份文件

列出服务器 backups 目录下的所有 .zip 备份文件，显示文件名、大小、修改时间和来源（导出/上传）。

新导出的备份文件名格式：`autoread_backup_{type}_{YYYYMMDD_HHMMSS}_{random}.zip`。

上传的备份文件名格式：`autoread_uploaded_{YYYYMMDD_HHMMSS}_{original_name}.zip`。

旧格式备份文件仍兼容，可正常查看、下载、恢复、删除。

### 下载备份文件

点击列表中的「下载」按钮即可下载对应备份文件到本地。下载不修改服务器数据，无需确认。

### 上传备份到服务器

### 上传备份到服务器

1. 选择本地 .zip 备份文件。
2. 点击「上传到服务器」。
3. 文件保存到服务器 backups 目录，出现在列表中。
4. 上传后不自动恢复，需手动点击「恢复」。

### 从服务器备份恢复

1. 在备份列表中点击某个文件的「恢复」。
2. 系统**自动解析**备份文件（不需要手动点击"解析"）。
3. 弹出恢复确认框，展示备份类型、书籍/笔记数量、恢复模式。
4. 确认弹窗 → 确认后执行合并导入恢复。
5. 恢复后刷新书籍、笔记、任务列表。
6. 恢复前的数据不会自动备份；如需保护当前数据，请先手动导出备份。

### 删除服务器备份

1. 在备份列表中点击「删除」。
2. 确认弹窗 → 确认后删除服务器上的备份文件。
3. 删除不可逆。
4. 后端防路径穿越校验：只能删除 backups 目录下的 .zip 文件。

### 上传恢复文件流程

上传恢复文件分两步：
1. 上传 .zip 备份文件到服务器（进入备份列表）。
2. 在备份列表中找到上传的文件，点击「恢复」。
两步分离可以避免误操作直接覆盖数据。

## 笔记恢复说明

- 删除笔记是永久删除当前数据中的笔记记录。
- 删除不会进入回收站，不会生成软删除状态。
- **删除笔记只能通过 WebUI 管理页面执行**，不能在自然语言聊天中触发。
- 如果误删笔记，可通过备份恢复找回（前提是之前导出过包含该笔记的备份）。
- 备份恢复是整包合并导入，按 record_id 去重。不会覆盖已有笔记。
- 插件不提供手工新增/编辑笔记的 WebUI 入口。
- 如果需要重新获得某段内容的新笔记，应通过重新阅读创建任务，而非手工添加。
- WebUI 中删除笔记受 `webui_delete_enabled` 配置开关和二次确认保护。

导入采用合并模式：

- 必须先解析备份并查看预览，再执行合并导入。
- 已存在的 `backup_id` 会跳过。
- 书籍按 `book_index.json` 中的 `book_id` 去重。
- 笔记按 `record_id` 或旧 `note_id` 去重。
- 仅导入新 ID，不覆盖、不删除、不替换现有数据。
- 完整备份中的状态或配置快照仅作只读参考，导入时不会恢复或覆盖当前配置。

## 设置页

设置页通过顶部「设置」标签进入。进入时会加载当前 AstrBot 插件配置和可用 provider 列表。

### 配置分组

- **基础设置**：启用阅读、默认阅读间隔、后台检查间隔、分享方式、对话工具开关、模型主动推进开关。
- **阅读设置**：切片长度、切片重叠、笔记上限、允许扩展名、阅读提示词、链接导入、记忆后端。
- **模型设置**：模型策略、阅读模型、思考模型、单模型、阶段路由、深入复核参数。
- **页面设置**：WebUI 页面开关、上传开关、上传大小上限、删除功能开关（默认关闭）、错误显示时长、笔记导出开关。
- **扩展设置**：链接导入和记忆后端入口。

### 模型策略

设置页提供三种模型策略：

| 策略 | 说明 |
|------|------|
| `current_session` | 使用当前聊天会话绑定的模型。 |
| `single` | 所有阶段使用 `single_provider_id`。 |
| `dual` | 片段阅读使用 `reader_provider_id`，总结、复核、记忆和分享使用 `thinker_provider_id`。 |

开启 `enable_stage_routing` 后，可分别配置：

- `stage_chunk_note_provider_id`
- `stage_chunk_review_provider_id`
- `stage_chapter_note_provider_id`
- `stage_final_review_provider_id`
- `stage_memory_note_provider_id`
- `stage_user_share_provider_id`

阶段 provider 留空时回退到当前策略下的默认分工。

### 配置持久化

设置保存后直接写回 AstrBot 原生插件配置 `AstrBotConfig`，并调用 `save_config()`。

展示时缺失字段会使用 `_conf_schema.json` 默认值兜底，但不会把默认值覆盖写入真实配置。

当前配置来源优先级为：

```text
AstrBotConfig 实际保存值 > _conf_schema.json 默认值展示兜底
```

插件不以 `settings_override.json` 作为主配置源。

## 配置项速查

| 配置项 | 分组 | 说明 | 默认值 |
|--------|------|------|--------|
| enabled | Basic_Settings | 是否启用阅读业务 | true |
| default_interval_minutes | Basic_Settings | 默认阅读间隔，单位分钟 | 1440 |
| worker_tick_seconds | Basic_Settings | 后台检查间隔，单位秒 | 60 |
| auto_share_mode | Basic_Settings | 分享方式 | chapter |
| enable_llm_tools | Basic_Settings | 是否启用 LLM Tool | true |
| allow_llm_read_next | Basic_Settings | 是否允许模型主动推进阅读 | true |
| chunk_size | Reading_Settings | 切片长度 | 1800 |
| chunk_overlap | Reading_Settings | 切片重叠 | 120 |
| allowed_extensions | Reading_Settings | 允许上传或导入的扩展名 | [`.txt`, `.md`] |
| memory_backend | Reading_Settings | 记忆后端 | none |
| model_strategy | Model_Settings | 模型策略 | dual |
| reader_provider_id | Model_Settings | 阅读模型 provider_id | 空 |
| thinker_provider_id | Model_Settings | 思考模型 provider_id | 空 |
| single_provider_id | Model_Settings | 单模型 provider_id | 空 |
| enable_stage_routing | Model_Settings | 是否启用阶段路由 | false |
| enable_deeper_review | Model_Settings | 是否启用深入复核 | true |
| importance_threshold | Model_Settings | 重要性阈值 | 0.75 |
| max_reviews_per_chapter | Model_Settings | 每章复核上限 | 3 |
| webui_enabled | WebUI_Settings | 是否启用 WebUI 页面 | true |
| webui_upload_enabled | WebUI_Settings | 是否允许 WebUI 上传 | true |
| webui_max_upload_mb | WebUI_Settings | 上传文件大小限制，单位 MB | 10 |
| webui_delete_enabled | WebUI_Settings | 允许 WebUI 删除书籍和笔记（危险操作，默认关闭） | false |
| webui_last_error_ttl_minutes | WebUI_Settings | 最后错误显示时长，超时自动隐藏 | 30 |
| webui_allow_book_delete | WebUI_Settings | 已弃用，请使用 webui_delete_enabled | false |
| webui_notes_export_enabled | WebUI_Settings | 是否允许导出笔记 | true |

## 数据存储位置

运行数据保存在 AstrBot 数据目录下：

```text
AstrBot/data/plugin_data/astrbot_plugin_autoread/
├── state.json          # 状态和书籍元数据
├── books/              # 上传的书籍文件
├── chunks/             # 切片文件
├── notes/              # 笔记 JSONL 文件
└── backups/            # 导出备份和导入历史
```

插件源码目录下如存在 `data/` 或 `skills/`，不属于 WebUI 运行数据目录。未确认用途前不应直接删除。

## 前端实现说明

当前实际运行页面是 `pages/manager/index.html`，样式和脚本均内联在该文件中。

旧的 `pages/manager/app.js` 和 `pages/manager/style.css` 未被引用且容易与实际运行代码不同步，已清理。

## 安全限制

- 上传文件扩展名白名单校验。
- 文件名安全化，防路径穿越。
- 文件大小限制。
- `book_id`、`note_id` 格式校验。
- 备份导入路径限制在插件数据目录内。
- session_id 脱敏展示。
- 不返回宿主机绝对路径。
- 不返回完整 `unified_msg_origin`。
- 删除功能默认关闭，前后端双重校验 `webui_delete_enabled`。
- 删除笔记通过 JSONL 原子写回实现，不损坏其他记录。
- 备份只做合并导入，不覆盖、不删除。

## 常见问题

### 页面不显示？

1. 检查 `pages/manager/index.html` 是否存在。
2. 检查插件是否启用。
3. 检查 `WebUI_Settings.webui_enabled` 是否为 true。
4. 重载插件。
5. 查看日志确认出现 `WebUI API routes registered`。

### 上传失败？

1. 检查文件扩展名是否在 `allowed_extensions` 中。
2. 检查文件大小是否超过 `webui_max_upload_mb`。
3. 检查 `webui_upload_enabled` 是否为 true。

### API 返回 404？

1. 检查 WebUI 路由是否注册成功。
2. 重载插件。
3. 查看 AstrBot 日志。

### 设置保存后不生效？

1. 确认保存时提示「设置已保存」。
2. 阅读业务设置会从 `ConfigService` 动态读取。
3. 上传扩展名、切片参数和记忆后端会在保存后同步到运行实例。
4. `worker_tick_seconds` 在下一轮 worker 循环生效。
5. 如果关闭 `webui_enabled`，需要重载插件使页面入口状态刷新。

### 如何获取可用 provider_id？

1. 进入设置页，查看 provider 列表。
2. 如果列表为空，说明当前运行环境未提供可用模型列表接口。
3. 向 AstrBot 管理员获取 provider_id 后手动填写或在原生插件设置中配置。

### 点击按钮无反应？

1. 打开浏览器 DevTools → Console，查看是否有 `[autoread-ui]` 开头的日志。
2. 如果看到 `action xxx` 但无后续日志：
   - `blocked by capability` → 功能被配置关闭（如删除功能）。
   - `cancelled by user` → 用户在确认弹窗中取消了操作。
   - `unhandled action` → 事件分发未匹配，检查 data-action 命名。
3. 如果 `confirm open` 出现但弹窗不显示 → 检查 z-index 或 CSS。
4. 如果 `request POST` 出现但无 `response` → 查看 Network 中请求状态码。
5. AstrBot 插件 Page 在受限 iframe 中运行，不应使用 `window.confirm`/`alert`/`prompt`。

### 确认弹窗不出现？

插件使用自定义 confirm modal（非浏览器原生弹窗）。如果弹窗不出现：
1. 检查 Console 是否有 `confirm open` 日志。
2. 检查 `#confirm-overlay` DOM 元素是否存在。
3. 检查 `.modal-overlay.hidden` CSS 是否正确切换。
