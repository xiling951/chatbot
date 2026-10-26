# 已固定的实际版本

- AstrBot v4.29.3，提交 `f61a7f42696da6df25dc7a249ec9b8d54ed9da18`，源码/虚拟环境 F:/chatbot-runtime/AstrBot，ASTRBOT_ROOT 为 C:/Users/Lenovo/AppData/Local/CompanionCore/runtime。源码未修改，插件部署在 C 盘根目录下的 data/plugins/astrbot_plugin_companion_core。
- Companion Core 0.1.0，源码 F:/chatbot/plugins/astrbot_plugin_companion_core；SQLite user_version=2，schema.sql 通过幂等建表加入短期 turns。只更新本轮创建的测试数据库；拒绝降级更高版本数据库。
- Python 3.12.15 / uv 0.12.24。实际安装 154 个包。上游标签未带 uv.lock；本机安装后生成运行目录 uv.lock，之后 `uv sync --frozen --no-dev --python 3.12`，不盲目重新解析。依赖导出见 config/runtime-requirements.lock。
- DeepSeek 请求别名 deepseek-flash，2026-10-26 官方文档映射 DeepSeek-V4.1-Flash-1017；当前桌面凭据真实调用通过，精确后端映射依据官方文档，非接口独立测得。
- DSH 已有源码提交 b150a551b8，只检查存在并读取获授权凭据，未安装/升级/调用 DSH。

升级前保留锁文件、受控配置与一致性数据库备份。不要让自动更新覆盖固定版本；升级后重新验证事件接口、单回复、记忆删除及恢复。
