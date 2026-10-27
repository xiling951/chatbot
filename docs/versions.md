# 已固定的实际版本

- AstrBot v4.29.3，提交 `f61a7f42696da6df25dc7a249ec9b8d54ed9da18`，源码/虚拟环境 F:/chatbot-runtime/AstrBot，ASTRBOT_ROOT 为 C:/Users/Lenovo/AppData/Local/CompanionCore/runtime。源码未修改，插件部署在 C 盘根目录下的 data/plugins/astrbot_plugin_companion_core。
- Companion Core 0.3.2，SQLite schema 3；新增当前消息受控自动抽取（默认关闭）、待确认冲突、称呼/边界关系视图及来源分明的事件与计划。新增有限活动表述输出兜底，保留不限次数测试授权和跨进程计数。v0.3.0 标签 checkpoint-2026-10-26-v0.3.0 保留自动记忆检查点。v0.2.1 标签 checkpoint-2026-10-26-v0.2.1 为迁移前源码检查点。v0.2.0 检查点 checkpoint-2026-10-26-v0.2.0 和初始基线标签均保留。
- Python 3.12.15 / uv 0.12.24。实际安装 154 个包。上游标签未带 uv.lock；本机安装后生成运行目录 uv.lock，之后 `uv sync --frozen --no-dev --python 3.12`，不盲目重新解析。依赖导出见 config/runtime-requirements.lock。
- DeepSeek 请求别名 deepseek-flash，2026-10-26 官方文档映射 DeepSeek-V4.1-Flash-1017；当前桌面凭据真实调用通过，精确后端映射依据官方文档，非接口独立测得。
- DSH 已有源码提交 b150a551b8，只检查存在并读取获授权凭据，未安装/升级/调用 DSH。

升级前保留锁文件、受控配置与一致性数据库备份。不要让自动更新覆盖固定版本；升级后重新验证事件接口、单回复、记忆删除及恢复。

待部署源码 v0.4.0 / schema 4：新增持久化主动任务、执行/发件箱、独立授权和投递账本、默认关闭的唯一工作循环、文字提醒入口及按范围模拟风格。当前在线保持上面的 v0.3.2/schema 3，等待用户完成手机记忆测试后再受控升级。实际提醒时区由用户明确设为 Asia/Shanghai；不按语言猜测。
