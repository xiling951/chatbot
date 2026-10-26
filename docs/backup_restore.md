# 一致性备份与隔离恢复

插件正式数据在 `C:/Users/Lenovo/AppData/Local/CompanionCore/runtime/data/plugin_data/companion_core`（NTFS 受限目录）。勿直接复制正在使用的 SQLite 单文件作为备份。

用运行目录虚拟环境 python 执行：

```powershell
python F:/chatbot/scripts/memory_admin.py backup C:/Users/Lenovo/AppData/Local/CompanionCore/runtime/data/plugin_data/companion_core/backups/memory-backup.sqlite
python F:/chatbot/scripts/memory_admin.py restore C:/Users/Lenovo/AppData/Local/CompanionCore/runtime/data/plugin_data/companion_core/backups/memory-backup.sqlite C:/Users/Lenovo/AppData/Local/CompanionCore/runtime/data/plugin_data/companion_core/backups/memory-quarantine-new.sqlite
```

backup 使用 SQLite backup API。restore 必须选择不存在的隔离目标，携带当前独立 deletions.sqlite；按删除 ID 清除旧事实、清除旧代次 turns 并重放停用策略，然后检查 integrity_check 和删除残留。脚本不切换在线服务。backups 目录本轮已创建并有一次真实运行库的合成测试恢复证据。

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

先确认 F:/chatbot-baseline 不存在；不要用 reset --hard 清除尚未保存的修改。当前 v0.1/v0.2 均为 schema 2，代码回退不需要恢复旧数据库，必须保持最新删除账本及调用配额。未来 schema 改变后先核对兼容性。v0.2 部署前另有受限的 backups/before-v0.2.sqlite，本轮没有把它激活为服务库。
