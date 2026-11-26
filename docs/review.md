# 全片文本复核与短句显示

工作区开启原文复核后，听幕使用分配给复核任务的文本模型处理原声转录，随后再分词并生成讲解。字幕 Agent 若采纳学习语言/方言原文，会沿用字幕原句。若仅能采纳译文，会把和转录时间重叠的译文作为上下文。文本复核无法核实实际发音，可回到原声确认。[模型与权限](providers.md) · [字幕来源判断](portable.md)

`review(cues, language, progress=None, cancelled=None, checkpoint=None, context_cues=None)`
返回原顺序的源 `Cue`。ID、声学 start/end、original_text 不变；学习正文使用工作区的学习语言/方言，粤语规范为繁体；
旧 translation、explanation、tokens 清空，让后续教师针对复核正文重新生成材料。
`Cue.segmented_texts: list[str]` 单独保存模型决定的短句边界，拼接必须逐字等于复核 text，
包括标点和空格。绝不把相邻源句合并，也不把某个源 ID 的文字搬到另一个 ID。

模型建议的每行最多 42 文本列（中日韩全角字符和全角标点各算 2，拉丁字符和空格各算 1，组合符号
算 0）。这约等于 21 个汉字。已经符合上限的源句保持一个分句。
长句按自然句法分割，并限制分句总数，拒绝用一串单字满足长度约束。客户端校验原始拼接、
繁体规范后拼接、非空、行宽和分句数量。校验失败按句记录，已通过的句子写入检查点，仅修复缺失或无效的行。
过度纠错被保守保留时，对保留正文用明确标记的本地标点/长度算法分句，不采用失配的模型边界。

默认每批 40 句，文本超过 2,500 字时更早分批。视频准备使用工作区的并发设置，默认同时 4 批，可选 1–16 批。初次复核提供全文原始转写及前后各 6 句。上下文超过 60,000 字符时使用标明省略的均匀原文摘录，并保留局部上下文。字段校验失败时先保存编号匹配且已通过的行，只为出错行附上字段要求与前后各两句上下文。
不会用模型虚构词汇表，也不额外调用收费摘要模型。上下文不是声音证据。

严格 JSON 字段为 `id/text/uncertain/reason/segmented_texts`。客户端检查字段、类型、数量、
顺序、重复键、ID 和长度。大幅改写及粤语口语词/语气词数量变化逐句拦截并保留原文。
该启发式不能识别所有同音错字或保证语义准确；疑似错词仍需听音核对。

`review_metadata` 按源 ID 保存 `state/reason/changed/model_uncertain`，state 为
`reviewed_unchanged/corrected/uncertain/retained`；另有 `segmentation`（model 或
 deterministic_fallback）、`segment_count`、`timing_basis`、`short_display_estimate`。
原有 uncertain 永不清除，实际改动也保留不确定性。原因用已知语言/方言，zh-CN 用简体中文。
不使用“声音已验证”状态。

`SubtitleTimeline` 保持源句分离。未分割的显示 Cue 仍用源 ID；分割后 ID 为
`源ID:part:序号`（从 1 开始），`source_ids(display_id)` 返回唯一源 ID，用于查原稿和保存关联。
`StudyDocument.cues` 始终保存源数据；显示 Cue 由 timeline 派生，不写回替代原稿。
`DisplaySegment.timing_basis` 为 source 或 text_proportional。后者将原始时间区间按文本列宽
比例分配，保持源区间首尾，明确是阅读估计，不是模型听音或新的声学对齐。
若任一分句按毫秒取整后的展示时间不足 600 毫秒，整句作为一条字幕显示，可超过 42 列。
复核记录以 `kept_whole_for_duration` 标出此次决定，`short_display_estimate` 标记原建议时长不足，
`timing_guard.proposed_end_offsets` 保存原建议的字符边界。播放和导出旧文档时同样检查分段时长，
已核验译稿随之一并显示整句译文。
进入复核时保留每条源 ID，邻句只提供只读语境。对一份 950 条的已保存转录做离线重放时，
265 条声学时长不足 600 毫秒，其中 143 条有句末标点，62 条前后 500 毫秒内均无邻句；
这些短句都缺少逐句说话人标签。仅凭短时长跨过停顿拼句容易把独立回答归给上一位说话人。
教师翻译/讲解仍属整个源句；显示分句保留相关 token，切断的 token 不沿用整个词的读音和释义。

短空白衔接默认 500 毫秒，可设为 0–1500 毫秒。衔接时前句显示至后句开头，文字和 ID 各自独立。
间隔较大且不超过 6 秒时，未达最短展示时间的字幕可向后借用空白，最多延至下一句前 100 毫秒。
例如 26.24–26.40 秒的字幕，如果下一句在 27.84 秒开始，默认 1200 毫秒最短展示时间会使它显示到
27.44 秒。字幕设置中可调整最短展示时间，设为 0 即关闭借时。重叠区间按并集判断空白，标牌结束后恢复底层对白。
超过 6 秒的停顿、无后句可界定的片尾以及零时长标记都保留原时间。字幕和伴侣直接根据播放位置查询；
源文和译文的 SRT 用相同的显示时间，学习文档与重听位置保留声学时间。

`response_metadata` 记录请求模型、服务返回模型、usage、延迟、校验结果及逐句复核记录；
`batch_metadata/completed_cues/checkpoint_metadata` 沿用共享调度器。`context_metadata` 不含正文。
检查点按原顺序返回累计完成句子，回调前同步复核记录。取消/失败停止新批次并保存已成功批次。

流水线把检查点、复核配置及 [复核版本 `REVIEW_VERSION`](../src/tingmu/review.py) 落盘。
版本/配置变化时从原始显示文本重新复核；教师配置变化从复核正文继续。关闭云复核后回到
原始显示文本并清除旧复核分句，不把不匹配的边界沿用到原稿。
教师 `preserve_text=True`，必须逐字返回复核正文，邻句也使用复核文字；分词修复不能绕过检查。

离线验证：`.venv/Scripts/python.exe -m pytest tests/test_timeline.py tests/test_review.py tests/test_pipeline_review.py`。
付费小样本：`.venv/Scripts/python.exe scripts/evaluate_review.py --live --models gpt-6-astra --input reports/private/sample-qwen-full.json`。
测试只发送文字，机械校验和模型元数据不代表人工字幕准确率。
