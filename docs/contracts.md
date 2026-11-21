# Integration contracts (2026-11-21)

## Batch concurrency in v0.10

Each workspace stores `llm_high_concurrency` and `llm_max_concurrency`. The queue reads them when starting or resuming each video, including jobs saved by earlier releases. The default is four concurrent requests, the supported range is 1–16, and disabling concurrency gives one. Scheduling preferences are excluded from queue identities and material cache keys. Transcript review, teaching and bilingual verification share the limit and run in sequence. Validated batches are merged by cue ID and checkpointed in source order. A rate-limit response stops new submissions in that stage and allows already issued requests to finish.

For all three stages, ordered unique response IDs may cover a subset of the requested cues. Validate each identified row and checkpoint successful rows before scheduling repairs for only invalid or missing IDs. Recovery retains a per-cue validation message with the checkpoint. Ambiguous identities and incomplete JSON can be retried in smaller batches. Each stage bounds its repair attempts and exposes the cue number and timestamp for a located terminal error. Provider transport and rate-limit errors bypass content repair.

## Reviewed translation track in v0.9

The known language is `language.source`. A text-enabled preparation produces per-source-cue teaching material and runs an additional bilingual review with the configured annotation model. Each successful verification batch checkpoints the verified cue digest, any ordered split translations, and provider response metadata in `translation_review`. The player and translated SRT use the same split boundaries. A stale digest suppresses the affected verified caption and invalidates the complete SRT. Local-only preparation records its scope explicitly.

`PreparationQueue.enqueue_lessons(path, settings)` schedules one existing media document without re-transcription. Completed drafts and current verification records can be resumed. `documents/<id>.translation.srt` is derived from the study JSON, participates in its publication journal and portable relocation, and can be exported from the File menu. Named workspaces can opt to inherit the text connections shown during creation.

## Revision 9 — v0.7

Workspace source and target languages are independent tags, including the same tag and tags outside the bundled ASR engines. The offline UI and setup chat use CLDR labels and accept custom codes. ASR language compatibility gates automatic inference only. A compatible sidecar or configured service can prepare a video in that same workspace.

Chat can create and enter a catalogued study workspace, configure its learning preferences, recommend a model using the actual inference runtimes, reuse installed weights, assign local text roles or configure a transcription service. Speech recommendations and preparation perform no model downloads. The human download decision is bound to the proposal, model, originating session and target workspace settings. An explicit decision in the chat input or confirmation card reaches the same trusted UI slot. Downloaded weights and the Qwen aligner use installation-scoped repositories and locks.

New Agent-created workspaces monitor their media directory and enable automatic preparation. An inbox job retains its identity across restarts, waits for completed copies and checks local model readiness and applicable audio permissions. Existing workspaces retain their automatic preparation preference. Remote text reuse requires a separate scoped decision which lists the actual roles and services. The original chat is saved before switching. The new workspace receives a setup summary.

## Revision 8 — v0.6

The workspace catalog uses the previous data directory as its default workspace. Normal launch opens the last study folder. The main window picker opens, creates and configures study folders under the installation's data/workspaces directory. Each owns its media library, documents, queues, settings, credentials, conversations, notebooks and playback state. Download and inference share the installation-local model cache. A model repository lock and a completeness check prevent duplicate downloads. API connections do not download model weights. Copying media into a workspace is an explicit library action. Exports stay in that workspace.

Every new ASR job takes a local preprocessing pass unless disabled. Local turn detection uses acoustic boundaries with timed words or alignment units. External transcription uses per-workspace endpoint and audio opt-in independently of cloud text. Queue uploads check current permission and endpoint before sending each piece. Imported subtitles take precedence in ordinary preparation. Explicit full regeneration can bypass discovery.

The companion can revise the subtitle selected at the start of the request and the lesson of its underlying source cue. Read and compare the source revision before publishing workspace-owned SRT and JSON. A user correction persists in `metadata.cue_corrections`; teaching-only regeneration reuses the corrected text and boundaries. Preserve source IDs, original text, acoustic times, unrelated cues and external subtitle files. Current source code and this revision supersede the historical contracts below.

## Revision 7 — v0.5 current behavior

The explicit v0.5 request authorizes application operations through `app_actions.py`: video preparation and regeneration, queue control, playback, existing model selection, reasoning strength, teaching preferences, workspace/theme controls, collections and Markdown notes. This supersedes the six-tool limitation below. No arbitrary OS commands, filesystem paths, credentials, or network browsing are exposed. UI mutations dispatch to the Qt main thread; queue acceptance is distinguished from completed processing.

Collections remain an inbox until explicit organization. `NotebookManager.snapshot()` returns separate `collections` and `notes`; organization retains collections and marks them organized. Notes support language comparisons, grammar, goals, examples and personal remarks. Existing Markdown and manual writing survive migration; archived source records preserve video navigation after collection removal. Full notebook reads plus revision checks precede replacement or note edits.

Each chat request freezes session, language, media, selection (maximum 100), cue, model fingerprint and reasoning effort. Retry/edit branches retain that context; changed/deleted model connections fail explicitly. Tool events and safe request metadata persist; credentials and private provider thinking/signature blocks never persist. Native signed blocks remain private within a tool loop.

The user's later v0.5 request permits displaying and saving provider-public thinking text and GPT summaries. The separate `on_reasoning({kind,text})` channel never includes signatures, redacted or encrypted blocks. Message presentation fields are `reasoning_text` (up to 60,000 characters), `reasoning_kind`, `reasoning_elapsed_ms`, and `phase`. They are excluded from model history and diagnostic content. Explicit `protocol='responses'` enables summaries without changing existing connections. Private Responses output stays within the live stateless tool loop.

`AppSettings.teaching_preferences` applies to future teaching calls and their cache specification without starting jobs. `companion_reasoning_effort='default'` inherits connection settings; explicit effort reaches the provider. Unsupported settings surface a clear error. `PreparationQueue.run(..., job_ids=None)` retains full-queue behavior; a whitelist runs only those IDs for scoped Agent requests. Explicit `process_videos` mode `all` uses existing backup/reset protection and preserves personal data and media.

See [ui-v5.md](ui-v5.md) for interface behavior and QML verification. Historical contracts below remain only where not superseded.

## Revision 6 — current development

The explicit v0.4 user request supersedes earlier model restrictions and environment-only credential rules. See [v4-development.md](v4-development.md) for current profile, notebook, session, QML and tool contracts. Default models remain compatible; user-selected profiles support different providers. Manual credentials are DPAPI-protected, never plain text. Bundled open-source fonts replace proprietary font dependencies. `review_provider` is independent from the companion provider. Current implementation introduces `quick/features.py`, `quick/typography.py`, `providers.py`, `credentials.py`, `notebooks.py`, `conversations.py`, and `richtext.py`.

`Store.reset_material(media_path,language,mode)` archives current generated material before clearing; `PreparationQueue.reset(ids,mode,requeue,settings)` serializes against queue workers. `force_asr=True` bypasses external subtitle discovery; text failure/retry still reuses the new ASR checkpoint. Teaching-only reset retains reviewed text and source recognition settings, including when cloud text is disabled.

`saveNotebook` accepts explicit `language` and `revision`; editors bind these to the originating draft. Streaming messages checkpoint to SQLite and remain bound to their original session. Scoped learning tools capture media/language/cue and cancellation at the start of the request. Model schemas and Markdown never authorize arbitrary OS actions.

Python 3.12, src package `tingmu`. Parent owns `models.py`, `storage.py`, `pipeline.py`, `cli.py`, project config/docs. Children own only their assigned files; no further delegation.

## Shared models (dataclasses in tingmu.models)

- `Token(text: str, reading: str = '', meaning: str = '', learn: bool = False)`
- `Cue(id: str, start: float, end: float, text: str, original_text: str = '', translation: str = '', explanation: str = '', tokens: list[Token] = field(default_factory=list), uncertain: bool = False)` seconds, immutable IDs/timing across AI edits.
- `LanguagePair(source: str = 'zh-CN', target: str = 'yue', level: str = 'beginner')` source is explanation/known language; target is video/studied language, allow same language.
- `StudyDocument(media_path: str, language: LanguagePair, cues: list[Cue], provider: str = 'local', model: str = '', metadata: dict = field(default_factory=dict))`, `to_dict()`, `from_dict(dict)`.
- `AppSettings(library_path: str = '', source_language: str = 'zh-CN', target_language: str = 'yue', level: str = 'beginner', provider: str = 'openai', model: str = 'gpt-5.4-mini', asr_model: str = 'large-v3-turbo', device: str = 'auto', audio_track: int = 0, subtitle_offset_ms: int = 0, cloud_text_enabled: bool = True, companion_provider: str = 'openai', companion_model: str = 'gpt-6-astra', target_script: str = 'traditional')`.

## Media owner module tingmu.media

- `scan_media(folder: Path) -> list[Path]`, `probe_media(path: Path) -> dict` normalized/raw ffprobe JSON fine.
- `load_subtitles(path: Path) -> list[Cue]`, `save_srt(cues: list[Cue], path: Path)`.
- `extract_audio(media_path: Path, output: Path, audio_track: int = 0, start: float = 0, duration: float | None = None) -> Path` audio track ordinal, not absolute stream ID.
- `transcribe(media_path: Path, language: str = 'yue', model: str = 'large-v3-turbo', device: str = 'auto', audio_track: int = 0, start: float = 0, duration: float | None = None, progress=None, cancelled=None) -> list[Cue]`; absolute timestamps, optional progress(str), cancelled() bool; use local ASR only.

## Agent owner module tingmu.agent

- `provider_status() -> dict` only nonsecret availability/default model metadata.
- `LearningAgent(provider: str = 'deepseek', model: str | None = None)`
- `.annotate(cues: list[Cue], language: LanguagePair, progress=None, cancelled=None) -> list[Cue]` contextual chunking, validated response, preserve IDs/timings/original text, conservative correction.
- `.summarize(entries: list[dict], language: LanguagePair) -> str` notes/words into concise Markdown.
- `annotate_local(cues: list[Cue], language: LanguagePair) -> list[Cue]` deterministic segmentation/reading fallback, explicitly no invented definitions.

## Storage parent module tingmu.storage

- `Store(data_dir: Path | None = None)`, `.data_dir`, `.load_settings() -> AppSettings`, `.save_settings(settings)`
- `.save_document(doc) -> Path`, `.load_document(media_path: str, language: LanguagePair) -> StudyDocument | None`
- `.save_word(media_path: str, cue: Cue, token: Token, language: LanguagePair)`, `.save_note(media_path: str, cue: Cue, note: str, language: LanguagePair)`
- `.entries(media_path: str | None = None) -> list[dict]`, `.delete_entry(entry_id: int)`; dict has id, kind (word/note), media_path, cue_id, start, text, reading, meaning, note, sentence, source_language,target_language,created_at.
- `.export_markdown(path: Path, media_path: str | None = None) -> Path`, `.export_csv(path: Path, media_path: str | None = None) -> Path`, `.save_review(media_path: str, text: str) -> Path`.
- `.save_position(media_path: str, milliseconds: int)`, `.load_position(media_path: str) -> int`.

## Pipeline parent module tingmu.pipeline

- `prepare_media(media_path: Path, settings: AppSettings, store: Store, subtitle_path: Path | None = None, start: float = 0, duration: float | None = None, progress=None, cancelled=None) -> StudyDocument` loads sidecar/text subtitle or transcribes then annotates; partial checkpoint after ASR and chunks; no API call unless cloud_text_enabled and provider != local.

## UI owner tingmu.ui package

- `run_app(initial_path: str | None = None, data_dir: Path | None = None) -> int`, and `MainWindow(store: Store)`.
- Main window Qt widgets, QtMultimedia player, library/settings, background preparation, playback controls/audio/subtitle track/speed/volume/seek/fullscreen/keyboard/A-B repeat, subtitle file import/export, progress/cancel/error.
- Separate synchronized companion window with clickable token+reading buttons, translation/explanation, save sentence/notes, word notebook review/export/summarize. Import public APIs above. All slow ASR/LLM work in worker threads, never touch UI from worker threads.

Default media is ../22.mkv, never alter sample. Dark utilitarian UI: #151719 #ECEAE4 #92988F #86B99A, Microsoft YaHei UI. Chinese short labels; no filler. External video stays outside git. Secrets only environment/Windows registry, never print or save. Environment names include Machine scope OpenAI and DEEPSEEK; process only currently has DEEPSEEK. Use standard aliases too. User authorizes text-only model tests on real sample; never upload audio/video.

## Revision 2 — latest user requirements

- AppSettings batch defaults now `provider='openai', model='gpt-5.4-mini'` based on real-sample conservative behavior. New `companion_provider='openai', companion_model='gpt-6-astra'` (verified by real account calls; also supports `gpt-6.1-sol`), `target_script='traditional'`.
- `Store.load_conversation(media_path: str, language: LanguagePair) -> list[dict]`, `.save_conversation(media_path, language, messages)`; each message is `{role:'user'|'assistant',content:str}`. Empty media path supports general software help before opening video.
- `tingmu.companion.CompanionAgent(provider='openai',model=None).reply(message,history,language,current_cue=None,context=None) -> str`; text only, bounded history, strong model, no execution tools. UI needs worker dispatch and explicit cloud-text toggle.
- `tingmu.language.normalize_cues(cues,language,target_script='traditional')` normalizes Cantonese display/tokens to Hong Kong Traditional, preserving original_text and timings. Does not reconstruct missed spoken words.
- Optional Qwen3-ASR in isolated `.cache/qwen-venv`, models `.cache/qwen-models`; media agent owns installer and new ASR aliases. Keep current faster-whisper fallback available.
- UI revision: video dominant, technical settings collapsed, separate companion with 讲解/对话/笔记 tabs. No further Computer Use this turn after user Escape; offscreen GUI code tests permitted.

## Revision 3 — local disk policy and completed integration

- All new large downloads, SDK caches and temporary files stay inside this project's `.cache`; install via `scripts/setup.ps1` and `scripts/setup_qwen.ps1`. Runtime import initializes process-only paths through `runtime.py`. Do not mutate global environment or clear unrelated caches.
- `transcribe(..., metadata_out: dict | None = None)` optionally populates actual runtime/device, audit path and bounded alignment warnings after success. Pipeline merges this into the saved document; UI exposes warnings via the existing status/tooltip.
- Completed sample: Qwen 1.7B, CUDA, 898 cues with all language materials ready. Original text, IDs, times and uncertainty survive the pipeline. Raw artifacts remain private. See `reports/acceptance.json` and `handoff.md`.
- Notebook review uses the companion provider/model (Astra or Sol), independently of the batch subtitle model. All background jobs must preserve each other's busy/progress state.

## Revision 4 — modular portable workspace

- AppSettings adds review_enabled=True, review_model='gpt-6-astra', subtitle_min_ms=1200, subtitle_gap_ms=180, companion_hold_ms=2200, overlay_opacity=0.86, overlay_font_size=20.
- TranscriptReviewer.review(cues,language,progress=None,cancelled=None,checkpoint=None,context_cues=None) returns corrected source Cues; review_metadata records per-ID state/reason; original_text,IDs,times remain unchanged. Pipeline persists reviewer independently. Teacher preserve_text=True uses reviewed context and rejects subsequent edits.
- SubtitleTimeline(cues,min_duration_ms,gap_fill_ms,companion_hold_ms): .cues readable groups; .segments separate display ranges; .subtitle_at(ms), .companion_at(ms), .source_ids(cue_id). Group Cues use source acoustic union, never display extension, for replay/bookmarks. The original StudyDocument.cues remains untouched.
- PreparationQueue(store): enqueue(paths,settings,subtitle_paths=None),snapshot(),run(progress,cancelled),pause(),retry(id),retry_failed(),remove(id),clear_completed(),pending_count. Persistent status queued/running/paused/failed/completed; app restart recovers interrupted running jobs.
- UI docks and menu controls are owned by ui/workspace.py; chat is independent, fullscreen uses one existing QMediaPlayer. Layout/overlay detail switches persist in workspace.json; scalar reading settings use AppSettings.
- Portable EXE distribution lives in ignored dist/Tingmu; marker-based runtime discovery ignores external inherited Python/cache paths. All portable data and caches are beside EXE. Original user database remains unchanged; internal-media path migration is scoped to portable data.
- C pip cache explicitly authorized for removal was cleaned using pip cache purge:1701files,1264.5MB; other software/data unaffected.

## Revision 5 — Qt Quick and independent subtitle gaps (2026-11-14)

- Default UI is `tingmu.quick.main.create_qml_app(data_dir=None, initial_path=None) -> (QApplication, Bridge, QQmlApplicationEngine)` / `run_app(initial_path=None,data_dir=None)`. `tingmu.ui.run_app` re-exports it; old QWidget classes remain only for regression references.
- QML context `backend` exposes separately notified state/settings/playback/cues/activeCue/activeIndex/library/jobs/messages/entries/layout. User actions use `command(str,QVariantMap)` and validated `setting(str,QVariant)`. `attachVideo(QObject)` keeps the sole QMediaPlayer; `refreshVideo()` re-uploads paused CPU image after scene changes.
- `Cue.segmented_texts:list[str]=[]` exact-concatenates source text. Review v2 supplies short boundaries (42 display columns maximum). Source ID/raw text/times remain immutable. `SubtitleTimeline.cues` never groups adjacent sources; only split parts use source:part:N and text_proportional timing. `source_ids()` maps display IDs back to source. Legacy min_duration_ms/companion_hold_ms arguments are ignored; only gap_fill_ms (default500, max1500) bridges actual short blanks.
- Bridge active cue adds sourceIds/sourceStart/sourceEnd/timingBasis/reviewReason. Export uses displayed split cues; original acoustic source remains in documents. Split translation/explanation still refers to source sentence.
- MediaLibrary stores app-local media-library.json, accepts files from arbitrary directories without moving/copying media. App data default is portable data/ or development .data/. Portable migration includes media-library.json and quick-layout.json (draft media keys only, never note content).
- Watching shows library/player/companion/subtitles; preparation shows library/queue; independent chat opens on demand. All module settings are local gears. Drag titles swaps slots or floats windows; views and geometry persist. Fixed fullscreen overlay has a bottom-right resize grip; content never drives width/height.
- Only native GPT-6-Astra subagents are allowed. The user explicitly forbids loading/applying the obsolete dispatching-parallel-agents skill.

## Developer diagnostics (Revision 5)

- AppSettings.developer_mode=False. Help→开发者模式 enables DEBUG events and 查看日志. INFO stage/error logs remain active normally.
- Bridge.diagnostics: safe runtime/config/queue metadata; Bridge.logs: latest structured events. Commands refreshDiagnostics/exportDiagnostics/openLogFolder. Automatic refresh every2sec only while developer mode enabled.
- diagnostics.AppLog writes data/logs/events.jsonl (2MiB+3backups), no learning/request/response bodies. Known environment keys and token patterns redacted. Error type and stack locations retained; exceptions do not dump arbitrary payload strings.
- Portable bootstrap captures redacted application.log (2MiB+2backups). Diagnostic exports are app-local data/diagnostics/*.json, never automatically uploaded. Media context uses a hashed ID, no transcript content.
