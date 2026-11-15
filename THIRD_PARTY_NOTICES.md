# 第三方资源

听幕源码仓库用于私有团队协作，目前没有为项目自身指定开源许可证。第三方代码、字体和模型仍遵循各自许可。

## 随源码提供的字体

以下字体来自 Adobe 的官方字体仓库，使用 SIL Open Font License 1.1；完整许可随字体保留。字体仅用于界面显示，不作为项目自身的许可声明。

| 字体 | 随附许可 |
| --- | --- |
| Source Han Sans SC（思源黑体） | [SourceHanSans-LICENSE.txt](src/tingmu/quick/qml/fonts/SourceHanSans-LICENSE.txt) |
| Source Han Serif SC（思源宋体） | [SourceHanSerif-LICENSE.txt](src/tingmu/quick/qml/fonts/SourceHanSerif-LICENSE.txt) |
| Source Sans 3 | [SourceSans-LICENSE.md](src/tingmu/quick/qml/fonts/SourceSans-LICENSE.md) |
| Source Code Pro | [SourceCodePro-LICENSE.md](src/tingmu/quick/qml/fonts/SourceCodePro-LICENSE.md) |

下载来源记录在 [SOURCES.txt](src/tingmu/quick/qml/fonts/SOURCES.txt)。

## 安装依赖与便携发行物

Python 直接依赖见 [pyproject.toml](pyproject.toml)，解析版本见 [uv.lock](uv.lock)。Qwen 的独立环境见 [scripts/qwen-requirements.lock.txt](scripts/qwen-requirements.lock.txt)。各软件包的许可证以安装包附带声明为准。

Git 仓库不提供 FFmpeg、Python/Qt 运行时、CUDA 库或语音模型权重。制作或对外分发便携版时，需要保留这些组件的许可及通知，并单独核对所用构建和模型的分发条件；源码仓库中的字体许可不能代替发行物审查。
