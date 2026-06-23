# DEVELOPMENT

本文档面向希望扩展或调试本插件的开发者。

## 项目结构

```text
astrbot_plugin_autoread/
├── main.py              # 插件入口、命令注册、LLM Tool 注册
├── metadata.yaml        # 插件元数据
├── _conf_schema.json    # 配置 schema
├── requirements.txt     # 依赖
├── README.md
├── services/
│   ├── autoread_service.py   # 业务编排核心
│   ├── book_loader.py        # 本地书籍导入
│   ├── text_chunker.py       # 文本切片
│   ├── reading_state.py      # 状态持久化
│   ├── note_writer.py        # LLM 笔记生成
│   └── memory_bridge.py      # 记忆桥接扩展点
├── worker/
│   └── reading_worker.py     # 后台定时调度
├── skills/
│   └── reading-assistant/
│       └── SKILL.md          # LLM 工具调用规则
└── docs/
    ├── ARCHITECTURE.md
    ├── USAGE.md
    ├── CONFIGURATION.md
    ├── DEVELOPMENT.md
    └── TESTING.md
```

## 开发原则

1. 不修改 AstrBot 本体
2. 插件入口为 `main.py`，所有 handler 写在插件类内部
3. 业务逻辑必须放在 `services/`，命令和 LLM Tool 共用 `AutoReadService`
4. 运行数据写入 `data/plugin_data/astrbot_plugin_autoread/`
5. LLM 只能基于当前 chunk 写笔记，不得假装读完整本书
6. LLM 调用失败不得推进进度
7. 主动发送失败不得丢失笔记或回滚进度

## 添加新功能

### 添加新的书籍来源

扩展 `BookLoader` 或新增实现 `BookSource` 接口。参考 `import_local_book()` 的方法签名和安全约束。

### 添加新的切片策略

扩展 `TextChunker.split()`，或新增 `ChunkStrategy` 接口实现。

### 添加新的记忆后端

扩展 `MemoryBridge`，或新增适配器（如 `angel_memory_adapter.py`）。不要在第一版直接调用其他插件的私有函数。

### 添加新的 LLM Tool

在 `main.py` 中添加 `@filter.llm_tool` 方法，内部调用 `AutoReadService`。确保 docstring 的 `Args` 格式正确：`参数名(类型): 描述`。

## 调试

### 查看日志

插件日志包含 `[AutoRead]` 前缀，可通过 AstrBot 日志查看。

### 测试流程

```text
/read ping                # 检查加载
/read bind                # 绑定会话
/read import test.txt     # 导入测试文本
/read list                # 验证已导入
/read start <book_id>     # 开始阅读
/read step                # 手动推进
/read notes               # 查看笔记
/read status              # 查看状态
```

## 兼容性

- 最低 AstrBot 版本：4.16
- Python：3.10+
- 运行数据路径兼容低版本 AstrBot（fallback 到 `cwd/data/`）
