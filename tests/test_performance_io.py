"""Regression checks for bounded local I/O and compute paths."""

from pathlib import Path
import threading
from unittest.mock import patch

import numpy as np
import pytest

from tingmu import diagnostics, embedded_subtitles, speech


def test_disabled_log_skips_redaction_and_cached_reads_follow_appends(tmp_path, monkeypatch):
    log = diagnostics.AppLog(tmp_path)
    try:
        with monkeypatch.context() as patch:
            def unexpected(*args):
                raise AssertionError("disabled event formatted a message")
            patch.setattr(diagnostics, "redact_text", unexpected)
            log.event("detail", "suppressed", "DEBUG")
        log.event("first", "one")
        rows = log.read()
        rows[0]["message"] = "modified"
        with monkeypatch.context() as patch:
            patch.setattr(diagnostics.json, "loads", unexpected)
            assert log.read(limit=1)[0]["message"] == "one"
        log.event("second", "two", "ERROR")
        assert [row["message"] for row in log.read()] == ["one", "two"]
    finally:
        log.close()


@pytest.mark.parametrize("width,hop", [(512, 128), (25, 6), (8, 11)])
def test_overlap_add_retains_frame_order_and_window_normalization(width, hop):
    rng = np.random.default_rng(921)
    frames = rng.normal(size=(27, width)).astype("f")
    window = np.hanning(width).astype("f")
    signal = np.zeros((len(frames) - 1) * hop + width, np.float32)
    weight = np.zeros_like(signal)
    for index, frame in enumerate(frames):
        cut = slice(index * hop, index * hop + width)
        signal[cut] += frame
        weight[cut] += window * window
    np.testing.assert_array_equal(speech._overlap_add(frames, hop, window), signal / np.maximum(weight, 1e-8))


def test_bitmap_frame_urls_are_loaded_on_demand_with_bounded_reuse(tmp_path, monkeypatch):
    from PySide6.QtGui import QColor, QImage
    from tempfile import TemporaryDirectory

    monkeypatch.setattr(embedded_subtitles, "_scratch", lambda: TemporaryDirectory(dir=tmp_path))
    monkeypatch.setattr(embedded_subtitles, "_binary", lambda name: name)

    def export(args, folder, *unused, **kwargs):
        for stamp in range(6):
            image = QImage(32, 16, QImage.Format.Format_RGBA8888)
            image.fill(QColor("white"))
            assert image.save(str(Path(folder) / f"{stamp:012}.png"))

    monkeypatch.setattr(embedded_subtitles, "_run", export)
    encoder = embedded_subtitles.base64.b64encode
    with patch.object(embedded_subtitles.base64, "b64encode", wraps=encoder) as encode:
        page = embedded_subtitles.load_page("fixture", 0, embedded_subtitles.BitmapTrack(32, 16, (0,)),
                                            0, threading.Event())
        try:
            assert not encode.called
            for stamp in range(6):
                assert page.at(stamp).url.startswith("data:image/png;base64,")
            assert encode.call_count == 6
            page.at(4)
            page.at(0)
            assert encode.call_count == 7
        finally:
            page.close()
    assert not list(tmp_path.iterdir())


def test_moving_partial_checkpoint_preserves_last_published_subtitles(tmp_path, monkeypatch):
    from tingmu.models import Cue, LanguagePair, StudyDocument
    from tingmu.storage import Store
    from tingmu.translation_review import TRANSLATION_REVIEW_VERSION, lesson_digest
    from test_portable_paths import portable_store

    root, store = portable_store(tmp_path, monkeypatch)
    movie = root[0] / "clip.mkv"
    movie.write_bytes(b"synthetic video")
    language = LanguagePair()
    cue = Cue("one", 0, 1, "first", translation="translation", explanation="fixture")
    review = {"version": TRANSLATION_REVIEW_VERSION, "provided": {cue.id: lesson_digest(cue, [])}}
    doc = StudyDocument(str(movie), language, [cue], metadata={"stage": "ready", "translation_review": review})
    path = store.save_document(doc)
    suffixes = (".srt", ".translation.srt")
    published = [path.with_suffix(ext).read_bytes() for ext in suffixes]
    doc.cues[0].text = "pending"
    doc.cues[0].translation = ""
    doc.metadata["stage"] = "teaching"
    store.save_document(doc)
    store.close()
    moved = tmp_path / "Moved"
    root[0].rename(moved)
    root[0] = moved
    restored = Store(moved / "data")
    try:
        movie = str(moved / "clip.mkv")
        assert restored.load_document(movie, language).cues[0].text == "pending"
        path, _ = restored.material_paths(movie, language)
        assert [path.with_suffix(ext).read_bytes() for ext in suffixes] == published
    finally:
        restored.close()
