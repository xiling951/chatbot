# 本机环境与运行入口

2026-10-26，Windows 原生单实例；项目 F:/chatbot，AstrBot 源码和虚拟环境 F:/chatbot-runtime/AstrBot，ASTRBOT_ROOT 为 C:/Users/Lenovo/AppData/Local/CompanionCore/runtime，私有数据位于其 data 子目录。F 盘是 exFAT，无法实施用户 ACL；已将本轮活动数据迁入 C 盘 NTFS，旧 data 路径已移除。未全盘扫描。保留现有 DSH，不接入执行器。

- Python 3.12.15（uv 已有解释器），uv 0.12.24；Node 24.13.0，未发现 Docker。磁盘检查时约 600 GiB 可用。
- 管理与 WebChat：[本机管理入口](http://127.0.0.1:6185)。实际监听 127.0.0.1，未认证聊天 API 返回 401，首页返回 200。
- 管理账号保存在 `C:/Users/Lenovo/AppData/Local/CompanionCore/runtime/data/admin.local.json`，不要提交或在聊天中粘贴密码。NTFS runtime 根目录已移除继承权限，实际 ACL 仅当前用户 Lenovo 和 SYSTEM 完全控制；不能只凭 icacls 返回成功判断 exFAT 已受限。
- 模型凭据经用户明确授权，从当前桌面版 `C:/Users/Lenovo/AppData/Roaming/dsh-desktop/harness/.credentials.yaml` 读取，仅写入运行目录 cmd_config.json，不修改 DSH。旧 ~/.dsh 保存无效历史密钥，不能作为当前配置。支持显式 --dsh-home。运行配置采用 provider_sources + provider_source_id 结构。
- 普通模型请求关闭思考，无工具/搜索；最长输入 4000 UTF-8 字节，记忆另限 1200、历史另限 4000 UTF-8 字节，输出上限 512 tokens，超时 35 秒；提供商 request_max_retries=1（总尝试一次），SDK 重试关闭。
- 最新授权：DeepSeek 项目测试不限次数，用户的额度上限指 Codex。使用 mode=unlimited_testing，不检查旧次数、每日预留或日期到期，累计尝试和用量继续记录。旧有限模式仅保留给显式选择的回退，不是当前限制。请求超时、有限重试、输入大小与 STOP 开关不变；实际费用按用量观察。
- 日志 WARNING，不打印聊天正文或密钥；成功模型请求记录独立用量及耗时。插件短期上下文与 WebChat 界面历史分别管理，见 data_lifecycle.md。

启动：`powershell -File F:/chatbot/scripts/start.ps1`。停止：`powershell -File F:/chatbot/scripts/stop.ps1`；停止会留下模型 STOP 文件，后续启动不会自动移除。开机自动启动尚未注册，避免未验收时自动联网。

部署插件：运行目录虚拟环境的 python 执行 `F:/chatbot/scripts/deploy_local.py`，然后重启。只有明确更新密钥时追加 `--reuse-dsh`；此命令本身不发模型请求。管理密码只有显式 `--reset-local-admin` 才重置。复用脚本不会增加模型额度。

当前授权配置命令：authorize_test.py --unlimited-testing，保留既有累计尝试，旧限制移入 superseded_limits；不需要反复追加批次。有限模式 --calls/--daily-cny 仅供以后明确选择，不可偷偷覆盖当前授权。插件和提供商探针共享加锁的请求计数，避免并发丢失或加密盘临时替换错误。

v0.2 可选择 --suite natural（3 次）、--suite privacy（1 次）、--suite retrieval --m01-indices 1（1 次），或 --suite persona --persona-start N --persona-cases K（2K 次，限开发题）。compare_thinking.py 必须先停止 AstrBot，独占同一配额后仅调用两次，不打印密钥或保存思维文本；结束后用 start.ps1 恢复服务。它不是工具或手机验收。

测试：在 F:/chatbot 下将 PYTHONPATH 设为 `F:/chatbot-runtime/AstrBot;F:/chatbot`、ASTRBOT_ROOT 设为上述 C 盘 runtime，再用 F 盘 AstrBot 的 `.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py" -v`。框架事件测试使用假模型和假发送，不产生费用。scripts/local_paths.py 统一维护代码/私有数据两条路径。

官方部署与接口：[源码部署](https://docs.astrbot.app/deploy/astrbot/cli.html)、[官方仓库固定版本](https://github.com/AstrBotDevs/AstrBot/tree/v4.29.3)。
