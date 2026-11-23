import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from zipfile import ZipFile

import pytest

from tingmu import qwen_support, wheel_support, whisper_support, workspace_models as models


@pytest.fixture
def wheel(tmp_path, monkeypatch):
    assets = {"nagisa_v001.dict": b"dict", "nagisa_v001.hp": b"hp", "nagisa_v001.model": b"weights"}
    archive = io.BytesIO()
    with ZipFile(archive, "w") as package:
        for name, content in assets.items():
            package.writestr("nagisa/data/" + name, content)
    contents = archive.getvalue()
    monkeypatch.setattr(wheel_support, "cache_root", lambda: tmp_path)
    support = wheel_support.WheelSupport(
        "fixture", "fixture-cache", "https://example.invalid/data.whl", len(contents),
        hashlib.sha256(contents).hexdigest(), {
            "nagisa/data/" + name: (len(content), hashlib.sha256(content).hexdigest())
            for name, content in assets.items()
        })
    return contents, support


def test_shared_download_is_verified_and_repairable(wheel, monkeypatch):
    calls = []
    content, support = wheel

    def fetch(url, timeout):
        calls.append(url)
        return io.BytesIO(content)

    monkeypatch.setattr(wheel_support, "urlopen", fetch)
    with ThreadPoolExecutor(2) as workers:
        futures = [workers.submit(support.install, lambda _: None, lambda: False) for _ in range(2)]
        for future in futures:
            future.result()
    assert support.inventory()["complete"]
    assert calls == [support.wheel_url]
    support.path("nagisa_v001.model").write_bytes(b"changed")
    assert not support.inventory()["complete"]
    support.install(lambda _: None, lambda: False)
    assert support.inventory()["complete"]
    assert len(calls) == 2


@pytest.mark.parametrize("cancel", [True, False])
def test_cancel_or_failed_integrity_does_not_publish(wheel, monkeypatch, cancel):
    seen = []
    content, support = wheel
    monkeypatch.setattr(wheel_support, "urlopen", lambda *_, **__: io.BytesIO(content[:-1]))
    with pytest.raises(InterruptedError if cancel else ValueError):
        support.install(seen.append, lambda: cancel and bool(seen))
    assert not support.inventory()["complete"]
    assert not support.path().exists()


def test_clean_portable_adds_support_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "cache_root", lambda: tmp_path)
    monkeypatch.setattr(wheel_support, "cache_root", lambda: tmp_path)
    monkeypatch.setattr(models, "is_clean_install", lambda: False)
    assert len(models.model_inventory("qwen3-asr-0.6b")["repositories"]) == 2
    assert len(models.model_inventory("small")["repositories"]) == 1
    monkeypatch.setattr(models, "is_clean_install", lambda: True)
    assert models.model_inventory("qwen3-asr-0.6b")["repositories"][-1] == qwen_support.inventory()
    assert models.model_inventory("small")["repositories"][-1] == whisper_support.inventory()
