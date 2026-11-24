from copy import deepcopy
from dataclasses import asdict
import json

import pytest
from PySide6.QtCore import QObject

from tingmu.app_actions import ActionContext, ActionError
from tingmu.corrections import freeze_target, read_target, revise_target
from tingmu.models import Cue, LanguagePair, StudyDocument, Token
from tingmu.storage import Store
from test_qml_v4 import find, invoke, quick as quick


def material(store, folder, ready=True):
    media = folder / "external.mkv"
    media.touch()
    source = folder / "external.srt"
    source.write_bytes(b"original subtitle")
    cue = Cue("one", 0, 5, "好啦上車", "raw ASR", translation="上车", explanation="旧讲解",
              segmented_texts=["好啦", "上車"], speaker="说话人 1", uncertain=True)
    doc = StudyDocument(str(media.resolve()), LanguagePair(), [cue, Cue("next", 5, 8, "另一句")],
                        metadata={"raw_display_text": {"one": "raw ASR"}, "review_records": {"one": {"reason": "旧复核"}}})
    if ready:
        doc.metadata.update(stage="ready", subtitle_path=str(source))
    store.save_document(doc)
    return doc


def binding(doc):
    return ActionContext("chat", doc.media_path, doc.language,
                         correction=freeze_target(doc, doc.cues[0], "one:part:2"),
                         guidance="我确认这里说的是下车。", model="test-model")


def update(target):
    return {"revision": target["revision"], "selected_text": "落車", "translation": "好啦，下车。",
            "explanation": "「落車」表示从车上下来。", "tokens": [
                asdict(Token("好啦", "hou2 laa1", "好啦")), asdict(Token("落車", "lok6 ce1", "下车", True))]}


@pytest.mark.parametrize("ready", [True, False])
def test_correct_split_material_and_backup_without_touching_raw_or_input(tmp_path, ready):
    store = Store(tmp_path / "study")
    doc = material(store, tmp_path, ready)
    previous = deepcopy(doc)
    path, srt = store.material_paths(doc.media_path, doc.language)
    raw = json.loads(path.read_bytes())
    raw["cues"].reverse()
    raw["cues"][0]["extra_alignment"] = [{"offset": 1.2}]
    raw["cues"][1]["legacy_evidence"] = "keep"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    assert srt.exists() == ready
    ctx = binding(doc)
    observed = read_target(doc, ctx.correction)
    assert (observed["selected_text"], observed["source_prefix"], observed["speaker"]) == ("上車", "好啦", "说话人 1")
    revise_target(store, ctx, update(ctx.correction), "traditional", None)
    saved = Store(store.data_dir).load_document(doc.media_path, doc.language)
    changed = saved.cues[0]
    assert changed.segmented_texts == ["好啦", "落車"]
    assert (changed.start, changed.end, changed.id, changed.original_text, changed.speaker) == (0, 5, "one", "raw ASR", "说话人 1")
    assert changed.uncertain and changed.text == "好啦落車" and changed.tokens[-1].reading == "lok6 ce1"
    assert changed.translation == update(ctx.correction)["translation"] and changed.explanation == update(ctx.correction)["explanation"]
    assert saved.metadata["cue_corrections"]["one"]["guidance"] == ctx.guidance
    assert saved.metadata["review_records"] == previous.metadata["review_records"]
    assert saved.cues[1] == previous.cues[1] and saved.metadata["raw_display_text"] == previous.metadata["raw_display_text"]
    assert "落車" in srt.read_text("utf-8") and "上車" not in srt.read_text("utf-8")
    assert (tmp_path / "external.srt").read_bytes() == b"original subtitle"
    patched = json.loads(path.read_bytes())
    assert patched["cues"][0] == raw["cues"][0] and patched["cues"][1]["legacy_evidence"] == "keep"
    assert [old.read_bytes() for old in (store.data_dir / "material-backups").glob("*/*.json")] == [before]
    with pytest.raises(ActionError, match="已变化"):
        revise_target(store, ctx, update(ctx.correction), "traditional", None)
    with pytest.raises(ValueError, match="逐句修订"):
        store.save_document(previous)
    assert store.reset_material(doc.media_path, doc.language, "teaching")
    assert "落車" in srt.read_text("utf-8") and store.load_document(doc.media_path, doc.language).cues[0].explanation == ""
    assert store.reset_material(doc.media_path, doc.language, "all")
    assert not srt.exists() and not path.exists()


def test_invalid_tokens_cancel_and_interrupted_publication_restore(tmp_path, monkeypatch):
    from tingmu.providers import RequestCancelled
    from tingmu import storage
    store = Store(tmp_path / "study")
    doc = material(store, tmp_path)
    ctx = binding(doc)
    path, srt = store.material_paths(doc.media_path, doc.language)
    baseline = (path.read_bytes(), srt.read_bytes())
    args = update(ctx.correction)
    with pytest.raises(ActionError):
        revise_target(store, ctx, {**args, "tokens": args["tokens"][:1]}, "traditional", None)
    with pytest.raises(RequestCancelled):
        revise_target(store, ctx, args, "traditional", lambda: True)
    write = storage.atomic_json

    def fail(destination, value):
        if destination == path:
            raise OSError("simulated failure")
        write(destination, value)
    monkeypatch.setattr(storage, "atomic_json", fail)
    with pytest.raises(OSError):
        revise_target(store, ctx, args, "traditional", None)
    assert baseline == (path.read_bytes(), srt.read_bytes())
    srt.write_text("interrupted", encoding="utf-8")
    path.with_suffix(".pending").touch()
    assert store.load_document(doc.media_path, doc.language).cues == doc.cues
    assert baseline == (path.read_bytes(), srt.read_bytes())


@pytest.mark.parametrize("surface", ["fullscreen", "companion", "list"])
def test_guidance_freezes_playback_and_dispatches_only_revision_tools(quick, qtbot, tmp_path, monkeypatch, surface):
    from tingmu.companion import CompanionAgent
    _, bridge, engine, window = quick
    doc = material(bridge.store, tmp_path)
    bridge.media_path = doc.media_path
    bridge._set_document(doc)
    bridge._sync_cue(3800)
    bridge.timer.stop()
    bridge._set_state(mediaPath=doc.media_path)
    player = find(window, "playerModule").property("loadedItem")
    if surface == "list":
        content = find(window, "subtitleModule").property("loadedItem")
        subtitle_list = find(content, "subtitleList")
        qtbot.wait(80)
        rows = [item for item in subtitle_list.property("contentItem").childItems() if item.property("index") == 1]
        assert rows, [(child, child.property("index")) for child in subtitle_list.property("contentItem").childItems()]
        invoke(find(rows[0], "correctSubtitleRow"), "clicked")
    else:
        if surface == "fullscreen":
            invoke(player, "toggleFullscreen")
            qtbot.wait(80)
            content = find(find(window, "companionOverlay"), "overlayScroll")
        else:
            content = find(window, "companionModule").property("loadedItem")
        invoke(find(content, "correctCompanionSubtitle"), "clicked")
    dialog = find(window, "subtitleCorrectionDialog")
    assert len(window.findChildren(QObject, "subtitleCorrectionDialog")) == 1
    assert dialog.property("parent").window() == content.window()
    assert dialog.property("opened")
    assert find(dialog, "subtitleCorrectionGuidance").window() == content.window()
    find(dialog, "subtitleCorrectionGuidance").setProperty("text", "这里说的是下车。")
    bridge._sync_cue(5700)

    def reply(_agent, text, history, language, cue, context, *, tools, tool_executor, on_tool_event, **unused):
        assert cue.id == "one:part:2" and "下车" in text
        assert {t["function"]["name"] for t in tools} == {"read_current_subtitle", "revise_current_subtitle"}
        args = update(tool_executor("read_current_subtitle", {}))
        result = tool_executor("revise_current_subtitle", args)
        on_tool_event({"name": "revise_current_subtitle", "label": "保存字幕与讲解", "status": "success", "result": result})
        return "已更新所选字幕。"
    monkeypatch.setattr(CompanionAgent, "reply", reply)
    invoke(find(dialog, "submitSubtitleCorrection"), "clicked")
    qtbot.waitUntil(lambda: bool(dialog.property("messageId")) or bool(dialog.property("localError")))
    assert dialog.property("messageId"), dialog.property("localError")
    qtbot.waitUntil(lambda: not bridge.state["chatBusy"], timeout=5000)
    assert dialog.property("saved"), bridge.messages[-1]
    assert bridge.document.cues[0].text == "好啦落車" and bridge.document.cues[1].text == "另一句"
    assert not engine._tingmu_warnings
    invoke(dialog, "close")
    if surface == "fullscreen":
        invoke(player, "toggleFullscreen")


def test_regular_chat_can_revise_lesson_only_after_read(quick, qtbot, tmp_path, monkeypatch):
    from tingmu.companion import CompanionAgent
    _, bridge, _, _ = quick
    doc = material(bridge.store, tmp_path)
    bridge.media_path = doc.media_path
    bridge._activate_context_session()
    bridge._set_document(doc)
    bridge._sync_cue(3800)
    prior = deepcopy(doc.cues[0])
    frozen = bridge._frozen_subtitle("one:part:2")

    def reply(_agent, text, history, language, cue, context, *, tools, tool_executor, **unused):
        assert len(tools) > 2 and cue.id == frozen["display_id"]
        args = {"revision": frozen["revision"], "selected_text": "上車", "translation": "好啦，上车。",
                "explanation": "「上車」表示登上車輛。"}
        with pytest.raises(ActionError, match="先读取"):
            tool_executor("revise_current_subtitle", args)
        assert tool_executor("read_current_subtitle", {})["revision"] == frozen["revision"]
        assert tool_executor("revise_current_subtitle", args)["saved"]
        with pytest.raises(ActionError, match="没有要求加入收藏箱"):
            tool_executor("collect_current_sentence", {"note": ""})
        return "已保存本句讲解。"
    monkeypatch.setattr(CompanionAgent, "reply", reply)
    bridge._v4_send_chat("请修改当前句的讲解，解释「上車」指登上车辆，保留字幕。")
    qtbot.waitUntil(lambda: not bridge.state["chatBusy"])
    assert bridge.messages[-1]["status"] == "complete"
    edited = bridge.document.cues[0]
    assert (edited.text, edited.segmented_texts, edited.tokens) == (prior.text, prior.segmented_texts, prior.tokens)
    assert edited.explanation == "「上車」表示登上车辆。"
