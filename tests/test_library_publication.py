"""A usable asynchronous summary remains publishable when its cache key changes."""

import threading

from test_domain_events import bridge as bridge
from tingmu.models import Cue, LanguagePair, StudyDocument
from tingmu.storage import atomic_json


def test_document_replaced_during_read_publishes_uncached_summary(bridge, qtbot, tmp_path, monkeypatch):
    media = tmp_path / "replaced-during-read.mkv"
    media.write_bytes(b"synthetic")
    document = StudyDocument(str(media), LanguagePair(), [Cue("first", 0, 1, "原先一句")])
    material = bridge.store.save_document(document)
    bridge.catalog.add([media])
    original = bridge.store.load_document
    read_threads = []

    def replace_then_read(path, language):
        read_threads.append(threading.get_ident())
        # The cache has already captured the old stat. Publish a new authoritative
        # document before its read, changing size as well as the atomic file identity.
        document.cues.append(Cue("second", 1, 2, "读取期间发布的第二句"))
        atomic_json(material, document.to_dict())
        return original(path, language)

    monkeypatch.setattr(bridge.store, "load_document", replace_then_read)
    bridge._refresh_library()
    qtbot.waitUntil(lambda: bridge._library_future is None and bridge._library_pending is None)

    assert len(read_threads) == 1 and read_threads[0] != threading.get_ident()
    assert bridge._library_cache.peek(str(media), document.language) is None
    assert len(bridge.library) == 1
    assert bridge.library[0]["path"] == str(media)
    assert bridge.library[0]["status"] == "partial"
    assert bridge.library[0]["cueCount"] == 2
