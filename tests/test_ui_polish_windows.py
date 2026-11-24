"""Exercise the live QML scene in every supported palette, language and window size."""
import os
from pathlib import Path

import pytest
from PySide6.QtGui import QColor
from PySide6.QtQml import QQmlEngine, QQmlExpression

from tingmu.models import Cue, LanguagePair, StudyDocument, Token
from test_qml_workspace import find, invoke, quick as quick


PALETTES = {"forest": "#101315", "paper": "#f5f1e8", "mist": "#e9f0ef", "graphite": "#17191d"}
LANGUAGES = {"zh-CN": "interfaceSimplified", "zh-TW": "interfaceTraditional", "en": "interfaceEnglish"}


def test_scroll_thumb_only_visible_for_overflow(quick):
    _, _, engine, window = quick
    bar = find(find(window, "workspaceSetup"), "viewportScrollBar")
    thumb = bar.property("contentItem")
    for size in (0.0, 1.0, 0.9995):
        assert bar.setProperty("size", 0.5)
        assert thumb.opacity() > 0
        assert bar.setProperty("size", size)
        assert thumb.opacity() == 0
    assert engine._tingmu_warnings == []


def luminance(color):
    def linear(value):
        return value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
    return sum(weight * linear(channel) for weight, channel in
               zip((.2126, .7152, .0722), (color.redF(), color.greenF(), color.blueF())))


@pytest.mark.parametrize("theme", PALETTES)
def test_palette_text_contrast(quick, theme):
    _, bridge, engine, window = quick
    bridge.setting("theme", theme)
    context = QQmlEngine.contextForObject(window)
    pairs = {"onAccent": ["accent", "accentHover", "accentPressed"],
             "text": ["bg", "panel", "raised", "surfaceElevated", "tooltipBg", "hover", "pressed"],
             "muted": ["bg", "panel", "raised", "surfaceElevated"],
             "accent": ["panel", "accentSoft"], "warning": ["warningSoft"],
             "organizedWord": ["organizedSoft"], "danger": ["dangerSoft", "dangerHover", "dangerPressed"]}
    colors = {}
    for token in set(pairs).union(*pairs.values()):
        expression = QQmlExpression(context, window, "Theme." + token)
        color, undefined = expression.evaluate()
        assert not undefined and not expression.hasError() and isinstance(color, QColor), token
        colors[token] = luminance(color)
        assert color.isValid(), (theme, token)
    for ink, surfaces in pairs.items():
        for surface in surfaces:
            dark, light = sorted((colors[ink], colors[surface]))
            assert (light + .05) / (dark + .05) >= 4.5, (theme, ink, surface)
    assert engine._tingmu_warnings == []


def populate(bridge):
    # These paths are display metadata only. No decoder, download or model is started.
    cue = Cue("tea", 1, 4, "得閒一齊飲茶。", translation="有空一起喝茶。",
              explanation="得閒指有空，一齊指一起。整句常用來邀請朋友喝茶。",
              tokens=[Token("得閒", "dak1 haan4", "有空"), Token("飲茶", "jam2 caa4", "喝茶")])
    name = "茶樓的一天.mp4"
    for token in cue.tokens:
        bridge.store.save_word(name, cue, token, LanguagePair())
    bridge.command("saveModel", {"id": "", "label": "本机讲解", "provider": "ollama", "protocol": "openai",
                                 "base_url": "http://127.0.0.1:11434/v1", "model": "local-study",
                                 "key_source": "none", "max_tokens": 4096})
    return StudyDocument(name, LanguagePair(), [cue, Cue("meet", 5, 9, "聽朝見。", translation="明早见。")])


def publish_lists(bridge, name):
    bridge._publish_library_rows([
        {"path": name, "name": name, "parent": "粤语会话", "status": "ready"},
        {"path": "海旁散步.mp4", "name": "海旁散步.mp4", "parent": "粤语会话", "status": "paused"},
    ])
    bridge._jobs = [{"id": "tea", "name": name, "status": "completed", "message": "已完成",
                     "progress": 1.0, "error": "", "settings": {"preparation_workflow": "subtitle_agent"}},
                    {"id": "walk", "name": "海旁散步.mp4", "status": "paused", "message": "已暂停",
                     "progress": 0.35, "error": ""}]
    bridge.jobsChanged.emit()
    bridge._messages = [
        {"id": "q", "role": "user", "content": "「得閒」可以怎样用？", "status": "complete"},
        {"id": "a", "role": "assistant", "content": "**得閒** 指有空。约朋友喝茶时，可以这样说：\n\n得閒一齊飲茶。",
         "status": "complete"},
    ]
    bridge.messagesChanged.emit()


@pytest.mark.parametrize("theme", PALETTES)
@pytest.mark.parametrize("width,height", [(1480, 930), (980, 620)])
def test_theme_language_window_matrix(quick, qtbot, theme, width, height):
    app, bridge, engine, window = quick
    if os.environ.get("TINGMU_POLISH_SCREENSHOTS") == "1":
        assert app.platformName() == "windows", "Visual acceptance must capture native Windows QQuickWindows"
    window.resize(width, height)
    bridge.timer.stop()
    document = populate(bridge)
    bridge.setting("theme", theme)
    bridge.setting("developer_mode", True)
    setup = find(window, "workspaceSetup")
    models = find(window, "modelManagerWindow")
    dev = find(window, "developerWindow")
    learn = find(window, "learningWorkspace")
    chat = find(window, "chatModule")
    invoke(setup, "close")
    assert window.color() == QColor(PALETTES[theme])

    assert (window.width(), window.height()) == (width, height)
    for language, action in LANGUAGES.items():
        invoke(find(window, action), "trigger")

        def check(name, target=window):
            qtbot.wait(12)
            assert target.isVisible(), name
            assert engine._tingmu_warnings == [], (theme, language, width, name, engine._tingmu_warnings)
            capture = os.environ.get("TINGMU_POLISH_SCREENSHOTS") == "1"
            detailed = theme in {"forest", "paper"} and (
                (width == 1480 and language == "zh-CN") or (width == 980 and language == "en")
            )
            sample = theme in {"mist", "graphite"} and width == 1480 and language == "zh-TW"
            core = {"watch", "subtitles", "prepare", "learn", "chat", "workspace", "models", "review"}
            if capture and ((detailed and name in core) or (
                (sample or (detailed and width == 1480)) and name == "fullscreen"
            ) or (sample and name in {"subtitles", "chat"})):
                qtbot.wait(100)
                folder = Path(__file__).resolve().parents[1] / ".cache/qa/ui-polish"
                folder.mkdir(exist_ok=True, parents=True)
                picture = target.grabWindow()
                assert not picture.isNull() and picture.save(str(folder / f"{theme}-{width}-{language}-{name}.png"))

        for menu_name in ("fileMenu", "playbackMenu", "workspaceMenu"):
            menu = find(window, menu_name)
            invoke(menu, "open")
            check(menu_name)
            if menu_name == "workspaceMenu":
                submenu = find(window, "interfaceLanguageMenu")
                invoke(submenu, "open")
                check("interface language menu")
                invoke(submenu, "close")
            invoke(menu, "close")
        choices = find(find(window, "themeChoice"), "choicePopup")
        invoke(choices, "open")
        check("themes")
        invoke(choices, "close")

        for view in ("watch", "prepare", "learn"):
            invoke(window, "changeView", view)
            publish_lists(bridge, document.media_path)
            if view == "learn":
                learn.setProperty("activeTab", "collections")
            check(view)
            if view == "watch":
                bridge._set_state(mediaPath=document.media_path, mediaName=document.media_path)
                bridge._set_document(document)
                bridge._sync_cue(1500)
                check("subtitles")
                bridge._set_document(None)
                bridge._set_state(mediaPath="", mediaName="")
        for tab in ("notes", "review"):
            learn.setProperty("activeTab", tab)
            if tab == "review":
                assert learn.property("practiceMode")
                learn.setProperty("showAnswer", True)
            check(tab)
        invoke(window, "changeView", "watch")
        chat.setProperty("detached", True)
        chat_panel = chat.property("loadedItem")
        qtbot.wait(20)
        chat_window = chat_panel.window()
        chat_window.resize(min(920, width - 48), min(760, height - 48))
        assert chat_window.color() == window.color()
        chat_panel.setProperty("followTail", False)
        invoke(find(chat_panel, "chatHistory"), "positionViewAtBeginning")
        check("chat", chat_window)
        chat.setProperty("detached", False)

        invoke(setup, "open")
        check("workspace")
        bar = find(setup, "viewportScrollBar")
        owner = bar.parentItem()
        assert bar.x() == pytest.approx(owner.width() - bar.width())
        assert bar.height() == pytest.approx(owner.property("availableHeight"))

        for section in ("models", "text"):
            setup.setProperty("section", section)
            check("workspace " + section)
        invoke(setup, "close")
        invoke(models, "open")
        invoke(models, "populate", bridge.modelProfiles[0])
        models.resize(min(840, width - 48), min(720, height - 36))
        assert window.color() == models.color()
        check("models", models)
        models.setProperty("showRoles", True)
        check("roles", models)
        models.setProperty("showRoles", False)
        models.hide()
        invoke(dev, "open")
        for page in (0, 1):
            dev.setProperty("page", page)
            check("developer", dev)
        dev.hide()

        # Visit each settings panel in its real host, including detached modules.
        for module in ("player", "companion", "subtitle", "library", "queue", "chat"):
            dock = find(window, module + "Module")
            dock.setProperty("detached", True)
            panel = dock.property("loadedItem")
            invoke(panel, "openSettings")
            check(module + " settings", panel.window())
            popup = find(panel, "playerSettingsPopup" if module == "player" else "moduleSettingsPopup")
            assert popup.property("opened"), module
            invoke(popup, "close")
            dock.setProperty("detached", False)

        # Locate named popups in their actual hosts. Opening one can destroy transient
        # controls (e.g. a tooltip), so never retain a snapshot of the whole QObject tree.
        def visit_dialogs(host, names):
            for name in names:
                dialog = find(host, name)
                invoke(dialog, "open")
                qtbot.waitUntil(lambda: dialog.property("opened"))
                assert engine._tingmu_warnings == [], (theme, language, name)
                invoke(dialog, "close")

        visit_dialogs(window, ("helpDialog", "aboutDialog", "teachingPreferencesDialog", "subtitleSourceDialog",
                               "subtitleCorrectionDialog", "activeModelDownload", "agentModelDownload", "workspaceTextConsent"))
        invoke(setup, "open")
        visit_dialogs(setup, ("manualModelDownload",))
        invoke(setup, "close")
        invoke(window, "changeView", "learn")
        visit_dialogs(learn, ("collectionNoteDialog", "deleteCollectionConfirmation", "savedNotebookDialog", "discardNotebookDialog"))
        invoke(window, "changeView", "prepare")
        visit_dialogs(window, ("queueResetConfirmation",))
        invoke(window, "changeView", "watch")
        chat.setProperty("detached", True)
        visit_dialogs(chat_panel, ("renameChatDialog", "deleteChatDialog"))
        chat.setProperty("detached", False)
        models.show()
        visit_dialogs(models, ("deleteModelDialog",))
        models.hide()
        player = find(window, "playerModule").property("loadedItem")
        bridge._set_state(mediaPath=document.media_path, mediaName=document.media_path)
        bridge._set_document(document)
        bridge._sync_cue(1500)
        invoke(player, "toggleFullscreen")
        check("fullscreen", find(window, "fullscreenWindow"))
        invoke(player, "toggleFullscreen")
        bridge._set_document(None)
        bridge._set_state(mediaPath="", mediaName="")
        assert engine._tingmu_warnings == []
