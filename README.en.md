<img align="right" src="src/tingmu/quick/brand/app-icon.svg" width="88" height="88" alt="Tingmu logo: open green pages framing a play button">

# Tingmu 听幕

[简体中文](README.md) · **English**

Tingmu is a Windows video player for learning a language one line at a time. Open an episode or a recorded lesson and click a subtitle to replay the line, read an explanation, or save a word or phrase. You can also ask the study companion about the dialogue you’re watching. Tingmu starts with Cantonese for Mandarin speakers. Each workspace lets you choose the language you know and the one you’re studying, including the same language for both.

## Get started

**Windows x64 portable app.** Open the [latest stable release](https://github.com/DennyClarkson/tingmu/releases/latest). Under **Assets**, download the file ending in `-windows-x64-portable.zip`. Extract the entire ZIP to a folder you can write to, then double-click `Tingmu/Tingmu.exe`. On first launch, Tingmu checks your computer for installed components offline. Select **Skip downloads and enter** (暂不下载，直接进入) to play videos with external subtitles right away. Playback needs **0 B of additional downloads**. When you’re ready to transcribe audio, open **Runtimes and dependencies** to configure the components you need. [All releases](https://github.com/DennyClarkson/tingmu/releases) · [Portable app and first-time setup](docs/portable.en.md)

To switch to English, open **视图 → 界面语言 / Language → English**. You can switch among English, Simplified Chinese, and Traditional Chinese at any time from **View → Interface language**. This setting applies to the whole installation. To choose the languages for subtitles and explanations, click the workspace name at the top and select your known and study languages.

**Run from source.** The `main` branch includes the latest development changes. Check the release notes for the features in a particular portable build. On Windows, install Git and uv, clone this repository, and open PowerShell in the directory containing `pyproject.toml`. Run these commands to set up and launch the CPU development environment. The first setup downloads dependencies. You can configure speech models later. [Development setup and checks (Chinese)](docs/development.md)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Dev -CpuOnly
.\.venv\Scripts\python.exe -m tingmu gui
```

## Prepare subtitles and explanations (0.12)

1. Add a video to the **Media library** and open the episode you want to study. In **Prepare**, select the audio track and set **Workflow for new tasks**. The default, **Classic**, uses your workspace’s transcription, review, and explanation settings. Choose **Subtitle Agent** if you already have subtitles or want Tingmu to check the video’s embedded subtitle tracks. Each task keeps the workflow selected when you add it to the queue.
2. To use a specific SRT, ASS, SSA, or VTT file, select **Subtitle Agent → Choose subtitles and queue this video…**. To have Tingmu find matching subtitle files alongside the video (including those with the same base name) and check embedded tracks, select the video in the library and add it to the queue.
3. Click **Start queue**. The Subtitle Agent checks the subtitle text, series and episode information where available, the selected audio track, and how much of the dialogue the subtitles cover. Expand a task to see which sources it accepted or ruled out, how many original lines and translations it used, and which step is running.

For example, if you know Chinese and are studying English, Tingmu handles the subtitles that pass these checks as follows.

| Available subtitles | Where the English dialogue comes from | How Tingmu handles the Chinese translation |
| --- | --- | --- |
| English only | Uses the supplied lines and timings directly, skipping transcription | Generates and checks a translation for every line |
| English and Chinese | Uses the supplied English lines and timings directly, skipping transcription | Keeps translations already matched to individual lines. Generates and checks only the missing ones |
| Chinese only | Transcribes the selected English audio track or reuses a matching transcript of the audio | Uses Chinese lines that overlap in time as references for review and explanations. Generates and checks translations against the English dialogue |
| No usable text subtitles | Transcribes the selected audio track or reuses a matching transcript of the audio | Generates and checks translations against the original dialogue |

The same rules apply to embedded text tracks. The task also lists image-based tracks such as DVD and PGS subtitles. Reading their text requires OCR. **Every Subtitle Agent task needs a line-by-line explanation**, even when both languages are already present. If a teaching model is unavailable, Tingmu keeps the material it has recovered. Configure a model and click **Retry failed** to continue. [Workflow and progress details](docs/portable.en.md)

## Watch, study, and manage your data

- In **Watch**, click a subtitle to jump to that line. Use the Up and Down arrow keys for the previous or next line, or press **F** for fullscreen. Open **Source** to check the original wording and timing evidence. If you hear a transcription error, select **Correct** to submit it for review.
- Save a word or a whole line, then jump back to its place in the video from your study notes. In the separate **Chat** window, you can ask the companion to explain the current line, queue videos for processing, or organize saved items. You can keep multiple conversations, stop a reply, edit a message, and retry. [Chat and app actions (Chinese)](docs/conversation.md)
- Under **File → Global text models**, assign model connections for review, explanations, and chat. Each workspace has its own text permissions. Speech recognition runs locally by default. To use an external transcription service, you must also allow the workspace to send the selected audio track. [Text models and permissions (Chinese)](docs/providers.md) · [Speech APIs (Chinese)](docs/external-asr.md)
- You can leave videos in their original location or copy them into a workspace. The portable app stores your learning data in `data/` next to the executable. A source checkout uses `.data/` by default. The repository includes the source code, tests, and packaging scripts. Models, portable runtimes, and personal study materials are stored separately. [Directories and disk usage (Chinese)](docs/disk-space.md)

## Contribute

Tingmu uses Python 3.12 and Qt Quick/QML. The development guide covers launching the app, running the offline regression suite, and building a portable package. The handoff notes record checks and known limitations for each version.

[Contributing (Chinese)](CONTRIBUTING.md) · [Local development and testing (Chinese)](docs/development.md) · [Modules and data flow (Chinese)](docs/architecture.md) · [Integration contracts](docs/contracts.md) · [Version handoff notes (Chinese)](handoff.md)
