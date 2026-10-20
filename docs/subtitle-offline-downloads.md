# 字幕依赖安装状态

2026-10-20：依赖已全部取得并完成离线安装，pip check 通过。无需继续手工下载。

官方 wheel 已按 PyPI SHA-256 校验；qrcode-terminal 0.8 仅提供源码包，已核对官方摘要及 setup.py，使用本地 setuptools/wheel 构建并安装。缓存位于 `.runtime/subtitle-wheels/`，实际版本见 requirements.lock。
