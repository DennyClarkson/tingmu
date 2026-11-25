"""Task domain state and explicit UI/service ports."""

from dataclasses import dataclass, field
from typing import Any, Callable
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from .state import task_domain


@dataclass
class TaskState:
    workers: dict = field(default_factory=dict)
    finishing_workers: list = field(default_factory=list)
    callbacks: dict = field(default_factory=dict)
    closed: bool = False


@dataclass(frozen=True)
class TaskPorts:
    clear_chat_worker: Callable
    completed: Any
    error: Callable
    log: Any
    operation_domain: Callable
    progress: Any
    refresh_jobs: Callable
    set_operation_domain: Callable
    set_state: Callable
    state_snapshot: Callable
    workersChanged: Any


class BackgroundTaskManager:
    EXCLUSIVE_GROUPS = ({"queue", "import"}, {"chat", "summary"})

    def __init__(self, state: TaskState, ports: TaskPorts):
        self.state = state
        self.ports = ports
        self._pools = {}
        self._futures = set()
        self._future_lock = threading.Lock()

    def _start_job(self, key, function, completed):
        if self.state.closed:
            raise ValueError("应用已经关闭，不能开始后台任务")
        if key in self.state.workers:
            raise ValueError("该任务正在进行")
        if any(key in group and group.intersection(self.state.workers) for group in self.EXCLUSIVE_GROUPS):
            raise ValueError("相关任务正在进行，请等待完成后重试")
        cancelled = threading.Event()
        self.state.callbacks[key] = completed
        started = time.monotonic()
        self.ports.log.event("worker_start", "后台任务开始", task=key)

        def work():
            try:
                result = function(lambda event: self.ports.progress.emit(key, event), cancelled.is_set)
                self.ports.log.event(
                    "worker_done",
                    "后台任务结束",
                    task=key,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    cancelled=cancelled.is_set(),
                )
                if not self.state.closed:
                    self.ports.completed.emit(key, result, "")
            except Exception as exc:
                self.ports.log.exception(exc, "worker_error")
                if not self.state.closed:
                    self.ports.completed.emit(key, None, f"{type(exc).__name__}: {exc}")

        thread = threading.Thread(target=work, name=f"tingmu-{key}", daemon=True)
        self.state.workers[key] = (thread, cancelled)
        if key == "model-download":
            self.ports.set_state(downloadProgress={}, downloadMessage="正在检查模型文件")
        self.ports.set_state(
            domain=task_domain(key),
            busy=True,
            queueRunning="queue" in self.state.workers,
            modelDownloading="model-download" in self.state.workers,
            chatBusy=bool({"chat", "summary"} & self.state.workers.keys()),
            learningBusy="notebook" in self.state.workers,
            modelBusy="models" in self.state.workers,
            error="",
        )
        thread.start()
        self.ports.workersChanged.emit()

    def _job_progress(self, key, text):
        if not self.state.closed:
            if key == "model-download":
                worker = self.state.workers.get(key)
                if worker is not None and worker[1].is_set():
                    return
                self.ports.set_state(
                    domain="download",
                    status=str(text),
                    downloadMessage=str(text),
                    downloadProgress=getattr(text, "details", {}),
                )
            else:
                self.ports.set_state(domain=task_domain(key), status=str(text))

    def _job_completed(self, key, value, error):
        if self.state.closed:
            return
        finished = self.state.workers.pop(key, None)
        self.state.finishing_workers = [item for item in self.state.finishing_workers if item[0].is_alive()]
        if finished and finished[0] is not None and finished[0].is_alive():
            self.state.finishing_workers.append(finished)
        if key == "chat":
            self.ports.clear_chat_worker()
        callback = self.state.callbacks.pop(key, None)
        self.ports.set_state(
            busy=bool(self.state.workers),
            queueRunning="queue" in self.state.workers,
            modelDownloading="model-download" in self.state.workers,
            chatBusy=bool({"chat", "summary"} & self.state.workers.keys()),
            chatStopping=False if key == "chat" else self.ports.state_snapshot().get("chatStopping", False),
            learningBusy="notebook" in self.state.workers,
            modelBusy="models" in self.state.workers,
            streaming="chat" in self.state.workers,
        )
        previous_domain = self.ports.operation_domain()
        self.ports.set_operation_domain(task_domain(key))
        try:
            if error:
                self.ports.error(error)
            elif callback:
                try:
                    callback(value)
                except Exception as exc:
                    self.ports.error(exc)
            self.ports.refresh_jobs()
        finally:
            self.ports.set_operation_domain(previous_domain)
            self.ports.workersChanged.emit()

    def cancel(self, key):
        worker = self.state.workers.get(key)
        if worker is None:
            return False
        worker[1].set()
        if key == "model-download":
            previous = self.ports.state_snapshot().get("downloadProgress") or {}
            self.ports.set_state(domain="download", downloadMessage="正在取消…",
                                 downloadProgress={**previous, "stage": "cancelling", "rate": 0, "eta": None})
        return True

    def register_pool(self, name, executor):
        if name in self._pools:
            raise ValueError(f"后台执行池已注册：{name}")
        pool = ManagedPool(self, name, executor)
        self._pools[name] = pool
        return pool

    def pool(self, name, max_workers=1):
        if name not in self._pools:
            self.register_pool(
                name, ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"tingmu-{name}")
            )
        return self._pools[name]

    def _register_future(self, future):
        with self._future_lock:
            self._futures.add(future)
        future.add_done_callback(self._future_done)
        return future

    def _future_done(self, future):
        with self._future_lock:
            self._futures.discard(future)

    @property
    def background_count(self):
        with self._future_lock:
            return len(self._futures)

    def shutdown(self, timeout=3.5):
        self.state.closed = True
        workers = list(self.state.workers.values()) + self.state.finishing_workers
        for _, cancelled in workers:
            cancelled.set()
        for pool in self._pools.values():
            pool.shutdown(wait=True, cancel_futures=True)
        deadline = time.monotonic() + timeout
        for thread, _ in workers:
            thread.join(max(0, deadline - time.monotonic()))
        return sum(thread.is_alive() for thread, _ in workers)

    def close_when_finished(self, close):
        """Keep storage valid for a cancelled transport still draining on exit."""
        workers = list(self.state.workers.values()) + self.state.finishing_workers
        live = [thread for thread, _ in workers if thread.is_alive()]
        if not live:
            close()
            return

        def finish():
            for thread in live:
                thread.join()
            close()

        threading.Thread(target=finish, name="tingmu-storage-close", daemon=True).start()


class ManagedPool:
    """An executor handle whose futures and shutdown belong to the task manager."""

    def __init__(self, manager, name, executor):
        self.manager = manager
        self.name = name
        self.executor = executor

    def submit(self, function, *args, **kwargs):
        if self.manager.state.closed:
            raise ValueError("应用已经关闭，不能开始后台任务")
        return self.manager._register_future(self.executor.submit(function, *args, **kwargs))

    def shutdown(self, wait=True, *, cancel_futures=False):
        self.executor.shutdown(wait=wait, cancel_futures=cancel_futures)
