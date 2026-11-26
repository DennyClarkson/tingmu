# Tingmu portable user guide

[简体中文](portable.md)

Download the Windows x64 portable ZIP, extract the entire archive to a writable folder with enough free space, then open the extracted `Tingmu` folder and double-click `Tingmu.exe`. The folder includes Python, the Qt Quick player and its multimedia plugins, four bundled fonts, and the basic study tools. Component licenses are listed in `THIRD_PARTY_NOTICES.md`. [GitHub Releases](https://github.com/DennyClarkson/tingmu/releases)

## Play your first video

1. At first launch, **Runtimes and dependencies** checks your computer offline. Select **Skip downloads and enter** (暂不下载，直接进入) to play a video and use external subtitles with **0 B downloaded**. A clean installation starts with an empty workspace and text APIs disabled.
2. To switch the interface to English, choose **视图 → 界面语言 / Language → English**. The choice applies to the installation. Each workspace has its own study languages. The workspace selector at the top lets you create or reopen one. **Language you know** sets the language of translations and explanations. **Language to study** identifies the dialogue you want to learn. You can choose the same language for both or enter a custom language code.
3. Choose **File → Open video…** (`Ctrl+O`). The library can reference a file anywhere on your computer. Use **Copy videos to workspace…** to keep a copy together with its sidecar subtitles. You can also add files to the workspace’s `media/` folder from **Workspace settings → Languages and files**. With **Automatically prepare videos added to media** enabled, Tingmu waits for copying to finish and queues preparation when resources are ready.
4. For a subtitle file you already have, use **File → Import playback subtitles…**. Switch to **Watch** to view the video. To prepare a study document, follow the workflow below. **Help → Quick start and user guide** opens the in-app guide and shortcuts to each setup screen.

### Optional components

Reopen **Runtimes and dependencies** from **File** or **Workspace settings → Speech recognition**. A component marked **Using an existing local component · 0 B** is ready to use.

| What you want to do | What to set up |
| --- | --- |
| Play videos, change audio tracks, read external subtitles or existing study documents | Everything needed ships in the ZIP. |
| Extract audio, call a transcription service or decode embedded subtitles | Detect or select a directory containing both FFmpeg and FFprobe. You can install the media tools into `bin/` when needed. |
| Run Whisper or ONNX locally | Select the local Whisper engine. Engine installation and model-weight downloads are separate steps. |
| Accelerate Whisper on an NVIDIA GPU | Use a compatible NVIDIA driver. Detect existing CUDA 12 cuBLAS and cuDNN 9 libraries before choosing a runtime download. |
| Use Qwen3-ASR | Select a local Python environment with PyTorch and Qwen3-ASR installed, or select the separate runtime, which requires several GiB. |

Review the estimated download size before confirming. The download panel shows the current stage, bytes received, transfer speed and estimated time remaining. Totals are marked as estimates when file lengths are incomplete. Choose **Continue in background** to return to the player, then click the download status at the bottom for verification, extraction and installation progress. You can also cancel a download.

## Choose how to use subtitles

| Goal | How to start | Result |
| --- | --- | --- |
| Watch with an external subtitle track | Open the video, then **File → Import playback subtitles…**. | The SRT, VTT, ASS or SSA file is saved in the workspace’s `playback-subtitles/`. Toggle it independently of the study document. |
| Study a specific original-language subtitle file | Select the default **Classic** workflow, open the video, then use **File → Create study document from subtitles…**. For a video with an existing document, use the replacement action in that menu. | The selected subtitles become the source text. Regular classic queue jobs look for matching sidecars and transcribe the selected audio track if needed. With text models disabled, you can still prepare source captions and local word segmentation. |
| Check which subtitles can be reused for bilingual study | Select **Subtitle Agent** under **Prepare → Workflow for new tasks**, then queue the video. To specify a sidecar, open the video and select **Choose subtitles and queue this video…** or **File → Choose subtitles for the Agent queue…**. | Automatic discovery checks matching sidecars and embedded tracks. Selecting a file checks that file and the embedded tracks. Each new job remembers its workflow. |

For a video with several audio tracks, open the queue settings and set **Audio track (starting at 0)** to the dialogue track you want to study. In the library, check the videos to prepare, add them to the queue and select **Start queue**.

### How the Subtitle Agent chooses material

The Agent checks the selected audio track, identifiable series and episode numbers, actual subtitle language and timing coverage. The job card shows which source it chose, how many source lines and translations it recovered, and the current stage. Open **Subtitle assessment details** to see why a candidate was skipped, such as an episode mismatch or insufficient dialogue coverage.

| What the Agent finds | What it uses |
| --- | --- |
| Usable captions in the language you study | Uses their text and timing, generates per-line explanations and missing translations, then checks the new translations against the source with the teaching model. |
| Original and translated captions that can be matched by time | Keeps the matched translations. Generates and checks only missing translations. A fully paired track still needs per-line explanations. |
| Only captions in the language you know | Transcribes the selected audio, or reuses a saved acoustic transcript for the same media, track and time range. Time-overlapping translations provide context. Drafts are generated and checked against the recognized dialogue. |
| No usable text dialogue | Transcribes the chosen audio and prepares explanations and translations from that transcript. Image tracks such as DVD or PGS can be displayed. Extracting their text would require OCR, which this workflow does not implement. |

Every Agent job needs a teaching model to finish. If one is missing, Tingmu saves recovered material. Choose **Configure the teaching model…** on the job, set its role and workspace permission, then retry. For an existing classic document, choose **Complete explanations and translations** in the companion or in the player’s subtitle settings to reuse saved work.

## Configure speech and text permissions

**Local speech recognition.** In **Workspace settings → Speech recognition**, choose built-in Whisper or Qwen and a model suitable for the audio language and your device. Choose the model download action, review the size and confirm. Workspaces in an installation share the weights. For an existing complete CTranslate2 Whisper model, place it under the installation’s `.cache/models/` and select the existing local Whisper model in settings. Choose `auto`, `cpu` or `cuda` as appropriate. Video playback and reading subtitle files require no speech model.

**Voice separation and segmentation.** Voice-separation and speaker-change models are optional downloads. With preprocessing enabled and no separation model installed, spectral processing reduces repeating background sounds. With no speaker-change model, Tingmu uses the recognizer’s segmentation. Study materials retain the original transcript and acoustic timestamps. Display times inferred when a text model splits a long line are estimates. Replay uncertain lines and use **Correct this line → Review and save** to record a correction. [Local speech processing (Chinese)](https://github.com/DennyClarkson/tingmu/blob/main/docs/local-speech.md)

**Transcription services.** You can use a running loopback API or specify a remote transcription service. For remote transcription, select the external API, enter its endpoint, model and subtitle format, and explicitly enable **Allow this workspace to send audio**. During transcription Tingmu sends segments of the selected audio track to that endpoint. Playback, extraction and preprocessing run locally. [External transcription settings (Chinese)](https://github.com/DennyClarkson/tingmu/blob/main/docs/external-asr.md)

**Text models.** In **File → Global text models…**, save the connection and assign models to **Full transcript review**, **Batch teaching** and **Study chat** as needed. Connections and role assignments are shared across the installation. Each workspace chooses its own policy under **Text models and API**: disabled, local API only or remote text allowed. Remote teaching sends text under that workspace’s permission. Sending audio requires the separate speech permission above. [Model connections and permissions (Chinese)](https://github.com/DennyClarkson/tingmu/blob/main/docs/providers.md)

### Monitor, retry or reset a job

The Prepare view lists missing prerequisites and links to the settings that resolve them. During processing, the job shows the source track, line counts, parallel batches and recently checked bilingual lines. A completed job can open directly in the player. Queued jobs retain their original permission constraints. If you grant broader permissions, follow the readiness prompt to queue the video again.

**Concurrent model requests** in the workspace text settings or queue settings allows 1–16 simultaneous batches. The default is four. Turning concurrency off runs one batch at a time. Changes apply to the next video or when you pause and resume the queue.

Tingmu saves successfully validated lines. Missing lines and malformed fields are retried individually or in smaller batches. If processing stops, use the cue number, timecode or service error on the job card to locate the problem. For rate limits, reduce concurrency, wait as indicated and retry. For authentication, balance or model-access problems, check the connection first.

To regenerate material, pause the queue, select the jobs and choose **Redo explanations only** or **Clear transcription and explanations**. The first option keeps recognition results and reviewed source text. The confirmation dialog lets you requeue the jobs. The workspace keeps the previous material in `material-backups/`.

## Watch, save and discuss

- **Player and subtitles.** The gear beside the timeline selects the playback audio track and output device. In subtitle settings, turn on **Show study subtitles** and translations in your known language to watch bilingually. Embedded and imported subtitle tracks have independent visibility. Drag subtitles in windowed or fullscreen mode, or move and scale image subtitles separately. Restore their position in subtitle settings. The style panel previews and saves the font, background, outline and placement.
- **Fullscreen and layout.** Press **F** for fullscreen, **Space** to pause or resume, and **Esc** to exit fullscreen. The player settings control how long the controls stay visible and whether to reserve room below the video. Adjust the companion overlay’s opacity, type size and content, or drag its lower-right corner to resize. The **View** menu opens floating panels and a separate chat window, or restores the default layout.
- **Exports.** Export source subtitles from **File**. Exporting the known-language translation becomes available once every cue has an accepted source translation or a model-checked translation. The workspace’s `documents/` holds the study JSON, source SRT and complete `.translation.srt`.
- **Saved items and notes.** Click a word card or save a whole line in the companion. In fullscreen, pause to select subtitle text. Review items in **Learn** and organize them into language-specific Markdown notes, preserving your own writing and references. Clicking an already organized word shows its status. Click the same card again to return it to the collection inbox. Follow a citation in a note to replay its original line.
- **Chat.** The local setup assistant can find or create a workspace, check your device and list installed models. Try typing `I want to learn English` in Chat. Confirm a proposed download in its originating conversation using the confirmation button. With a study chat model assigned, ask about the current line, control playback and the queue, organize notes or save preferences for future explanations. Editing or retrying a message creates a branch that retains its original video, languages, model and reasoning effort.
- **Background work.** Use **File → Hide to system tray** to keep the queue and downloads running. Click the Windows notification-area icon to restore the window. Right-click it to change what closing the window does or to quit Tingmu.

## Data, backups and updates

| Location in the portable folder | Contents |
| --- | --- |
| `data/` | Default workspace, global model connections and workspace catalog. Additional workspaces live in `data/workspaces/`. |
| Within each workspace | `media/` holds copied videos, `documents/` holds study materials, `notebooks/` holds notes, `study.db` holds saved items, chats and playback history, and `exports/` is the default export folder. |
| `.cache/` | Shared model weights, runtime caches and temporary files. |
| `app/`, `runtime/python/` | Application code, component version and SHA-256 manifests, bundled core Python and Qt. |
| `bin/`, `.cache/runtime-deps/`, `runtime/asr/` | Optional media tools, Whisper/ONNX and CUDA libraries, and the separate Qwen environment. |
| `data/runtime-setup.json` | First-run choices and verified external dependency paths. |

**Back up or move the installation.** Quit Tingmu, including the tray process, then copy the entire portable folder. At startup, Tingmu can relocate references to media inside the installation. Migration preserves the original databases and JSON in the workspace’s `migration-backups/` and stops on a destination conflict. Videos referenced outside the installation still need to be available at their original paths. A personal copy includes study records. Use a clean release when sharing Tingmu with another person.

**Update from a release ZIP.** Quit Tingmu from the tray and back up the entire old folder. Extract the new ZIP to a fresh writable location. Before opening it, copy your old `data/` into the new `Tingmu/` directory. To reuse downloaded models and optional components, copy any existing `.cache/`, `bin/` and `runtime/asr/` as well. Keep the new release’s `app/` and `runtime/python/`. Launch the new copy and check the last workspace, saved items, notes and video playback. Detect dependencies again or select new paths if you changed computers. Manually saved API keys need to be entered again after changing computers or Windows accounts. If you need to roll back, reopen the backed-up installation.

**Diagnostics.** For startup errors, read `data/logs/application.log`. Use **Help → Developer mode → View logs** to inspect events or export diagnostics to the current workspace’s `diagnostics/`. Workspace events appear in `logs/events.jsonl` with up to three rotated backups. The startup log keeps two backups. Each file is limited to about 2 MiB. Diagnostics include exception types, code locations and redacted environment details. They exclude subtitle, chat, note and request bodies.

## Build and verify a portable copy

Maintainers can build from a configured Windows x64 Python 3.12 development environment in `.venv/` or from the core Python of an existing portable folder. Both routes produce the same clean core distribution. The build copies program and runtime dependencies, uses the system .NET Framework compiler for the launcher, and records Qt dependency checks, resource hashes and the model-payload audit. [Development setup (Chinese)](https://github.com/DennyClarkson/tingmu/blob/main/docs/development.md)

From the repository root, use a fresh QA destination and choose the build route you need.

```powershell
$qa = '.cache/qa/bundle-opt'
New-Item -ItemType Directory -Force $qa | Out-Null
# Build from the development environment
.venv/Scripts/python.exe -B packaging/build_portable.py --destination "$qa/Tingmu"
# Or use an existing portable folder as the source for a clean copy
.venv/Scripts/python.exe -B packaging/build_clean_install.py "$qa/clean" --source "$qa/Tingmu"
```

With Tingmu and its tray process closed, `packaging/build_clean_install.py <installation-folder> --refresh` refreshes the program and its manifest while retaining workspaces, models and optional components. If the launcher source or icon changed, build a full copy in a new directory.

On a relocated test copy, run `Tingmu.exe --portable-smoke --smoke-media "absolute path to a dual-audio-track video"` to check playback, audio switching, seeking, speed changes, pause, fonts and first-run setup offline. Where a system tray exists, the check also hides and restores the window. For headless checks set `QT_QPA_PLATFORM=offscreen` and `QT_QUICK_BACKEND=software`. Read the report in `data/logs/portable-smoke.json` in the test copy. Its temporary workspace and first-run state live in `.cache/portable-smoke/`. [Size, cold startup and measurements (Chinese)](https://github.com/DennyClarkson/tingmu/blob/main/docs/bundle-and-startup-optimization.md)
