# WEBUI

AutoRead WebUI 管理页面，用于可视化管理书籍和查看阅读笔记。

## 功能概述

- 查看总览（书籍数、笔记数、活跃阅读任务数）
- 上传 txt/md 书籍并自动导入、切片
- 浏览和管理已导入书籍
- 查看当前阅读任务（含脱敏会话 ID）
- 浏览和搜索阅读笔记
- 查看单条笔记详情
- 查看和修改插件配置（设置页）
- 选择阅读笔记生成所用的模型

## 页面导航

页面顶部有标签导航：

- **总览**：插件概览（书籍数、笔记数、活跃任务）
- **书籍**：上传和管理书籍
- **阅读任务**：当前阅读会话状态
- **笔记**：浏览和搜索阅读笔记（只读）
- **设置**：查看和修改插件配置

## 如何打开

1. 进入 AstrBot WebUI
2. 进入「插件管理」
3. 找到 `astrbot_plugin_autoread`
4. 点击进入插件详情页
5. 应看到「AutoRead 管理」页面入口

## 书籍管理

### 上传书籍

1. 点击「选择文件」，选择本地 .txt 或 .md 文件
2. 点击「上传并导入」
3. 上传完成后自动进行文本切片并注册到书籍列表

支持的文件类型：`.txt`、`.md`（可通过配置项 `allowed_extensions` 修改）。

单个文件大小限制：默认 10 MB（可通过配置项 `webui_max_upload_mb` 修改）。

### 查看书籍

书籍列表展示书名、book_id、字符数、切片数、笔记数、是否活跃、阅读进度和创建时间。

点击「详情」可查看书籍元数据和活跃会话信息。

## 当前阅读任务

展示当前正在进行的阅读任务，包括：

- 脱敏会话 ID（不暴露真实 unified_msg_origin）
- 当前书名
- 阅读进度
- 暂停/活跃状态
- 最近阅读时间和下次阅读时间

## 笔记查看

### 笔记列表

- 可按书籍筛选
- 可按关键词搜索（匹配所有字段）
- 分页浏览
- 展示时间、书名、章节、段索引、摘要和感想

### 笔记详情

点击「查看」可阅读单条笔记的完整内容，包括：

- 摘要、细节、感想
- 长期记忆摘要
- 分享文案
- 元数据（note_id、book_id、章节、段索引、创建时间）

### 笔记只读原则

笔记**仅可查看，不可修改**。前端和后端均不提供：

- 编辑笔记的功能
- 保存笔记修改的功能
- 删除单条笔记的功能
- 覆盖笔记文件的功能

## 数据存储位置

所有运行数据保存在：

```text
AstrBot/data/plugin_data/astrbot_plugin_autoread/
├── state.json          # 状态和书籍元数据
├── books/              # 上传的书籍文件
├── chunks/             # 切片文件
└── notes/              # 笔记 JSONL 文件
```

WebUI 上传的书籍与其他方式导入的书籍共用同一数据目录。

## 安全限制

- 上传文件扩展名白名单校验（前后端双重检查）
- 文件名安全化（防路径穿越）
- 文件大小限制
- book_id / note_id 格式校验
- session_id 脱敏展示（SHA256 哈希截断）
- 不返回宿主机绝对路径
- 不返回完整 unified_msg_origin
- 笔记完全只读
- 上传功能可通过配置关闭

## 设置页

设置页可通过顶部「设置」标签进入。进入时自动加载当前配置和可用模型列表。

### 配置分组

- **基础设置**：启用开关、默认阅读间隔、Worker 扫描间隔、主动分享模式
- **阅读设置**：chunk 大小、重叠字符数、角色可见阅读提示词
- **阅读模型设置**：模型选择策略、固定 provider 配置、可用模型列表
- **WebUI 设置**：上传开关、大小限制、删除/归档开关、笔记导出开关
- **扩展设置**：URL 导入开关、记忆后端选择

### 模型选择

设置页提供三种模型选择策略：

| 策略 | 说明 |
|------|------|
| `current_session` | 使用当前聊天会话绑定的模型（默认，兼容原有行为） |
| `fixed_provider` | 使用插件设置中指定的固定 provider_id |
| `default` | 使用系统/框架默认模型 |

选择 `fixed_provider` 时：
- 需要填写 Provider ID 和显示名称
- 如果可用模型列表非空，可点击模型项自动填入
- 如果可用模型列表为空，请手动填写 provider_id（可向 AstrBot 管理员获取）
- 可设置「固定模型不可用时回退到当前会话」

### 配置持久化

设置保存后写入 `settings_override.json`，存放于插件运行数据目录。优先级：

```text
settings_override.json > 框架 AstrBotConfig > _conf_schema 默认值
```

重启后设置自动恢复。

## 配置项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| webui_enabled | 是否启用 WebUI 管理页面 | true |
| webui_upload_enabled | 是否允许 WebUI 上传 | true |
| webui_max_upload_mb | 上传文件大小限制（MB） | 10 |
| webui_allow_book_delete | 是否允许删除/归档（暂未实现） | false |
| webui_notes_export_enabled | 是否允许导出笔记 | true |
| reading_model_mode | 模型选择策略 | current_session |
| reading_provider_id | 固定模型 provider_id | （空） |
| reading_provider_display_name | 固定模型显示名称 | （空） |
| fallback_to_current_session_provider | 是否允许回退到当前会话模型 | true |

## 常见问题

### 页面不显示？

1. 检查 `pages/manager/index.html` 是否存在
2. 检查插件是否启用
3. 在 WebUI 中重载插件
4. 检查配置项 `webui_enabled` 是否为 true

### 上传失败？

1. 检查文件扩展名是否为 .txt 或 .md
2. 检查文件大小是否超过限制
3. 检查配置项 `webui_upload_enabled` 是否为 true

### API 返回 404？

1. 检查 WebUI 路由是否正确注册
2. 重载插件
3. 查看 AstrBot 日志确认路由注册成功

### 设置保存后不生效？

1. 确认保存时提示 "设置已保存"
2. 部分配置（如 worker_tick_seconds）在下一轮 worker 循环生效
3. 模型选择策略立即生效（下次阅读笔记生成时使用新模型）
4. 如果修改了 `webui_enabled` 并关闭，需要重载插件使页面隐藏

### 如何获取可用的 provider_id？

1. 进入设置页，查看「可用模型列表」
2. 如果列表为空，说明当前框架不支持列出 provider
3. 请向 AstrBot 管理员获取可用的 provider_id
4. 手动填入 Provider ID 字段并保存
