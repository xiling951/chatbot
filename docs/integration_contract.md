# Companion Core / AstrBot 4.29.3

实际核对官方标签源码 `astrbot/core/star/context.py`、`platform/astr_message_event.py`、`platform/sources/webchat`、`provider/sources/openai_source.py` 以及当前插件。

- 使用 `@filter.event_message_type(EventMessageType.ALL, priority=1000)`；处理时先 `should_call_llm(False)` 和 `stop_event()`，由插件独占 `context.llm_generate` 与 `event.send(event.plain_result(...))`。内置默认 LLM 另设 enable=false。真实 Web 管理命令确认只返回一次。
- 框架唤醒阶段会移除 `/`；命令读取 `event.message_obj.message_str` 的原始适配器文本，缺失时回退 event.get_message_str。已有回归。
- 模型 ID 从本机配置选择，非消息正文；人格只有一个 system_prompt 注入点。记忆写入通过 MemoryStore.put、apply_statement 和 confirm，共享同一事务校验及事实写入函数，只接受本人明确陈述。memory_intent.py 识别显式指令；extraction.py 仅在主体单独开启自动记忆后提取当前可信消息，未启用时普通聊天不写事实。
- 身份来自 get_platform_id、get_self_id、get_sender_id；机器人键同时包含适配器 ID，不能用昵称合并。私聊在同一用户跨 Web 会话共享事实，群聊按 group_id 隔离，persona 独立。手机号与完整 ID 不写普通日志。
- 事件键由平台、机器人、会话、消息 ID 哈希组成，SQLite 唯一约束先占位。崩溃或发送状态不明时不重发：保证至多一次尝试，不能声称平台端恰好一次或必达。缺失稳定消息 ID 会拒绝处理。
- 所有生成及删除串行，避免迟到回复继续使用删除前记忆。请求最多一次，不执行工具或后台模型任务。
- 不读取 AstrBot 显示历史。插件自己的 turns 保存最多 3 轮/24 小时的短期上下文，每次注入上限 4000 UTF-8 字节，通过 UserMessageSegment / AssistantMessageSegment 传入 contexts。与长期事实分开；更正、删除、停用清空该用户可见范围的所有短期轮次，旧备份按 ledger revision 失效。
- `/忘记` 调用 conversation_manager.delete_conversations_by_user_id；Web 当前会话显示记录通过 get_platform_message_history / delete_platform_message_history_by_id 清理。该接口删除的是当前显示会话，其他会话旧记录保留，且不参与回复。
- 手机适配器默认关闭；绑定后只按可信登录返回的 owner user_id 建立精确私聊白名单，群聊和其他用户无响应。未经验证的主动发送不启用。

离线：40 项通过（19 存储、14 框架事件、4 配额、3 中文意图），模型/发送为假实现。真实：Web 中文记忆 3/3、隐私修复 1/1、最新检索复测 1/1；原 M01 首轮 19/20 等证据保留。图片/视频/文件暂时文本降级，音频只处理微信实际返回的云端转写，未转写不调用模型。微信新版行为留待人工验证，不能把 Web 或假事件当作设备端通过。

[官方 AI 接口](https://docs.astrbot.app/dev/star/guides/ai.html)、[消息事件](https://docs.astrbot.app/dev/star/guides/listen-message-event.html)、[消息发送](https://docs.astrbot.app/dev/star/guides/send-message.html)。

调用授权现由 call_policy.py 共享加锁记录；明确 mode=unlimited_testing 不检查旧计数、日期或费用预留，仍检查单次上下文大小，STOP 与模型开关在调用前生效。v0.2.1 本地检查 47 项通过，真实完整记忆回归 20/20 与关联 5/5。

v0.3 当前整体本地检查 64 项通过；真实自动提取 20/20 问答及 7/7 关联流程。待确认冲突不进入短期历史，称呼/边界与相关事件和事实共用 1200 字节注入预算；未启用自动记忆的主体保持显式写入。来源与删除规则见 data_lifecycle.md。
