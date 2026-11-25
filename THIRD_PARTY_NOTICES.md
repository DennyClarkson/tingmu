# 第三方资源

听幕源码仓库用于私有团队协作，目前没有为项目自身指定开源许可证。第三方代码、字体和模型仍遵循各自许可。

在 Windows x64 便携包中，Unicode 数据许可位于 `app/tingmu/Unicode-LICENSE.txt`，字体许可及来源记录位于 `app/tingmu/quick/qml/fonts/`。Python 许可位于 `runtime/python/LICENSE.txt`。已安装 Python 软件包的元数据及随附许可可在 `runtime/python/Lib/site-packages/` 中查阅。

## 语言名称

工作区语言名称取自 Unicode CLDR 47.0.0 的简体中文和英文语言名数据。数据用于界面显示和离线识别语言名称，工作区还接受自定义语言代码。[Unicode CLDR 数据](https://github.com/unicode-org/cldr-json/tree/47.0.0/cldr-json/cldr-localenames-full/main) · [Unicode 数据许可](https://github.com/DennyClarkson/tingmu/blob/main/src/tingmu/Unicode-LICENSE.txt)

## 随源码提供的字体

以下字体来自 Adobe 的官方字体仓库，使用 SIL Open Font License 1.1；完整许可随字体保留。字体仅用于界面显示，不作为项目自身的许可声明。

| 字体 | 随附许可 |
| --- | --- |
| Source Han Sans SC（思源黑体） | [SourceHanSans-LICENSE.txt](https://github.com/DennyClarkson/tingmu/blob/main/src/tingmu/quick/qml/fonts/SourceHanSans-LICENSE.txt) |
| Source Han Serif SC（思源宋体） | [SourceHanSerif-LICENSE.txt](https://github.com/DennyClarkson/tingmu/blob/main/src/tingmu/quick/qml/fonts/SourceHanSerif-LICENSE.txt) |
| Source Sans 3 | [SourceSans-LICENSE.md](https://github.com/DennyClarkson/tingmu/blob/main/src/tingmu/quick/qml/fonts/SourceSans-LICENSE.md) |
| Source Code Pro | [SourceCodePro-LICENSE.md](https://github.com/DennyClarkson/tingmu/blob/main/src/tingmu/quick/qml/fonts/SourceCodePro-LICENSE.md) |

下载来源记录在 [SOURCES.txt](https://github.com/DennyClarkson/tingmu/blob/main/src/tingmu/quick/qml/fonts/SOURCES.txt)。

## 安装依赖与便携发行物

Python 直接依赖见 [pyproject.toml](https://github.com/DennyClarkson/tingmu/blob/main/pyproject.toml)，解析版本见 [uv.lock](https://github.com/DennyClarkson/tingmu/blob/main/uv.lock)。Qwen 的独立环境见 [scripts/qwen-requirements.lock.txt](https://github.com/DennyClarkson/tingmu/blob/main/scripts/qwen-requirements.lock.txt)。各软件包的许可证以安装包附带声明为准。

便携版包含 CPython、PySide6、Qt 及其多媒体插件所用的 FFmpeg 动态库。独立的 FFmpeg/FFprobe 工具、Whisper 引擎、CUDA 库、Qwen 环境和模型权重按需复用本机安装或另行下载。再分发时须保留对应组件的许可及通知，并核对所选构建和模型的分发条件。

v0.6 的语音前处理可使用 Spleeter 和 pyannote segmentation 3.0 的 ONNX 模型。安装器验证归档的 SHA-256，许可与模型一起保存在应用缓存的 `speech-models/spleeter/LICENSE` 和 `speech-models/pyannote/LICENSE`。模型来源及实测见[本地语音处理说明](https://github.com/DennyClarkson/tingmu/blob/main/docs/local-speech.md)。
