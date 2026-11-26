import json
import os
from pathlib import Path
import re
import subprocess
import sys

from PySide6.QtCore import QMetaObject, QObject

from tingmu.models import Cue, LanguagePair, StudyDocument
from tingmu.quick import localization
from tingmu.workspaces import WorkspaceCatalog
from test_qml_workspace import find, quick as quick


def trigger(window, name):
    assert QMetaObject.invokeMethod(find(window, name), "trigger")


def test_interface_menu_retranslates_windows_and_preserves_the_study_session(quick, qtbot):
    app, bridge, engine, window = quick
    cue = Cue("one", 0, 2, "开始队列", translation="新对话", explanation="重试")
    bridge._set_document(StudyDocument("example.mkv", LanguagePair(), [cue]))
    bridge._sync_cue(100)
    bridge._set_state(status="已收藏「开始队列」")
    pair = bridge._settings.language
    session = bridge.activeSessionId
    models = find(window, "modelManagerWindow")
    models.show()
    setup = find(window, "workspaceSetup")
    assert QMetaObject.invokeMethod(setup, "open")
    trigger(window, "interfaceEnglish")
    qtbot.waitUntil(lambda: models.title() == "Tingmu · Global text models")
    assert setup.property("opened") and setup.property("title") == "Study workspace"
    assert find(window, "interfaceLanguageMenu").property("title") == "Interface language"
    assert find(window, "learningLanguage").property("label") == "Language/dialect to study"
    assert find(window, "workspaceTextTab").property("text") == "Text models and API"
    assert find(window, "queueStartButton").property("text") == "Start queue"
    assert find(window, "applicationStatus").property("text") == "Saved “开始队列”"
    assert app._tingmu_tray._open.text() == "Open Tingmu"
    assert find(window, "workspaceInterfaceLanguage").property("currentText") == "English"
    trigger(window, "interfaceTraditional")
    assert setup.property("title") == "學習工作區"
    assert find(window, "learningLanguage").property("label") == "想學哪種語言/方言"
    assert find(window, "queueStartButton").property("text") == "開始佇列"
    assert models.title() == "聽幕 · 全域文字模型"
    assert any(o.property("text") == "確定" for o in window.findChildren(QObject) if o.inherits("QQuickButton"))
    assert bridge.activeCue["text"] == cue.text and bridge.activeCue["translation"] == cue.translation
    assert bridge.activeCue["explanation"] == cue.explanation
    assert bridge.activeSessionId == session and bridge._settings.language == pair
    trigger(window, "interfaceSimplified")
    assert find(window, "fileMenu").property("title") == "文件"
    assert setup.property("title") == "学习工作区"
    assert find(window, "learningLanguage").property("label") == "想学哪种语言/方言"
    assert not engine._tingmu_warnings


def test_selected_language_survives_a_new_process_and_workspace(quick):
    _, bridge, _, window = quick
    root = bridge.store.data_dir
    catalog = WorkspaceCatalog(root)
    item_id = catalog.create("观看", "ja", "en")
    trigger(window, "interfaceEnglish")
    catalog.select(item_id)
    script = """
import sys
from pathlib import Path
from PySide6.QtCore import QObject
from tingmu.quick.main import create_qml_app
app, bridge, engine = create_qml_app(Path(sys.argv[1]))
window = engine.rootObjects()[0]
assert window.findChild(QObject, "fileMenu").property("title") == "File"
assert window.title().startswith("观看")
assert bridge._settings.language.source == "ja" and bridge._settings.language.target == "en"
assert not engine._tingmu_warnings
bridge.shutdown()
window.hide()
"""
    subprocess.run([sys.executable, "-c", script, str(root)], check=True, capture_output=True, timeout=25,
                   env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    assert not (catalog.directory(item_id) / "interface-language.json").exists()


def test_switching_language_does_not_reset_dropdown_selections(quick, qtbot):
    _, bridge, engine, window = quick
    bridge.setting("theme", "graphite")
    bridge.setting("companion_reasoning_effort", "high")
    bridge.setting("asr_backend", "local_api")
    theme = find(window, "themeChoice")
    reasoning = find(window, "chatReasoningChoice")
    speech = find(window, "asrBackendChoice")
    for action, theme_label, reasoning_label, speech_label in (
        ("interfaceEnglish", "Graphite", "High", "Local API · Local speech service"),
        ("interfaceTraditional", "石墨", "高", "本機 API · 本機語音服務"),
        ("interfaceSimplified", "石墨", "高", "本机 API · 本机语音服务"),
    ):
        trigger(window, action)
        for choice, index, label in ((theme, 3, theme_label), (reasoning, 5, reasoning_label), (speech, 1, speech_label)):
            assert choice.property("currentIndex") == index
            assert choice.property("displayText") == label
            assert choice.property("contentItem").property("text") == label
        popup = find(theme, "choicePopup")
        assert QMetaObject.invokeMethod(popup, "open")
        content = popup.property("contentItem").property("contentItem")
        qtbot.waitUntil(lambda: any(item.property("index") == 3 for item in content.childItems()))
        assert next(item for item in content.childItems() if item.property("index") == 3).property("text") == theme_label
        assert QMetaObject.invokeMethod(popup, "close")
    assert bridge.settings["theme"] == "graphite" and bridge.settings["companion_reasoning_effort"] == "high"
    assert bridge.settings["asr_backend"] == "local_api"
    assert not engine._tingmu_warnings


def test_interface_catalogs_cover_controls_and_keep_evidence_filenames(tmp_path):
    base = Path(localization.__file__).parent
    english, traditional = (json.loads((base / "i18n" / f"{code}.json").read_text("utf-8")) for code in ("en", "zh-TW"))
    assert english.keys() == traditional.keys()
    for path in (base / "qml").glob("*.qml"):
        for literal in re.findall(r'qsTranslate\("Tingmu",\s*("(?:[^"\\]|\\.)*")\)', path.read_text("utf-8")):
            assert json.loads(literal) in english, (path.name, literal)
    language = localization.InterfaceLanguage(tmp_path)
    filename = "大语言模型.srt"
    evidence = filename + "：文本语种证据不足"
    assert language.translate("TingmuEvidence", evidence) == filename + "：无法确定字幕的语言/方言"
    assert language.translate("TingmuStatus", "大语言模型") is None
    assert language.select("en")
    assert language.translate("TingmuEvidence", evidence) == filename + ": Insufficient evidence for the subtitle language/dialect"
    assert language.translate("TingmuEvidence", "开始队列.srt：剧名不符") == "开始队列.srt: Series title mismatch"
    assert language.translate("TingmuEvidence", "内嵌字幕轨 3：图像字幕，需要 OCR") == "Embedded subtitle track 3: Image subtitles require OCR"
    assert language.translate("TingmuEvidence", "指定字幕：集数不符（视频 S05E15，字幕 S05E05）") == (
        "Selected subtitles: Episode mismatch (video S05E15, subtitles S05E05)")
    assert language.translate("TingmuStatus", "字幕已导出：C:/学习/新对话.srt") == "Subtitles exported to C:/学习/新对话.srt"
    assert language.translate("TingmuStatus", "Unknown provider diagnostic") is None
    failure = "原字幕已保存。请在工作区启用逐句讲解模型，允许其使用本机或指定的远程文本服务后重试。"
    suffix = " 已完成步骤已保存，重试会继续处理。"
    for code, catalog in (("en", english), ("zh-TW", traditional)):
        assert language.select(code)
        assert language.translate("TingmuStatus", failure + suffix) == catalog["%1" + suffix].replace("%1", catalog[failure])
        # A provider can return arbitrary details. The suffix alone belongs to Tingmu.
        assert language.translate("TingmuStatus", "错误代码 Z-23" + suffix) == (
            catalog["%1" + suffix].replace("%1", "错误代码 Z-23"))
