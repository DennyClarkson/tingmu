"""Public UI entrypoint; the shipped interface is Qt Quick."""

from tingmu.quick.main import run_app


def __getattr__(name):
    if name == "MainWindow":
        # Kept for existing integration checks and explicit legacy development use.
        from .app import MainWindow
        return MainWindow
    raise AttributeError(name)

__all__ = ["MainWindow", "run_app"]
