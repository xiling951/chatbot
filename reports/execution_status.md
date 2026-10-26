# 执行状态

更新日期：2026-10-26。基线：实施计划 v1.2。

## 当前状态

用户已纠正停止条件：指 Codex 自身额度，DeepSeek 项目测试不限次数，旧次数/5 元/日期闸门已撤销。Companion Core v0.3.2 已部署，schema 3；75 项本地检查通过。真实 Web 自动记忆 20 事实跨新会话问答 20/20，另 7 项冲突、时间变化、隔离、计划和删除问答均通过。完整项目仍在实施，不以 DeepSeek 批次数量自行停止。

AstrBot v4.29.3 代码/环境在 F:/chatbot-runtime/AstrBot，私有 ASTRBOT_ROOT 为 C:/Users/Lenovo/AppData/Local/CompanionCore/runtime（NTFS 仅 Lenovo/SYSTEM）。普通模型回复及免费记忆管理已恢复，保留超时、有限重试、停止开关与用量记录。用户休息期间手机人工验收继续暂挂。

## 已有交付

- [执行入口](../START_HERE.md)。
- [实施计划 v1.2](../AI虚拟伴侣_分阶段实施与验收计划_含DeepSeekHarness替代方案.md)。
- [独立审查意见](../AI虚拟伴侣_计划审查意见_2026-10-26.md)。
- [v1.1 历史归档](../docs/archive/AI虚拟伴侣_实施计划_v1.1_2026-10-26.md)，归档时已与修订前原文核对 SHA-256 一致。
- GitHub 初始可回退基线：[41f860f](https://github.com/xiling951/chatbot/commit/41f860f2537d59534212ddcebd5a3007bd21e80d)，标签 baseline-2026-10-26-weixin-memory；远程 main 和标签剥离提交均已核对一致。后续修改在 implementation/v1.2-memory-mvp 分支。

## 分阶段进度

- P0 环境与 Web 基线：通过。T01–T04、T06 有直接证据，T05 工具测试未做且非当前前置；见 P0.md。
- P1 手机渠道关卡：进行中，用户休息期间搁置人工手机测试。原有文字/重启/暗号流程证据有效，正式 30 条文字及 10 个媒体待测，原生语音气泡硬要求未满足。新增媒体诚实降级、微信云端转写路径有离线事件验证；tests/media_cases.json 不代表手机验收通过。
- P2 最小插件与人格：进行中。最新开发 20 场景 46 回复及冻结后的保留 10 场景 20 回复已采样并做 Agent 初评，非本人评分。总均分约 4.09，自然度维度约 3.87，P30 首轮程序停止入口说明失败，v0.3.2 已接入明确停止请求，真实隐私回归 2/2 与写入阻断检查通过；原保留题失败仍保留，自然度及全新保留题评估未通过，不准出。早期虚报保存/活动问题已修约束并加有限输出兜底，真实思考对照各 10 回复后保留关闭默认。见 P2.md。
- P3 单渠道记忆：进行中。schema 3 加法迁移已部署；受控自动提取默认关闭，可按主体开启，冲突等待确认，时间变化及明确更正可更新；新增称呼/边界关系视图、本人报告事件与共同计划。删除/停用/自动开关作废候选及旧票据，旧快照重放最新删除与开关。64 项整体本地检查通过，真实自动记忆 20/20+关联 7/7；迁移前真实库隔离恢复通过，未激活隔离库。规则覆盖不能代表任意中文语义，跨天与手机新版待验，未冻结 MVP。
- P4 主动行为：部分本地实现。时间规则及默认关闭策略已写入，6 项模拟时间测试覆盖 DST、停机合并、免打扰边界及一次性计划；无持久化发件队列或真实外发。见 P4.md。
- P5 媒体与 B 站：未开始。未安装相关工具或配置登录 Cookie。
- P6 第二平台与运行观察：未开始。无跨平台绑定或持续运行证据。

## 待核实的外部条件

已授权复用 DSH 当前官方 DeepSeek 凭据；实际路径 C:/Users/Lenovo/AppData/Roaming/dsh-desktop/harness，勿误用 ~/.dsh 的历史失效密钥。免费模型列表 HTTP 200 和本项目真实模型响应已确认，不将用户提供的余额报告当成本项目测得账单。

最新授权：DeepSeek 项目测试不限次数，不受旧日费用预留或日期到期约束；用户说的额度上限属于 Codex，本项目账本不能推算 Codex 剩余额度。旧 100 次尝试/99 成功和约 0.058212 元峰值估计为历史证据，不再作为停止依据。新授权记录 mode=unlimited_testing 并保留累计计数，原有限配置归入 superseded_limits；请求级边界与人工停止仍有效，不运行无意义付费循环。

本次真实验收使用临时 chat 权限 API key 和两个合成 Web 身份，不改本人微信事实；每次提问新建会话，避免答案由短期历史泄漏。两次执行均清理合成主体活跃记忆并删除临时 key。证据在私有插件目录 memory-evaluation-f49fd293e6364d598555bf25e498e71d.local.json、memory-evaluation-56101cc278f9403cae2e9b9d419aea5f.local.json。

v0.2 新增私有证据：natural 的 cb8b40f0fff74818858275fcfad53b86、persona 的 9babd6a41b424399adb3adc49abc76bb、privacy 的 8af5b50bae094d1aac76b8e3a3238bb6、retrieval 的 e90d4d54cb3446988072c5702305c811，均为 memory-evaluation-<ID>.local.json，活跃合成记忆和临时 key 清理均为 true；提供商对照为 thinking-comparison-efa46607b4344072b74201f730bb7ca3.local.json。对照脚本停止 AstrBot 后独占配额，结束后已重启服务，无手机投递验证。

v0.3 私有证据 memory-evaluation-cafd39ac88a44760a5027fa7738d95e6.local.json：27/27 自动内容检查、候选/开关/导出/删除操作检查通过，合成活跃数据及临时 key 清理均为 true。backups/before-v0.3.sqlite 保留迁移前一致性快照；v0.3-migration-quarantine.sqlite 为真实旧库隔离迁移验证，未用于在线对话。schema 3 不可直接用旧版代码启动，回退限制见 docs/backup_restore.md。

## 下一步

完整真实 Web 显式与自动记忆回归已通过。正在补 P2 修订人格的完整开发场景和独立保留题评估，随后继续生命周期及可独立实现的后续模块。手机正式文字/媒体/主动观察、原生语音冲突、平台资格及真实跨天保持待验证。保持 GitHub 检查点，不以 DeepSeek 配额自行结束目标。

本地启动：powershell -File F:/chatbot/scripts/start.ps1；停止：scripts/stop.ps1 会留下 STOP 模型开关。测试重现及数据路径见 docs/environment.md。源码 plugins/astrbot_plugin_companion_core，SQLite schema v3；普通短期上下文最多 3 轮/24 小时，删除、更正或停用会清理，旧备份按最新账本重放失效。

## 记录约定

每次新增进展记录日期、动作、文件或版本、检查方法与结果。真实接入、离线模拟、人工手机观察分别标注。未测试保持未测试，延期记录理由，不用预期结果代替实际结果。
