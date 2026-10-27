# 一致性备份与隔离恢复

插件正式数据在 config/runtime_root.local.json 指向的实际 NTFS 受限目录的 data/plugin_data/companion_core 下。原 C 盘 AppData 逻辑路径受 Codex 文件重定向影响，普通登录任务不可硬编码该路径。勿直接复制正在使用的 SQLite 单文件作为备份。

用运行目录虚拟环境 python 执行：

```powershell
$taskPrivate = (Get-Content F:/chatbot/config/runtime_root.local.json -Raw | ConvertFrom-Json).runtime_root
$taskBackups = Join-Path $taskPrivate 'data/plugin_data/companion_core/backups'
F:/chatbot-runtime/AstrBot/.venv/Scripts/python.exe F:/chatbot/scripts/memory_admin.py backup (Join-Path $taskBackups 'memory-backup.sqlite')
F:/chatbot-runtime/AstrBot/.venv/Scripts/python.exe F:/chatbot/scripts/memory_admin.py restore (Join-Path $taskBackups 'memory-backup.sqlite') (Join-Path $taskBackups 'memory-quarantine-new.sqlite')
```

backup 使用 SQLite backup API，要求目标不存在，避免覆盖已有快照。restore 必须选择不存在的隔离目标，携带当前独立 deletions.sqlite；清除已删事实/事件/候选及旧代次 turns，重放停用和自动记忆策略，然后检查 integrity_check 和删除残留。脚本不切换在线服务。schema 2 的 before-v0.3.sqlite 已用当前 schema 3 代码实际隔离迁移，完整性与删除重放通过，未激活为服务库。

激活前停止实例，验证隔离库的身份/范围/事实/版本及删除回归，保留当前配置与回退快照，再用已验证的隔离库替换正式库。继续使用最新账本，不能退回旧账本；无法证明账本最新时保持离线。数据库内保存 ledger UUID，缺失或不同来源的账本会拒绝启动，但 UUID 本身不能证明账本足够新。

恢复删除前旧备份时允许保守丢弃已更正的旧条目；不会从旧备份伪造恢复备份后才写入的新值。恢复当前备份可以保持 ID、来源、版本、范围一致。两种情形已用临时库验证。

配置备份包含凭据，只留在受限运行目录；不要写入项目 reports 或上传。框架 data_v4.db 属独立显示历史，不作为模型事实恢复源，也不能拿旧显示历史重新抽取已删除内容。

## GitHub 源码回退

已验证基线提交 41f860f2537d59534212ddcebd5a3007bd21e80d，远程标签 baseline-2026-10-26-weixin-memory。main 保留基线，继续实现使用 implementation/v1.2-memory-mvp 分支。GitHub 不保存密钥、数据库、登录状态或私有验收正文，因此源码备份不能代替本机数据备份。

回退可建立独立 checkout，避免覆盖当前工作：

```powershell
git -c safe.directory=F:/chatbot -C F:/chatbot worktree add F:/chatbot-baseline baseline-2026-10-26-weixin-memory
powershell -File F:/chatbot/scripts/stop.ps1
F:/chatbot-runtime/AstrBot/.venv/Scripts/python.exe F:/chatbot-baseline/scripts/deploy_local.py
powershell -File F:/chatbot/scripts/start.ps1
```

先确认 F:/chatbot-baseline 不存在；不要用 reset --hard 清除尚未保存的修改。上例仅展示独立取源码：当前在线 schema 4，旧代码会拒绝直接读取新版库，不能照例直接降级在线数据库。独立 checkout 的部署脚本也必须读取当前冻结的实际私有目录，禁止让旧版脚本在逻辑 C 盘视图创建第二套数据。优先使用当前兼容版本修复。

如确需回退 v0.2.1，选择 checkpoint-2026-10-26-v0.2.1 源码，用该版本恢复迁移前 backups/before-v0.3.sqlite 至新隔离库，并始终携带当前最新 deletions.sqlite。核对事实/范围/版本和删除回归后，再停止服务、保留完整新版私有库并激活隔离库。这样会丢失迁移后新写入内容，不能声称无损；不要回滚删除账本或不限次数授权。GitHub 仅备份源码，不保存这些私有数据。尚未执行旧代码在线激活或宣称降级演练通过。

v0.4.1/schema 4 已部署，迁移前一致性快照 backups/before-v0.4-activation.sqlite 为 schema 3。memory_admin.py 的 backup 分支直接只读使用 SQLite backup API，不实例化新开发版 MemoryStore，因此备份不会提前迁移仍运行的旧版；迁移前曾实际复核在线 user_version 保持 3。

schema 4 恢复还会重放最新主动授权/投递账本；已接受/unknown 不重发，历史提交的额度和有限重试次数不回退。不能恢复旧账本来绕开撤销、次数或删除标记。停止脚本先置 STOP，并用不迁移数据库的停止助手持久化撤销，再终止已核对身份的进程；在途提交仍可能被平台接受。源码检查点 checkpoint-2026-10-26-v0.3.2 保留可回退版本，不能直接用它打开 schema 4；必须走相应的旧快照隔离恢复和兼容性检查。

v0.4.1 仍为 schema 4，但修正微信机器人身份键。当前已确认绑定的适配器历史 fallback 键更新为真实账号键，person/scope 不变，故事实和删除账本不回滚；修复前快照 backups/before-v0.4.1.sqlite 已建立。降到 v0.4.0 时除了 schema 兼容，还必须核对身份键格式，不能宣称只替换代码就无损恢复；使用修复前快照加当前账本隔离验证。账号修复助手要求服务离线、当前绑定/白名单/配置一致和匹配备份，不按昵称合并，也不允许已存在两个身份时自动合并。
