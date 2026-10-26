# Companion Core / AstrBot 4.29.3

实际核对官方标签源码 `astrbot/core/star/context.py`、`platform/astr_message_event.py`、`platform/sources/webchat`、`provider/sources/openai_source.py` 以及当前插件。

- 使用 `@filter.event_message_type(EventMessageType.ALL, priority=1000)`；处理时先 `should_call_llm(False)` 和 `stop_event()`，由插件独占 `context.llm_generate` 与 `event.send(event.plain_result(...))`。内置默认 LLM 另设 enable=false。真实 Web 管理命令确认只返回一次。
- 框架唤醒阶段会移除 `/`；命令读取 `event.message_obj.message_str` 的原始适配器文本，缺失时回退 event.get_message_str。已有回归。
- 模型 ID 从本机配置选择，非消息正文；人格只有一个 system_prompt 注入点。记忆唯一写入入口 MemoryStore.put，只接受 user_explicit。暂不做自然语言自动抽取。
- 身份来自 get_platform_id、get_self_id、get_sender_id；机器人键同时包含适配器 ID，不能用昵称合并。私聊在同一用户跨 Web 会话共享事实，群聊按 group_id 隔离，persona 独立。手机号与完整 ID 不写普通日志。
- 事件键由平台、机器人、会话、消息 ID 哈希组成，SQLite 唯一约束先占位。崩溃或发送状态不明时不重发：保证至多一次尝试，不能声称平台端恰好一次或必达。缺失稳定消息 ID 会拒绝处理。
- 所有生成及删除串行，避免迟到回复继续使用删除前记忆。请求最多一次，不执行工具或后台模型任务。
- 不读取 AstrBot 显示历史。插件自己的 turns 保存最多 3 轮/24 小时的短期上下文，每次注入上限 4000 UTF-8 字节，通过 UserMessageSegment / AssistantMessageSegment 传入 contexts。与长期事实分开；更正、删除、停用清空该用户可见范围的所有短期轮次，旧备份按 ledger revision 失效。
- `/忘记` 调用 conversation_manager.delete_conversations_by_user_id；Web 当前会话显示记录通过 get_platform_message_history / delete_platform_message_history_by_id 清理。该接口删除的是当前显示会话，其他会话旧记录保留，且不参与回复。
- 手机适配器默认关闭；绑定后只按可信登录返回的 owner user_id 建立精确私聊白名单，群聊和其他用户无响应。未经验证的主动发送不启用。

离线：32 项本地检查按组件通过（19 记忆、9 框架事件、4 配额脚本），框架测试的模型与发送为假实现。真实：Web 命令、20 条模型冒烟、重启及删除已验证；新增真实 M01 首轮 19/20，修正排序后失败题及关联 6/6，证据保留首轮失败。微信本人单聊文字及重启收发获人工确认，不能把 Web 验收当作设备端验收。详见分阶段报告。

[官方 AI 接口](https://docs.astrbot.app/dev/star/guides/ai.html)、[消息事件](https://docs.astrbot.app/dev/star/guides/listen-message-event.html)、[消息发送](https://docs.astrbot.app/dev/star/guides/send-message.html)。
