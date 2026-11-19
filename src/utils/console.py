"""Terminal output, stop controls, and global runtime state. 终端输出、停止控制与全局运行状态。"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

from .progress import PRINT_LOCK, MultiProgress

# Global progress display (set when app starts). 全局进度区块（由 app 启动时设置）
PROGRESS: MultiProgress | None = None

STOP_EVENT = threading.Event()
STOP_WORDS = {"q", "quit", "stop", "exit", "s"}
_STOP_LOCK = threading.Lock()
_ACTIVE_RESPONSE_LOCK = threading.Lock()


class _ClosableResponse(Protocol):
    def close(self) -> None: ...


_ACTIVE_RESPONSES: set[_ClosableResponse] = set()


class DownloadCancelled(Exception):
    """Raised when the user stops a download; this exception is not retried. 用户主动停止下载时抛出，不参与重试。"""


def set_progress(progress: MultiProgress | None) -> None:
    global PROGRESS
    PROGRESS = progress


def log(message: str = "") -> None:
    """Thread-safe printing; erase the progress display first to keep logs separate from the progress bar. 线程安全的打印；有进度区块时先擦掉再打印，避免日志和进度条混在一起。"""
    with PRINT_LOCK:
        if PROGRESS is not None:
            PROGRESS.erase_locked()
        print(message, flush=True)


def request_stop(reason: str) -> None:
    with _STOP_LOCK:
        if STOP_EVENT.is_set():
            return
        STOP_EVENT.set()
    with _ACTIVE_RESPONSE_LOCK:
        responses = tuple(_ACTIVE_RESPONSES)
    for response in responses:
        response.close()
    if PROGRESS is not None:
        PROGRESS.stop()
    log(f"[!] Stopping ({reason}); waiting for downloads in progress to finish... (Press Ctrl+C again to force quit.) 正在停止（{reason}），等待进行中的下载收尾…（再按一次 Ctrl+C 可强制退出）")


@contextmanager
def cancellable_response(response: _ClosableResponse) -> Iterator[_ClosableResponse]:
    with _ACTIVE_RESPONSE_LOCK:
        if STOP_EVENT.is_set():
            response.close()
            raise DownloadCancelled("Stopped (已停止)")
        _ACTIVE_RESPONSES.add(response)
    try:
        yield response
    finally:
        with _ACTIVE_RESPONSE_LOCK:
            _ACTIVE_RESPONSES.discard(response)


def ensure_running() -> None:
    if STOP_EVENT.is_set():
        raise DownloadCancelled("Stopped (已停止)")


