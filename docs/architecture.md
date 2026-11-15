# 架构与扩展点

## 桌面与选择

Python 后端与 Qt Quick/QML 界面分离。QML 使用 Qt Quick Controls Basic 和本地图标，自定义平面控件；不运行网页服务，不加载远程前端资源。相较浏览器播放器，现有 QtMultimedia/FFmpeg 能直接复用已验证的媒体格式、音轨和本地 GPU 渲染。官方参考：[VideoOutput](https://doc.qt.io/qt-6/qml-qtmultimedia-videooutput.html)、[Qt Quick 部署](https://doc.qt.io/qt-6/qtquick-deployment.html)。

`quick/main.py` 创建 QApplication、Bridge、QQmlApplicationEngine。`ui.run_app` 默认指向这个入口；旧 `ui/app.py` 仅作历史回归参考。`quick/qml/Main.qml` 管理观看/准备视图、模块位置和浮动窗口，每个模块内部管理其设置。Qt 原生文件选择器仍用于多选文件和目录。

`quick/bridge.py` 在主线程持有唯一 QMediaPlayer 和 QAudioOutput，分别发布播放时钟、当前句、字幕列表、媒体、队列、聊天、笔记等属性。QML 通过限定 command/setting 接口调用，不执行任意 Python。昂贵识别和云请求在后台线程，结果经 Qt 信号回主线程；独立忙碌状态避免聊天/队列相互覆盖。聊天与复盘串行写会话，异步返回绑定原始视频/语言。

视频输出使用 QML VideoOutput.videoSink。全屏与浮动只改变视觉父节点；暂停时将最后一帧转成CPU图像重新上传到新场景，避免跨GPU场景纹理失效黑屏。全屏浮层固定尺寸、内部滚动，标题拖动、右下角缩放，尺寸不依赖字幕长度。关闭时通知所有任务取消并给本地子进程清理最多3.5秒；阻塞中的云请求不无限阻止退出。

## 时间轴与处理

`timeline.py` **禁止合并相邻源句**。只把实际空白间隔不超过gap_fill_ms（默认500、范围0–1500ms）的前句保持到后句开始。处理重叠事件时看覆盖区间，不把一个短标牌延长到遮盖长期对话。任意定位直接查询，没有延迟队列，伴侣与字幕共享当前句。

`Cue.segmented_texts` 保存强模型复核的短行边界，连接后必须完全等于正文，每条最多42显示列。源StudyDocument.cues的ID、原文、原声学时间保持不变。只在显示时间轴派生 source:part:N，按文字长度分配源区间并标记 timing_basis=text_proportional；不是强制对齐结果。原始短片段仍可能快速切换，不做虚假最短时长保证。

`pipeline.py` 本地转写→Astra全文复核→语言讲解→就绪。先保存原文，再保存独立review_spec/checkpoint/reviewed_text/reviewed_segmented_texts。教师 preserve_text=True，不二次改写复核正文。切换云端开关时旧复核缓存保留，恢复时同时恢复正文与短行边界。取消和失败保留已完成批次；片内ASR尚无断点。

`batch.py` 持久保存逐视频设置快照、状态和进度，顺序处理视频，当前视频的文本批次可并行。重启恢复running为paused，失败重试不丢后续项。`library.py` 保存多目录媒体索引，不复制视频、不向视频旁写学习文件。

## 存储与隐私

`storage.py` 每次操作独立SQLite连接/事务保存单词、笔记、对话及进度；JSON文档使用临时文件+原子替换。学习文档按规范化绝对媒体路径和语言方向/程度生成键，文件大小和修改时间避免使用过期资料。

便携数据在EXE旁data/，开发默认在项目.data/；缓存在各自.cache/，包括QML和shader缓存。所有材料与视频路径对应。`portable_paths.py` 在整体移动便携目录后迁移内部媒体的文档、数据库键、队列、媒体索引和草稿路径，外部视频不变；保留迁移备份和冲突保护。用户文本绝不随路径替换。

`media.py` 使用独立子进程运行本地ASR、FFmpeg与模型，失败不杀死UI。Qwen3-ASR+ForcedAligner保存原始字词审计；Whisper为另一路本地后端。`language.py` 用OpenCC规范粤语繁体显示，不声称修复实际漏字或语气词。

`review.py` 与 `agent.py` 只处理授权文本。`companion.py` 默认Astra/可选Sol，对话模型和批量教师分开；提供当前句教学和准确的软件导航，没有操作工具。UI把字幕、模型回答和用户文字按纯文本渲染。密钥只从环境变量/Windows注册表读取，不进配置、日志、Git或便携包。

## 限制

文本模型没有听音；拆分时间是估算，拆分句的讲解仍来源于完整源句。ASS/SSA仅提取文字，无复杂特效；图片字幕尚无OCR。下一步可做人工字幕编辑、声学时间复核、间隔复习和跟读反馈。
