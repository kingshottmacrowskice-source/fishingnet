# 字幕依赖与适配说明

本项目安装并调用 bilibili-api-python 17.4.1，许可证为 GPL-3.0-or-later；其许可证不能被 DSH 包装层的 MIT 替代。上游：https://github.com/Nemo2011/bilibili-api 。发布或分发前应按 GPL 条件检查组合项目的许可证、对应源码和许可声明。

本项目没有复制 DSH 代码。适配器继承库的 Video.get_subtitle，重写 get_player_info，以项目现有传输读取 nav 和 player/wbi/v2，并调用库内置 _enc_wbi 与 OE 参数表。私有 API 依赖固定为17.4.1，升级需重新验证。库默认网络传输、WBI2、buvid、ticket、浏览器UA和额外重试不会被调用；不修改库的全局设置。

正常完整采集为四次请求：元数据、签名参数、字幕清单、字幕正文。重试仍消耗同一个四次总预算；预算不足时停止，不增加上限。搜索路径维持未签名。
