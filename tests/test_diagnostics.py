import json

from tingmu.diagnostics import AppLog, redact_text, safe_fields, _known_secrets


def test_logs_redact_environment_secrets_and_omit_learning_content(tmp_path, monkeypatch):
    secret = "synthetic-sensitive-key-value-1234"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    _known_secrets.cache_clear()
    log = AppLog(tmp_path, developer=True)
    try:
        log.event("test", "测试 " + secret, "DEBUG", text="PRIVATE SUBTITLE", note="PRIVATE NOTE",
                  request={"messages": "PRIVATE CHAT"}, diagnostic=secret, bearer="Bearer token-value-9876")
        data = log.path.read_text("utf-8")
        assert secret not in data
        assert "PRIVATE" not in data
        assert "token-value-9876" not in data
        row = log.read()[0]
        assert row["level"] == "DEBUG"
        assert "[OMITTED]" in row["details"]
        assert "[REDACTED]" in row["message"]
    finally:
        log.close()
        _known_secrets.cache_clear()


def test_rotation_bounds_disk_and_debug_requires_developer_mode(tmp_path):
    log = AppLog(tmp_path, max_bytes=600)
    log.event("hidden", "debug", "DEBUG")
    assert log.read() == []
    log.set_developer(True)
    for i in range(40):
        log.event("action", "操作" * 10, "DEBUG", sequence=i)
    assert len(list(log.directory.glob("events.jsonl*"))) <= 4
    assert sum(p.stat().st_size for p in log.directory.glob("events.jsonl*")) < 4 * 600
    assert json.loads(log.path.read_text("utf-8").splitlines()[-1])["fields"]["sequence"] == 39
    log.close()


def test_malformed_or_nonobject_log_rows_do_not_break_viewer(tmp_path):
    log = AppLog(tmp_path)
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write('[]\n"text"\nnull\n{broken\n')
    log.event("healthy", "正常记录")
    assert [row["event"] for row in log.read()] == ["healthy"]
    log.close()
    log.event("ignored", "closed")  # Safe if an uncancellable socket returns after shutdown.


def test_exception_log_has_stack_locations_without_raw_error_payload(tmp_path):
    log = AppLog(tmp_path)
    try:
        raise ValueError("PRIVATE REQUEST BODY")
    except ValueError as error:
        log.exception(error)
    payload = log.path.read_text("utf-8")
    assert "PRIVATE REQUEST BODY" not in payload
    assert "ValueError" in payload and "test_diagnostics.py" in payload
    assert "api_key=abcd123456" not in redact_text("api_key=abcd123456")
    assert safe_fields({"content": "private", "status": "ready"}) == {"content": "[OMITTED]", "status": "ready"}
    log.close()
