# 一致性备份与隔离恢复

插件正式数据在 `C:/Users/Lenovo/AppData/Local/CompanionCore/runtime/data/plugin_data/companion_core`（NTFS 受限目录）。勿直接复制正在使用的 SQLite 单文件作为备份。

用运行目录虚拟环境 python 执行：

```powershell
python F:/chatbot/scripts/memory_admin.py backup C:/Users/Lenovo/AppData/Local/CompanionCore/runtime/data/plugin_data/companion_core/backups/memory-backup.sqlite
python F:/chatbot/scripts/memory_admin.py restore C:/Users/Lenovo/AppData/Local/CompanionCore/runtime/data/plugin_data/companion_core/backups/memory-backup.sqlite C:/Users/Lenovo/AppData/Local/CompanionCore/runtime/data/plugin_data/companion_core/backups/memory-quarantine-new.sqlite
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

先确认 F:/chatbot-baseline 不存在；不要用 reset --hard 清除尚未保存的修改。上例仅展示独立取源码：当前 v0.3 为 schema 3，v0.1/v0.2 会拒绝直接读取新版库，不能照例直接降级在线数据库。优先在 schema 3 上修复或选择兼容版本。

如确需回退 v0.2.1，选择 checkpoint-2026-10-26-v0.2.1 源码，用该版本恢复迁移前 backups/before-v0.3.sqlite 至新隔离库，并始终携带当前最新 deletions.sqlite。核对事实/范围/版本和删除回归后，再停止服务、保留完整新版私有库并激活隔离库。这样会丢失迁移后新写入内容，不能声称无损；不要回滚删除账本或不限次数授权。GitHub 仅备份源码，不保存这些私有数据。尚未执行旧代码在线激活或宣称降级演练通过。
