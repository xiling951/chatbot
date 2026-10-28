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

明文配置备份包含凭据，只留在受限运行目录；下述运行快照在加密后才写 F 盘，不写入项目 reports 或上传。框架 data_v4.db 属独立显示历史，不作为模型事实恢复源，也不能拿旧显示历史重新抽取已删除内容。

## 加密运行快照

scripts/local_backup.py backup 创建当前 Windows 用户范围的 DPAPI 加密快照，放 F:/chatbot-backups，源码、聊天正文和凭据不会因此上传。快照包含当前配置、账号绑定、授权、插件/人格/策略素材、任务所在 memory.sqlite、deletions.sqlite 及框架显示库。主库与账本在同一只读事务内同时锁定，用 SQLite backup API 分别生成内存快照，再加密；框架显示库另做一致性备份，不宣称所有独立数据库具有全局原子时间点。发布完整文件前实际解密校验清单及摘要，不在 exFAT 留明文临时包。

后台守护每 24 小时检查到期（在线条件下 RPO 目标），失败退避一小时；7 天仅淘汰自身命名的完整加密快照，账本永不参与轮转。2026-10-27 实际创建 28 文件/335394 字节的加密包，并在新隔离目录完成三库完整性检查、最新账本重放，删除残留和危险重复队列均为 0，隔离处理实测 0.063 秒；未激活、未覆盖在线配置。此时间仅为隔离校验，不是整机恢复 RTO。真实 24 小时自动轮换和损坏主机恢复尚未观察。

检查最近一份：运行目录 Python 执行 scripts/local_backup.py verify；也可 --snapshot 指定加密文件。只解密到内存，数据库在受限临时目录验证后移除，配置不写回在线环境。必须读取当前权威删除/撤销账本，旧包里的账本不能证明已包含之后的删除；现有账本丢失时，此工具拒绝把旧包激活为服务。当前工具没有自动激活功能。

采用 [Microsoft DPAPI 当前用户范围](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)；通常需要同一 Windows 账号和机器解密，不作为异机灾备或离线账号密钥备份。F 盘副本不等于已验证异地/独立硬盘容灾，重装系统前需另行安排可移植的受控备份。

## GitHub 源码回退

已验证基线提交 41f860f2537d59534212ddcebd5a3007bd21e80d，远程标签 baseline-2026-10-26-weixin-memory。main 保留基线，继续实现使用 implementation/v1.2-memory-mvp 分支。GitHub 不保存密钥、数据库、登录状态或私有验收正文，因此源码备份不能代替本机数据备份。

回退可建立独立 checkout，避免覆盖当前工作：

```powershell
git -c safe.directory=F:/chatbot -C F:/chatbot worktree add F:/chatbot-baseline baseline-2026-10-26-weixin-memory
powershell -File F:/chatbot/scripts/stop.ps1
F:/chatbot-runtime/AstrBot/.venv/Scripts/python.exe F:/chatbot-baseline/scripts/deploy_local.py
powershell -File F:/chatbot/scripts/start.ps1
```

先确认 F:/chatbot-baseline 不存在；不要用 reset --hard 清除尚未保存的修改。上例仅展示独立取源码：当前在线 schema 5，旧代码会拒绝直接读取新版库，不能照例直接降级在线数据库。独立 checkout 的部署脚本也必须读取当前冻结的实际私有目录，禁止让旧版脚本在逻辑 C 盘视图创建第二套数据。优先使用当前兼容版本修复。

如确需回退 v0.2.1，选择 checkpoint-2026-10-26-v0.2.1 源码，用该版本恢复迁移前 backups/before-v0.3.sqlite 至新隔离库，并始终携带当前最新 deletions.sqlite。核对事实/范围/版本和删除回归后，再停止服务、保留完整新版私有库并激活隔离库。这样会丢失迁移后新写入内容，不能声称无损；不要回滚删除账本或不限次数授权。GitHub 仅备份源码，不保存这些私有数据。尚未执行旧代码在线激活或宣称降级演练通过。

v0.4.1/schema 4 已部署，迁移前一致性快照 backups/before-v0.4-activation.sqlite 为 schema 3。memory_admin.py 的 backup 分支直接只读使用 SQLite backup API，不实例化新开发版 MemoryStore，因此备份不会提前迁移仍运行的旧版；迁移前曾实际复核在线 user_version 保持 3。

schema 4 恢复还会重放最新主动授权/投递账本；已接受/unknown 不重发，历史提交的额度和有限重试次数不回退。不能恢复旧账本来绕开撤销、次数或删除标记。停止脚本先置 STOP，并用不迁移数据库的停止助手持久化撤销，再终止已核对身份的进程；在途提交仍可能被平台接受。源码检查点 checkpoint-2026-10-26-v0.3.2 保留可回退版本，不能直接用它打开 schema 4；必须走相应的旧快照隔离恢复和兼容性检查。

v0.4.1 仍为 schema 4，但修正微信机器人身份键。当前已确认绑定的适配器历史 fallback 键更新为真实账号键，person/scope 不变，故事实和删除账本不回滚；修复前快照 backups/before-v0.4.1.sqlite 已建立。降到 v0.4.0 时除了 schema 兼容，还必须核对身份键格式，不能宣称只替换代码就无损恢复；使用修复前快照加当前账本隔离验证。账号修复助手要求服务离线、当前绑定/白名单/配置一致和匹配备份，不按昵称合并，也不允许已存在两个身份时自动合并。

## v0.5.0/schema 5兼容性

M07在独立权威账本新增短期绑定码、指定属性授权及撤销审计；主库仅提升兼容性标记，身份/事实/范围未重建。旧版v0.4.x不得直接打开schema 5，防止旧逻辑忽略共享上下文失效；优先用兼容新版修复。升级前backups/before-v0.5.sqlite为schema 4一致性快照，真实隔离4→5逐行核对身份/事实/范围未变；独立账本不能回滚。即使采用旧快照，也必须在隔离库重放当前删除、授权和修订，再做共享撤销与上下文回归；不能用旧账本复活绑定或一次性码。

2026-10-28T02:48 UTC升级前DPAPI快照32文件/342466字节，已真实解密、隔离三库完整性和最新账本重放通过，删除残留及危险重复队列0、未激活。仅是同账号本机恢复证据，不是整机/异机恢复或全局原子备份。源码v0.5.0检查点保留于现有实施分支；手机双端绑定没有执行，不把源码可回退等同数据无损降级。

v0.5.0源码检查点63bc4264e7f55be86d8f1c1cb16557f005ffc530与远程checkpoint-2026-10-28-v0.5.0已核对一致。v0.5.1保持schema 5，新增外部读取事件类型（现有events表）和权威账本每日读取尝试表，事件的删除/修订仍走同一生命周期；没有新个人事实或身份合并。升级前v0.5.0加密包33文件/379570字节已实际解密并隔离最新账本重放，删除残留/危险重复队列0、未激活。即使回退代码，不能回滚deletions.sqlite来重置每日读取尝试。

v0.6.0保持schema 5，在最新权威账本增加逐主体表情偏好/提交/冷却；旧主库恢复不重置发送尝试。备份现在限定纳入assets/emotes的32组SVG/PNG与manifest，排除缓存、任意文件和链接。2026-10-28T03:21 UTC实际100文件/807490字节DPAPI快照，解密/三库完整性/最新账本重放通过，删除残留/危险重复队列0，未激活；素材源代码与开发构建工具同时由GitHub源码保存。测试生成的Web图片副本按本次确切文件清理，不清理用途不明的框架附件。

运行优化版的启动入口经runtime_bootstrap.py加载仅QQ的本机代理兼容，再进入固定AstrBot。现密文快照额外限定收集11个必要运维脚本，含启动、停止、QQ会话兼容、守护与备份工具；不会递归备份工作区任意私有文件。2026-10-28T14:55 UTC实际111文件/830386字节，解密/隔离重放最新账本删除残留与危险重复队列0，未激活；这些新增脚本不改变数据schema 5。SDK兼容/网络恢复不应以回滚删除账本、发送预算或重新绑定来“修复”。
