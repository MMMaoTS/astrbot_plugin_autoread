# DEVELOPMENT

本文档面向希望扩展或调试本插件的开发者。

## 项目结构

```text
astrbot_plugin_autoread/
├── main.py                 # 插件入口、命令注册、LLM Tool、WebUI 初始化
├── metadata.yaml           # 插件元数据
├── _conf_schema.json       # 配置 schema
├── requirements.txt        # 依赖
├── README.md
├── services/
│   ├── autoread_service.py   # 业务编排核心
│   ├── book_loader.py        # 本地书籍导入
│   ├── text_chunker.py       # 文本切片
│   ├── note_writer.py        # LLM 笔记生成
│   ├── memory_bridge.py      # 记忆桥接扩展点
│   ├── backup_service.py     # 备份导入/导出/管理
│   ├── provider_resolver.py  # LLM Provider 解析
│   └── model_router.py       # 模型路由
├── core/
│   ├── config_service.py     # 配置管理
│   ├── page_api.py           # WebUI API 路由（30 条）
│   └── page_service.py       # WebUI 业务编排
├── repositories/
│   └── reading_state_repository.py  # state.json + notes 持久化
├── models/
│   └── reading_record.py     # 笔记记录 schema
├── worker/
│   └── reading_worker.py     # 后台定时调度
├── pages/
│   └── manager/
│       └── index.html        # WebUI 单文件页面
├── skills/
│   └── reading-assistant/
│       └── SKILL.md          # LLM 工具调用规则
└── docs/
    ├── ARCHITECTURE.md
    ├── USAGE.md
    ├── CONFIGURATION.md
    ├── BACKUP.md
    ├── WEBUI.md
    ├── DEVELOPMENT.md
    └── TESTING.md
```

## 开发原则

1. 不修改 AstrBot 本体
2. 插件入口为 `main.py`，所有 handler 写在插件类内部
3. 业务逻辑放在 `services/` 和 `core/`，命令和 LLM Tool 共用 `AutoReadService`
4. 运行数据写入 `data/plugin_data/astrbot_plugin_autoread/`
5. WebUI API 通过 `context.register_web_api()` 注册，不使用 `astrbot.api.web`
6. LLM 只能基于当前 chunk 写笔记，不得假装读完整本书
7. LLM 调用失败不得推进进度
8. 主动发送失败不得丢失笔记或回滚进度
9. 自然语言工具不得暴露删除笔记/书籍能力
10. reread 不得调用 read_next；reread 不推进主进度
11. set_progress 不读取内容、不创建笔记

## WebUI API

所有 API 通过 `context.register_web_api(route, handler, methods, desc)` 注册。

### 概览与状态
| 方法 | 路由 | 说明 |
|---|---|---|
| GET | `/astrbot_plugin_autoread/overview` | 插件概览 |
| GET | `/astrbot_plugin_autoread/status` | capabilities/config |

### 书籍
| 方法 | 路由 | 说明 |
|---|---|---|
| GET | `/astrbot_plugin_autoread/books` | 书籍列表 |
| GET | `/astrbot_plugin_autoread/books/<book_id>` | 书籍详情 |
| POST | `/astrbot_plugin_autoread/books/upload` | 上传书籍 |
| POST | `/astrbot_plugin_autoread/books/delete` | 删除书籍 |

### 笔记
| 方法 | 路由 | 说明 |
|---|---|---|
| GET | `/astrbot_plugin_autoread/notes` | 笔记列表 |
| GET | `/astrbot_plugin_autoread/notes/<book_id>/<note_id>` | 笔记详情 |
| POST | `/astrbot_plugin_autoread/notes/delete` | 删除笔记 |

### 任务
| 方法 | 路由 | 说明 |
|---|---|---|
| GET | `/astrbot_plugin_autoread/sessions` | 任务列表 |
| POST | `/astrbot_plugin_autoread/sessions/cancel` | 取消任务 |
| POST | `/astrbot_plugin_autoread/sessions/clear-finished` | 清理已完成 |

### 设置与错误
| 方法 | 路由 | 说明 |
|---|---|---|
| GET | `/astrbot_plugin_autoread/settings` | 读取设置 |
| POST | `/astrbot_plugin_autoread/settings` | 保存设置 |
| POST | `/astrbot_plugin_autoread/status/clear-error` | 清除错误 |

### 备份
| 方法 | 路由 | 说明 |
|---|---|---|
| GET | `/astrbot_plugin_autoread/backups` | 备份列表 |
| GET | `/astrbot_plugin_autoread/backups/download/<name>` | 下载备份 |
| POST | `/astrbot_plugin_autoread/backups/export/books` 等 | 导出备份 |
| POST | `/astrbot_plugin_autoread/backups/import/preview` | 导入预览 |
| POST | `/astrbot_plugin_autoread/backups/import/apply` | 执行导入 |
| POST | `/astrbot_plugin_autoread/backups/upload` | 上传备份 |
| POST | `/astrbot_plugin_autoread/backups/inspect` | 预检备份 |
| POST | `/astrbot_plugin_autoread/backups/restore` | 从服务器恢复 |
| POST | `/astrbot_plugin_autoread/backups/delete` | 删除备份 |
| POST | `/astrbot_plugin_autoread/backups/export-to-server` | 导出到服务器 |

## 添加新功能

### 添加新的书籍来源

扩展 `BookLoader` 或新增实现 `BookSource` 接口。

### 添加新的切片策略

扩展 `TextChunker.split()`，或新增 `ChunkStrategy` 接口实现。

### 添加新的记忆后端

扩展 `MemoryBridge`，或新增适配器。

### 添加新的 LLM Tool

在 `main.py` 中添加 `@filter.llm_tool` 方法，内部调用 `AutoReadService`。注意：
- 不要暴露删除类操作作为 LLM Tool
- docstring 的 `Args` 格式：`参数名(类型): 描述`

### 添加新的 WebUI API

1. 在 `core/page_service.py` 中添加业务方法
2. 在 `core/page_api.py` 中注册路由并添加 handler
3. 返回格式统一使用 `self._ok(data)` / `self._err(message)`

## 调试

### 查看日志

插件日志包含 `[AutoRead]` 前缀，WebUI 日志包含 `[AutoRead WebUI]` 前缀。

### 测试流程

```
/read ping                # 检查加载
/read bind                # 绑定会话
/read import test.txt     # 导入测试文本
/read list                # 验证已导入
/read start <book_id>     # 开始阅读
/read step                # 手动推进
/read notes               # 查看笔记
/read status              # 查看状态
```

### WebUI 调试

打开浏览器 DevTools → Console，所有操作均有 `[autoread-ui]` 前缀日志。

## 兼容性

- 最低 AstrBot 版本：4.16
- Python：3.10+
- 运行数据路径兼容低版本 AstrBot（fallback 到 `cwd/data/`）
