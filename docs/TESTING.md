# TESTING

手工测试清单，用于验证插件各项功能。

## 基础加载

- [ ] 插件可正常加载，WebUI 热重载无异常
- [ ] `/read ping` 返回正常

## 状态管理

- [ ] `/read bind` 后 state.json 出现当前会话记录
- [ ] `/read status` 在未开始阅读时返回合理提示

## 书籍导入

- [ ] 导入 UTF-8 编码的 txt 文件
- [ ] 导入 UTF-8-SIG 编码的 txt 文件
- [ ] 拒绝包含 `../` 路径穿越的文件名
- [ ] 拒绝不支持的文件扩展名
- [ ] 拒绝不存在的文件名

## 文本切片

- [ ] 导入后生成 chunks json 文件
- [ ] chunk index 连续
- [ ] chunk text 非空
- [ ] 章节标题被正确识别

## 手动阅读

- [ ] `/read start <book_id>` 正确设置当前书
- [ ] `/read step` 调用 LLM 生成笔记
- [ ] `/read step` 推进 current_chunk_index
- [ ] LLM 调用失败时进度不推进
- [ ] JSON 解析失败时使用 fallback
- [ ] 读到末尾后正确提示已完成

## 命令完整闭环

- [ ] `/read import` → `/read list` → `/read start` → `/read step` → `/read notes`

## LLM Tool 自然对话

- [ ] 自然对话触发 autoread_list_books
- [ ] 自然对话触发 autoread_start_book
- [ ] 自然对话触发 autoread_read_next
- [ ] 工具调用日志在控制台可见
- [ ] enable_llm_tools=false 时工具返回关闭提示

## Worker 后台任务

- [ ] interval 设为 2 分钟时可自动推进
- [ ] `/read pause` 后 worker 不推进
- [ ] `/read resume` 后 worker 恢复推进
- [ ] 热重载后无重复 worker

## 主动消息

- [ ] 支持主动消息的平台可收到分享
- [ ] 不支持平台仅记录 last_error
- [ ] 发送失败不丢失笔记

## 测试数据准备

准备一个测试用 txt 文件放入 `AstrBot/data/plugin_data/astrbot_plugin_autoread/books/`，例如：

```text
第一章 开始

这是一段测试文本，用于验证 AutoRead 插件的导入、切片和阅读功能。
这段文本应该足够长，以便能够被切成多个 chunk 进行逐步阅读。
（在此添加更多文本内容以填满至少两个 chunk...）

第二章 继续

第二章的内容，用于验证章节检测功能。
（在此添加更多文本内容...）
```
