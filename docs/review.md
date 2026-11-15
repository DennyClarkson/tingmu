# 全片文本复核与短句显示

`TranscriptReviewer(provider='openai', model='gpt-6-astra')` 默认使用 Astra，允许切换
`gpt-6.1-sol`。这是 ASR 后、分词读音和教师讲解前的独立步骤；音视频不离开本机。
模型只看到转写文本，不能核实实际发音。复核成功不代表人工听写验收。

`review(cues, language, progress=None, cancelled=None, checkpoint=None, context_cues=None)`
返回原顺序的源 `Cue`。ID、声学 start/end、original_text 不变；学习正文使用繁体粤语；
旧 translation、explanation、tokens 清空，让后续教师针对复核正文重新生成材料。
`Cue.segmented_texts: list[str]` 单独保存模型决定的短句边界，拼接必须逐字等于复核 text，
包括标点和空格。绝不把相邻源句合并，也不把某个源 ID 的文字搬到另一个 ID。

每行最多 42 文本列（中日韩全角字符和全角标点各算 2，拉丁字符和空格各算 1，组合符号
算 0）；这约等于 21 个汉字，不是声学或像素测量。已经符合上限的源句保持一个分句。
长句按自然句法分割，并限制分句总数，拒绝用一串单字满足长度约束。客户端校验原始拼接、
繁体规范后拼接、非空、行宽和分句数量。无法验证的响应重试一次，仍无效则拒绝整批。
过度纠错被保守保留时，对保留正文用明确标记的本地标点/长度算法分句，不采用失配的模型边界。

默认每批 40 句，文本超过 2,500 字时更早分批，并发最多 2。每批都提供全文原始转写及
前后各 6 句。上下文超过 60,000 字符时使用标明省略的均匀原文摘录，并保留局部上下文。
不会用模型虚构词汇表，也不额外调用收费摘要模型。上下文不是声音证据。

严格 JSON 字段为 `id/text/uncertain/reason/segmented_texts`。客户端检查字段、类型、数量、
顺序、重复键、ID 和长度。大幅改写及粤语口语词/语气词数量变化逐句拦截并保留原文。
该启发式不能识别所有同音错字或保证语义准确；疑似错词仍需听音核对。

`review_metadata` 按源 ID 保存 `state/reason/changed/model_uncertain`，state 为
`reviewed_unchanged/corrected/uncertain/retained`；另有 `segmentation`（model 或
 deterministic_fallback）、`segment_count`、`timing_basis`、`short_display_estimate`。
原有 uncertain 永不清除，实际改动也保留不确定性。原因用已知语言，zh-CN 用简体中文。
不使用“声音已验证”状态。

`SubtitleTimeline` 保持源句分离。未分割的显示 Cue 仍用源 ID；分割后 ID 为
`源ID:part:序号`（从 1 开始），`source_ids(display_id)` 返回唯一源 ID，用于查原稿和保存关联。
`StudyDocument.cues` 始终保存源数据；显示 Cue 由 timeline 派生，不写回替代原稿。
`DisplaySegment.timing_basis` 为 source 或 text_proportional。后者将原始时间区间按文本列宽
比例分配，保持源区间首尾，明确是阅读估计，不是模型听音或新的声学对齐。
若任何分句估计不足 350ms，复核记录标记 short_display_estimate；不能为了好看编造较长
声学区间。需要消除这类极短原始区间时应重新对齐声音。
教师翻译/讲解仍属整个源句；显示分句保留相关 token，切断的 token 不沿用整个词的读音和释义。

只有真正的短空白才填平：默认 500ms，可设 0–1500ms。A 的显示结束延至 B 开始，文字和
ID 仍分开，B 到点立即出现。重叠字幕按区间并集判断空白，短暂标牌结束后恢复底层对白。
末尾及长停顿不延长，零时长源句不制造时长。旧 min_duration_ms、companion_hold_ms 参数
只为读取旧配置保留，不再延长、合并或延迟字幕；字幕和伴侣都按当前播放位置直接定位，
倒退定位不会积压旧句。旧文档无 segmented_texts 时显示源句，重新准备会触发新版复核。

`response_metadata` 记录请求模型、服务返回模型、usage、延迟、校验结果及逐句复核记录；
`batch_metadata/completed_cues/checkpoint_metadata` 沿用共享调度器。`context_metadata` 不含正文。
检查点按原顺序返回累计完成句子，回调前同步复核记录。取消/失败停止新批次并保存已成功批次。

流水线把检查点、复核配置及 `REVIEW_VERSION='text-review-v2-short-lines'` 落盘。
版本/配置变化时从原始显示文本重新复核；教师配置变化从复核正文继续。关闭云复核后回到
原始显示文本并清除旧复核分句，不把不匹配的边界沿用到原稿。
教师 `preserve_text=True`，必须逐字返回复核正文，邻句也使用复核文字；分词修复不能绕过检查。

离线验证：`.venv/Scripts/python.exe -m pytest tests/test_timeline.py tests/test_review.py tests/test_pipeline_review.py`。
付费小样本：`.venv/Scripts/python.exe scripts/evaluate_review.py --live --models gpt-6-astra --input reports/private/sample-qwen-full.json`。
测试只发送文字，机械校验和模型元数据不代表人工字幕准确率。
