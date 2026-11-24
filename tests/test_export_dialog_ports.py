"""Export naming and overwrite decisions use only the injected dialog port."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QInputDialog, QMessageBox

from tingmu.quick.bridge import Bridge
from tingmu.storage import Store


@pytest.fixture
def export_bridge(qapp, tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Export bypassed the injected native dialog port")

    monkeypatch.setattr(QInputDialog, "getText", unexpected)
    monkeypatch.setattr(QMessageBox, "question", unexpected)
    dialogs = SimpleNamespace(getText=Mock(return_value=("命名字幕.srt", True)),
                              question=Mock(return_value=QMessageBox.Yes))
    bridge = Bridge(Store(tmp_path / "study"), dialogs=dialogs)
    try:
        yield bridge, dialogs
    finally:
        bridge.shutdown()


def test_export_name_uses_injected_dialog_and_preserves_default(export_bridge):
    bridge, dialogs = export_bridge
    destination = bridge._export_destination(None, "默认字幕.srt", (".srt",))
    assert destination == bridge.store.data_dir / "exports" / "命名字幕.srt"
    assert dialogs.getText.call_count == 1
    assert dialogs.getText.call_args.kwargs == {"text": "默认字幕.srt"}
    dialogs.question.assert_not_called()
    assert not destination.exists()


@pytest.mark.parametrize("answer", [("取消的文件.srt", False), ("  ", True)])
def test_export_cancel_or_blank_name_does_not_request_overwrite(export_bridge, answer):
    bridge, dialogs = export_bridge
    dialogs.getText.return_value = answer
    assert bridge._export_destination(None, "默认字幕.srt", (".srt",)) is None
    dialogs.question.assert_not_called()
    assert not (bridge.store.data_dir / "exports").exists()


@pytest.mark.parametrize("answer,accepted", [(QMessageBox.Yes, True), (QMessageBox.No, False)])
def test_export_overwrite_uses_injected_answer_without_touching_existing_file(export_bridge, answer, accepted):
    bridge, dialogs = export_bridge
    destination = bridge.store.data_dir / "exports" / "命名字幕.srt"
    destination.parent.mkdir()
    destination.write_text("existing subtitle", encoding="utf-8")
    dialogs.question.return_value = answer
    assert bridge._export_destination(None, "默认字幕.srt", (".srt",)) == (destination if accepted else None)
    assert dialogs.question.call_count == 1
    assert destination.name in dialogs.question.call_args.args[2]
    assert destination.read_text(encoding="utf-8") == "existing subtitle"


def test_export_dialog_keeps_filename_validation(export_bridge):
    bridge, dialogs = export_bridge
    dialogs.getText.return_value = ("../outside.srt", True)
    with pytest.raises(ValueError, match="请输入文件名"):
        bridge._export_destination(None, "默认字幕.srt", (".srt",))
    dialogs.question.assert_not_called()


def test_provided_export_path_needs_no_dialog(export_bridge):
    bridge, dialogs = export_bridge
    assert bridge._export_destination("provided.srt", "默认字幕.srt", (".srt",)) == (
        bridge.store.data_dir / "exports" / "provided.srt")
    dialogs.getText.assert_not_called()
    dialogs.question.assert_not_called()
