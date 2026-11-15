# v0.4 upgrade — integration plan

User authorized the complete local application upgrade, portable refresh and UI verification. No changes to original ../22.mkv or existing personal records. New explicit requirements supersede older restrictions: user-configured models and manually entered protected API keys are permitted; the companion may use scoped learning tools. All fonts must be open source and bundled.

## Design

Purpose: watch local video, understand language in context, collect material into a durable language notebook, and practice with a conversational tutor.
Direction: editorial reading workspace within the established desktop player. Asymmetric language/topic navigation, central Markdown notebook and right-hand collected sources/practice. No web runtime.
Themes: forest (#101315/#191e21/#8bbda1), paper (#f5f1e8/#fffdf8/#39694c), mist (#e9f0ef/#f7fbfa/#276b69), graphite (#17191d/#22252b/#c5b58e). Semantic colors must update live in every window.
Fonts: bundled Source Han Sans SC, Source Han Serif SC, Source Sans 3, Source Code Pro, with licenses. Chinese body sans, notebook headings serif, code monospace. No proprietary font dependency.

## Ownership

- Provider implementer: providers.py, credentials.py, agent.py, review.py, companion.py; their new/affected focused tests and docs/providers.md. No shared settings or bridge edits.
- Learning implementer: notebooks.py, conversations.py, richtext.py; focused tests. No Store/bridge/QML edits.
- UI implementer: quick/qml/** and font assets/licenses; UI-specific tests and docs/ui-v4.md. No bridge/main/models edits.
- Root: models.py, storage.py, pipeline.py, batch.py, quick/bridge.py, quick/main.py, diagnostics/portable_paths/packaging, integration, all remaining docs and tests.

## Shared settings

New AppSettings fields: theme='forest', annotation_profile_id='', review_profile_id='', companion_profile_id=''. Existing provider/model fields remain compatible. New profiles override legacy config only when an ID is selected. API keys never enter AppSettings, queue snapshots, messages, diagnostics or returned public profiles.

## Provider contract

ModelRegistry(data_dir).public_profiles() -> list[dict]; .presets() -> list[dict]; .save_profile(payload, api_key=None) -> dict; .delete_profile(id); .discover_models(id) -> list[str]; .resolve(id) -> dict (private resolved config). Public profile fields: id,label,provider,protocol,base_url,model,key_source,env_var,has_key,models; optional reasoning_effort and max_tokens. IDs stable.
LearningAgent(provider,model,*,profile=None), TranscriptReviewer(...,profile=None), CompanionAgent(...,profile=None): profile comes from registry.resolve(). Existing two-argument call sites/tests remain valid. CompanionAgent.reply(...,on_delta=None,cancelled=None,tools=None,tool_executor=None) supports streaming text and bounded scoped tool execution; final text and last_response_metadata. tools are OpenAI function schemas, executor(name,args)->dict. No arbitrary filesystem/process/network tools. Unsupported tool protocols may fall back to text while explicit UI learning actions still work. Streaming cancellation retains partial text at bridge level. Headers never follow cross-origin redirects. Supported mainstream presets and endpoints must cite official docs.

## Learning contract

NotebookManager(store): snapshot(target_language)->dict with language,title,markdown,revision,path,topics,entries,books,stats; sync(target_language)->dict; save(target_language,markdown,expected_revision)->dict; organize(target_language,agent,source_language='zh-CN',cancelled=None)->dict; review(entry_id,rating)->dict; practice(target_language)->list[dict]. Notebook markdown editable with revision conflict protection, backups, managed generated sections preserving manual writing; language isolation, stable entry IDs and tingmu://entry/<id> source links. Organization must only use existing IDs and preserve all collected entries. Agent receives bounded text, no full paths. Spaced review ratings again/hard/good/easy.
ConversationStore(store): list_sessions(target_language)->list[dict]; create(language,media_path='',title='新对话')->dict; load(session_id)->dict including id,title,media_path,source_language,target_language,messages; save(session_id,messages,title=None); delete(session_id); rename(session_id,title); migrate_legacy(media_path,language)->dict or None. Preserve message id/role/content/status/model/created_at fields. Never expose protected credentials.
richtext.markdown_blocks(text)->list[{kind:'markdown'|'code',text,language?}]. Sanitize arbitrary raw HTML, images/remote resources and unsafe links. Code body copied verbatim. Qt MarkdownText handles headings, lists, tables. No execution.

## Bridge/QML contract (root implements)

New properties: modelProfiles, modelPresets, discoveredModels, modelStatus, notebooks, chatSessions, activeSessionId. Existing messages gain id,status,model,blocks. Existing state gains streaming,learningBusy,modelBusy,selectedLanguage. notebooks snapshot includes current language/book and all language books.
Commands:
- saveModel payload profile fields plus apiKey (write-only), deleteModel{id}, discoverModels{id}, refreshModels{}, selectModel{role:'annotation'|'review'|'companion',id}.
- newChat{}, selectChat{id}, renameChat{id,title}, deleteChat{id}, stopChat{}, retryChat{}, editChat{id,text}, copyText{text}, openLink{url}. sendChat{text} existing.
- selectNotebook{language}, syncNotebook{}, saveNotebook{markdown,revision}, organizeNotebook{}, openNotebook{}, exportNotebook{}, reviewEntry{id,rating}, practiceNotebook{}, askNotebook{text}.
- resetJobs{ids:[jobid],mode:'all'|'teaching',requeue:bool}, resetMedia{paths,mode,requeue}. UI confirmation states exact scope and keeps media/notes. Root resets settings snapshot to current selections and avoids running jobs.
- saveSelection{cueId,text} supports selected source substring; saveWord existing; saveNote existing.
Notebook topic/video filters may be local QML. Notebook links jump to saved entry through openLink. Paused full-screen overlay exposes word chips, text selection, save sentence, and open learning workspace. Root provides notebook refresh after every word/note save/delete.
Root registers bundled fonts in quick/main.py and theme setting persists. UI may use a Theme singleton (qmldir) with properties bound to backend.settings.theme.

## Acceptance

Meaningful tests cover reset/cache bypass and retained notes; profile secrets/redirects/streaming; language notebook isolation/idempotence/conflicts/tools; chat sessions and edit/retry/cancel; Markdown sanitization; real QML theme/menu/learning/paused overlay behavior. Use isolated test data; retain current personal data hashes when refreshing portable app. Verify actual EXE, screenshots and usability at desktop and smaller resolutions. Update handoff/README and reports with actual evidence and limitations.
