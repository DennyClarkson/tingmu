# 架构与状态管理重构：Phase 1 / Phase 2 审阅报告

日期：2026-11-23。审阅基线为 `41c61b7`，工作分支为 `refactor/state-management-phase1-2`。本轮仅实施 Phase 1 和 Phase 2，之后停止等待审阅。Phase 3、Phase 4 未开展。

## 分阶段改造与依赖方向

| 项目 | 文件（相对仓库根） | 结果、取舍及依赖方向 |
| --- | --- | --- |
| P1.1 全局角色与工作区偏好 | `models.py`、`global_models.py`、`storage.py`（均在 `src/tingmu/`），`batch.py`、`quick/bridge.py`、`quick/features.py`、`quick/workspace_controller.py` | `AppSettings._loaded_roles` 从 InitVar 改为独立复制的运行时字段，replace 保留基线。`to_dict()` 排除运行时字段，所有正式设置传输与队列持久化出口使用它。工作区 JSON 不保存角色。角色写入经 `assign/update_roles → _save_roles`；普通设置保存只在旧式角色字段确实被修改时调用显式字段增量 API，无角色修改时刷新当前角色，不写全局分配。`Store → 缓存 ModelRegistry → 文件签名刷新/跨进程写锁`，不再反复构造并扫描迁移。 |
| P1.2 草稿独立持久化 | `src/tingmu/drafts.py`、`portable_paths.py`、`quick/bridge.py`、`quick/qml/{Main,LearningPanel,CompanionPanel}.qml` | `QML → Bridge 草稿槽 → DraftStorage → study.db`，替代 `QML → quick-layout.json`。新增 `ui_drafts` 与 `ui_draft_migrations` 表，事务迁移两类旧草稿、保留完整迁移快照，成功后才去除旧布局项；一次标记防止崩溃重导覆盖后续编辑。便携迁移在原事务内重写句备注 key 的内部媒体前缀，不改文本、修订号或外部路径。布局只保留轻量值，按 UTF-8 字节检查并显式报错；`savedLayout` 保持只读绑定，编辑缓冲单独管理。 |
| P1.3 线程快照 | `src/tingmu/quick/{features,workspace_controller}.py` | `聊天 worker → 排队请求 → UI 线程复制设置 → worker 自有快照`。消息、历史、工具事件、持久化和返回值不共享可变引用；工具执行器显式使用冻结 workspace ID。模型发现权限、笔记整理 ID 同样在 UI 线程冻结。取消、超时和关闭后迟到信号不再更改界面状态。 |
| P1.4 单一弹窗宿主 | `src/tingmu/quick/qml/Main.qml`、`ChatPanel.qml`、`QueuePanel.qml`、`LearningPanel.qml`、`CompanionPanel.qml`、`CompanionContent.qml`、`SubtitlePanel.qml`，`quick/bridge.py` | 子面板 `requestDialog → Bridge UI 信号 → Main 单一宿主`。ModelManager、TeachingPreferences、SubtitleCorrection 各只有一份。按来源窗口选择 popup overlay 或 transientParent，保留 objectName 与普通、浮动、全屏交互。此 UI 路由不加入 LLM 工具定义。 |
| P2.1 状态及错误分域 | `src/tingmu/quick/state.py`、`bridge.py`、`features.py`、`workspace_controller.py` 与对应 QML | `Bridge 更新 → DomainStates → 各领域独立 notify → 对应 QML`。媒体、队列、聊天、下载、学习、模型、工作区、应用信息及底栏通知分开；局部错误只写所属域，同步成功清理自身错误，不清其他面板。保留旧 `state/stateChanged` 投影供兼容，正式 QML 不再绑定其广播。教学偏好验证失败保留弹窗与完整输入。 |
| P2.2 增量列表与 Markdown 缓存 | `src/tingmu/quick/list_models.py`、`features.py`、`bridge.py`、`qml/{ChatPanel,QueuePanel}.qml` | `Bridge 消息/任务 → StableListModel → QML ListView`，按 ID 发 insert/remove/move/dataChanged，同 ID 更新不 reset。Markdown 仅重解析正文变化的消息；每个当前 ID 只保留最新正文版本，并清理已移除 ID。旧无 ID 消息用内部位置身份兼容，公开数据不被改写。 |
| P2.3 事件与媒体库 IO | `src/tingmu/batch.py`、`storage.py`、`quick/{bridge,workspace_controller,library_summary}.py` | 队列提供锁外订阅，变更通知驱动单次合并刷新，取消空闲 400ms 轮询；工作区切换和语言刷新由任务完成/确认状态事件驱动，取消 200ms 轮询。进度计时器仅播放时运行，诊断计时器仅开发者模式运行。收件箱复用单线程池。媒体库冷读取、文档解析/哈希与批量位置 SQL 均在独立 worker；UI 先显示已有缓存/占位，使用请求代次拒绝过期结果。缓存以文档 stat、媒体指纹及语言为依据，`.pending` 强制恢复；并发替换时有效但不可缓存的摘要仍由完成消息发布。业务完成后尚未退出的线程单独保留，关闭时仍等待回收。 |

`ui/app.py`、`ui/workspace.py` 保持原样；新模块不依赖旧 QWidget 路径。`pipeline.prepare_media()`、原始识别文本、源句 ID、声学时间戳以及文档 `.pending` 格式未改变。文本与音轨授权仍独立，人工确认入口仍不在工具 schema 中。

## 兼容层

- `AppSettings` 保留既有公开角色字段。`save_role_changes()` 是旧式直接修改字段的适配器；运行时基线不进入正式 JSON/队列。反序列化白名单排除私有字段。
- `Bridge` 保留 `_settings`、`_workers`、`_messages`、`_active_session_id`、`_set_state`、`_feature_command`、`_v4_send_chat` 等既有测试入口。
- `Bridge.state/stateChanged` 保留旧键；`messages/jobs` 保留 Python list 读视图，新增 `messageModel/jobModel` 专供增量 QML 绑定。
- `Bridge.timer` 保留为事件触发的单次刷新定时器；`_inbox_thread.is_alive()` 是 Future 活动视图。工作区旧 `_switch_timer/_language_timer` 只留下无计时器的取消/状态适配器。
- 旧 `asdict(settings)` 格式的收件箱模拟快照仍可输入；比较前忽略运行时私有字段。
- `prepare_media()` 签名与管道行为完全未重构。便携迁移兼容不存在草稿新表的旧数据库。

## 存量测试调整

没有删除测试或降低行为断言。只更新三处由新存储/单宿主架构必然改变的结构定位：

- `tests/test_qml_v4.py`：草稿持久化检查改读独立 DraftStorage，同时增加 quick-layout 不含两类草稿的断言；关闭前刷新、重启恢复、修订号与保存后清理断言保留。
- `tests/test_qml_v5.py`：教学偏好弹窗从根窗口查找，新增单实例与来源窗口归属断言，原真实点击、保存行为保留。
- `tests/test_subtitle_corrections.py`：修订弹窗从根窗口查找，新增单实例与来源窗口断言，原全屏/伴侣/列表三入口冻结上下文、工具约束与修订行为保留。

## 验证原文

所有回归使用项目 `.cache` 下的隔离数据和离线测试入口；FFmpeg/ffprobe 可用，使用 `--require-ffmpeg`。没有调用外部真实付费 API，没有处理或改写用户媒体/学习库。

Phase 1：

```text
uv lock --check --offline
Resolved 68 packages in 0.89ms

python -m ruff check .
All checks passed!

python .github/run_offline_tests.py -q --require-ffmpeg
771 passed, 1 warning, 113 subtests passed in 147.09s (0:02:27)

git diff --check
无输出，退出码 0
```

Phase 1 主测试 771/771，通过；跳过 0。唯一警告是既有第三方 wordseg/pkg_resources 弃用提示。原文日志位于 `.cache/qa/state-refactor/phase1/`。

Phase 2：

```text
uv lock --check --offline
Resolved 68 packages in 1ms

python -m ruff check .
All checks passed!

python .github/run_offline_tests.py -q --require-ffmpeg
822 passed, 1 warning, 113 subtests passed in 214.22s (0:03:34)

git diff --check
无输出，退出码 0
```

Phase 2 主测试 822/822，通过；跳过 0。相比基线 744 项共新增 78 个测试实例，原有 113 项子测试保留。唯一警告仍是上述第三方弃用提示。原文日志位于 `.cache/qa/state-refactor/phase2/`。

Windows 真窗口（`QT_QPA_PLATFORM=windows`，源码 QML 实例）专项：

```text
12 passed in 21.19s
```

命令为 `.venv/Scripts/python.exe .github/run_offline_tests.py -q --require-ffmpeg tests/test_ui_state_phase1.py tests/test_incremental_models.py tests/test_domain_routing.py::test_invalid_teaching_preferences_stay_open_with_draft_and_local_error tests/test_player_sources.py::test_drag_actual_subtitles_in_both_windows_and_persist`。覆盖弹窗单宿主与浮动/全屏归属、布局重启恢复、增量 delegate/展开/滚动位置保持、教学偏好验证失败保留输入，以及字幕拖动持久化。日志位于 `.cache/qa/state-refactor/native-windows.txt`。

## 下一轮范围与限制

- Phase 3（LLM client、并发批调度器与准备管道）和 Phase 4（控制器组合、BackgroundTaskManager、统一数据库 schema/连接管理）均未开始。本轮新草稿表采用增量建表，不预先实施 Phase 4 的全库迁移。
- 媒体库冷读取是异步的，首次显示短暂使用已有缓存或未准备占位；数据到达后更新，UI 不等待全库 JSON 解析。
- 收件箱仍需要扫描文件系统以识别复制完成；本轮复用 worker，没有改为仅依赖文件监视事件。
- 兼容旧数据格式不代表旧 EXE 能编辑新草稿表；旧布局导入的完整原值保留在迁移快照。未做旧版 EXE 降级交互验收。
- 未刷新任何正式或独立便携包，未发布、推送或改动工作区外文件。外部 API、真实 ASR 质量与真实媒体性能不属于本次重构验收。

## 新增回归测试索引

以下列出源码用例名；参数化用例计数以 pytest 汇总为准。

### `tests/test_settings_role_isolation.py`

- `test_replaced_window_preferences_do_not_revert_external_roles`
- `test_legacy_direct_field_changes_update_only_the_edited_roles`
- `test_settings_wire_format_excludes_runtime_baseline`
- `test_frozen_queue_keeps_its_roles_and_identity_without_runtime_baseline`
- `test_cached_store_registry_refreshes_external_assign_edit_and_delete`
- `test_cached_registry_copies_cannot_mutate_state_or_lose_other_writers`

### `tests/test_draft_storage.py`

- `test_large_chinese_drafts_roundtrip_update_delete_and_workspace_isolation`
- `test_layout_import_is_lossless_once_and_retains_recovery_snapshot`
- `test_failed_legacy_import_keeps_layout_and_all_drafts`
- `test_portable_copy_remaps_internal_keys_only_and_preserves_original`
- `test_portable_failure_rolls_back_drafts_and_preserves_original`
- `test_companion_drafts_survive_cue_switch_restart_and_save`

### `tests/test_thread_snapshots.py`

- `test_recommendation_captures_ui_settings_and_publishes_independent_result`
- `test_cancelled_queued_settings_snapshot_never_probes_or_reads_settings`
- `test_chat_worker_owns_history_events_and_frozen_context`
- `test_human_decisions_remain_absent_from_model_tools`
- `test_model_discovery_freezes_permission_before_worker_runs`
- `test_notebook_worker_owns_selected_entry_ids`

### `tests/test_ui_state_phase1.py`

- `test_single_dialog_host_routes_from_detached_and_fullscreen_windows`
- `test_saved_layout_remains_bound_after_local_edits`
- `test_layout_limit_reports_error_and_keeps_large_draft`
- `test_stale_bridge_language_and_preference_edits_keep_external_roles`

### `tests/test_incremental_models.py`

- `test_list_model_changes_only_updated_rows_and_keeps_move_identity`
- `test_list_model_owns_snapshots_and_supports_legacy_rows`
- `test_markdown_cache_parses_only_changed_content_and_releases_old_revisions`
- `test_real_queue_delegate_keeps_expanded_evidence_across_progress`
- `test_real_chat_delegate_and_reasoning_scroll_survive_stream_updates`

### `tests/test_queue_events_cache.py`

- `test_queue_notifies_changes_unsubscribes_and_isolates_observer_failure`
- `test_queue_observer_runs_outside_lock_and_reentrant_mutation_is_persisted`
- `test_queue_progress_is_throttled_but_stage_and_terminal_changes_are_immediate`
- `test_queue_pause_retry_and_automatic_flags_notify`
- `test_summary_cache_unchanged_rows_do_not_load_or_hash_documents`
- `test_summary_cache_detects_document_media_missing_and_language_changes`
- `test_summary_cache_pending_recovery_and_explicit_invalidation`
- `test_summary_peek_does_not_wait_for_cold_worker_and_invalidation_wins`
- `test_bulk_positions_one_select_canonicalizes_and_supplies_missing_defaults`

### `tests/test_workspace_events.py`

- `test_switch_waits_for_last_worker_callback_and_durable_origin`
- `test_download_decision_and_completion_wake_switch`
- `test_text_reuse_decision_wakes_switch`
- `test_declined_download_cancels_deferred_switch`
- `test_changed_chat_navigation_does_not_drop_origin_failure_guard`
- `test_language_refresh_runs_once_after_origin_worker`
- `test_waiting_workspace_has_no_polling_and_events_coalesce`
- `test_queued_worker_updates_do_not_mutate_closed_workspace`

### `tests/test_domain_events.py`

- `test_progress_notifies_only_its_domain_and_legacy_projection`
- `test_errors_are_local_and_command_routing_keeps_compatibility`
- `test_idle_queue_has_no_poll_and_position_timer_runs_only_while_playing`
- `test_inbox_reuses_worker_without_blocking_ui_or_marking_user_job`
- `test_cold_library_reads_are_off_ui_and_stale_language_is_not_published`
- `test_queue_notification_updates_without_a_periodic_timer`

### `tests/test_worker_completion_lifetime.py`

- `test_shutdown_joins_thread_still_returning_from_completion_emit`

### `tests/test_library_publication.py`

- `test_document_replaced_during_read_publishes_uncached_summary`

### `tests/test_domain_routing.py`

- `test_direct_setting_validation_routes_to_its_panel`
- `test_actual_commands_route_rejections_to_their_panels`
- `test_successful_model_save_clears_only_model_error`
- `test_direct_speech_key_and_collection_errors_keep_local_domains`
- `test_player_error_callback_routes_to_media`
- `test_import_worker_failure_routes_to_media`
- `test_invalid_teaching_preferences_stay_open_with_draft_and_local_error`
