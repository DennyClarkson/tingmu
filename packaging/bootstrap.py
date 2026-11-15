"""Entry point used by the native windowed launcher; no source checkout needed."""
import json
from pathlib import Path
import sys
import traceback

from bootstrap_logging import RedactingLog

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "data" / "logs"
LOGS.mkdir(parents=True, exist_ok=True)
# pythonw has no standard streams. Keep SDK output local and available on failure.
log = RedactingLog(LOGS / "application.log")
sys.stdout = log
sys.stderr = log


def main():
    from qml_runtime import configure_qml_environment
    configure_qml_environment(ROOT)
    from tingmu.diagnostics import redact_text
    log.redactor = redact_text
    if sys.platform == "win32":
        import ctypes
        try:
            set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
            set_app_id.argtypes = [ctypes.c_wchar_p]
            set_app_id.restype = ctypes.c_long
            result = set_app_id("Tingmu.Portable.3")
            if result < 0:
                raise OSError(f"SetCurrentProcessExplicitAppUserModelID failed: {result:#x}")
        except OSError:
            # Taskbar identity is cosmetic; a Windows shell failure must not block playback.
            pass
    if "--portable-smoke" in sys.argv:
        from portable_smoke import run_smoke
        media = None
        if "--smoke-media" in sys.argv:
            index = sys.argv.index("--smoke-media")
            media = Path(sys.argv[index + 1]).resolve(strict=True)
        return run_smoke(ROOT, media)
    from tingmu.cli import main as run
    return run()


try:
    raise SystemExit(main())
except Exception as error:
    # Source stack and exception type diagnose startup without logging an SDK
    # exception body, which might include transcript text or request contents.
    print("Application startup failed:", file=log)
    traceback.print_tb(error.__traceback__, file=log)
    print(type(error).__name__, file=log)
    log.flush()
    if "--portable-smoke" in sys.argv:
        (LOGS / "portable-smoke.json").write_text(json.dumps({
            "ready": False, "frontend": "Qt Quick/QML", "error": type(error).__name__,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, "听幕启动失败，详情见 data/logs/application.log。", "听幕", 16)
    raise SystemExit(1)
