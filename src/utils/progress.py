"""Multi-line terminal progress display: one row per episode, refreshed with progress and speed. 终端多行进度显示：每一集一行，各自刷新进度与网速。"""

from __future__ import annotations

import os
import sys
import threading
import time
import unicodedata

# Global console output lock: logs and the progress display are mutually exclusive. 控制台输出的全局锁：日志与进度区块互斥
PRINT_LOCK = threading.RLock()

BAR_WIDTH = 16


def _display_width(text: str) -> int:
    """Calculate terminal display width (CJK/full-width characters count as two columns). 终端显示宽度（中文/全角字符按 2 格计算）。"""
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text)


def _fits(text: str, encoding: str) -> bool:
    try:
        text.encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _format_speed(bytes_per_second: float) -> str:
    mb = bytes_per_second / 1024 / 1024
    if mb < 1000:
        return f"{mb:.1f} MB/s"
    return f"{mb / 1024:.2f} GB/s"


class TaskBar:
    def __init__(self, key: str, label: str) -> None:
        self.key = key
        self.label = label
        self.fraction = 0.0
        self.bytes = 0
        self.speed = 0.0
        self.started = False
        self.done = False
        self.failed = False
        self.cancelled = False
        self._last_bytes = 0
        self._last_time = time.monotonic()


class MultiProgress:
    def __init__(self, rows: list[tuple[str, str]]) -> None:
        self._rows = [TaskBar(key, label) for key, label in rows]
        self._by_key = {row.key: row for row in self._rows}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._drawn = 0
        env = os.environ.get("XIFAN_PROGRESS")
        self.enabled = (env != "0") if env is not None else sys.stdout.isatty()
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        if _fits("█░", encoding):
            self._full, self._empty = "█", "░"
        elif _fits("█", encoding):
            self._full, self._empty = "█", " "
        else:
            self._full, self._empty = "#", "-"
        self._label_width = max(
            (_display_width(row.label) for row in self._rows), default=0
        )

    def start_task(self, key: str) -> None:
        with self._lock:
            row = self._by_key.get(key)
            if row is not None:
                row.started = True
                row._last_bytes = row.bytes
                row._last_time = time.monotonic()

    def add_task(self, key: str, label: str) -> None:
        with self._lock:
            if key in self._by_key:
                return
            row = TaskBar(key, label)
            self._rows.append(row)
            self._by_key[key] = row
            self._label_width = max(self._label_width, _display_width(label))
            started = self._thread is not None
        if started:
            self._draw()

    def add_bytes(self, key: str, count: int) -> None:
        with self._lock:
            row = self._by_key.get(key)
            if row is not None:
                row.bytes += count

    def update(self, key: str, fraction: float) -> None:
        with self._lock:
            row = self._by_key.get(key)
            if row is not None:
                row.fraction = max(row.fraction, min(max(fraction, 0.0), 1.0))

    def complete(self, key: str) -> None:
        with self._lock:
            row = self._by_key.get(key)
            if row is not None:
                row.fraction = 1.0
                row.done = True

    def fail(self, key: str, cancelled: bool = False) -> None:
        with self._lock:
            row = self._by_key.get(key)
            if row is not None:
                row.failed = not cancelled
                row.cancelled = cancelled

    def start(self) -> None:
        if not self.enabled or not self._rows or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._render_loop, name="progress", daemon=True)
        self._thread.start()
        self._draw()

    def _render_loop(self) -> None:
        while not self._stop.wait(0.4):
            self._draw()

    def _draw(self) -> None:
        with PRINT_LOCK:
            if self._stop.is_set() or not self.enabled:
                return
            with self._lock:
                self._tick_locked()
                lines = [self._line_locked(row) for row in self._rows]
            self._paint_locked(lines)

    def _tick_locked(self) -> None:
        now = time.monotonic()
        for row in self._rows:
            if not row.started or row.done or row.failed or row.cancelled:
                row._last_bytes = row.bytes
                row._last_time = now
                continue
            elapsed = max(now - row._last_time, 1e-6)
            if elapsed < 0.05:
                row._last_bytes = row.bytes
                row._last_time = now
                continue
            instant = (row.bytes - row._last_bytes) / elapsed
            row.speed = instant if row.speed <= 0 else row.speed * 0.7 + instant * 0.3
            row._last_bytes = row.bytes
            row._last_time = now

    def _line_locked(self, row: TaskBar) -> str:
        pad = " " * max(self._label_width - _display_width(row.label), 0)
        filled = int(round(min(max(row.fraction, 0.0), 1.0) * BAR_WIDTH))
        bar = self._full * filled + self._empty * (BAR_WIDTH - filled)
        percent = min(max(row.fraction, 0.0), 1.0) * 100
        if row.done:
            tail = "100.0%  Done (完成)"
        elif row.cancelled:
            tail = f"{percent:5.1f}%  Stopped (已停止)"
        elif row.failed:
            tail = f"{percent:5.1f}%  Failed (失败)"
        elif not row.started:
            tail = "Waiting (等待中)"
        else:
            tail = f"{percent:5.1f}%  {_format_speed(row.speed):>10}"
        return f"{row.label}{pad} [{bar}] {tail}"

    def _paint_locked(self, lines: list[str]) -> None:
        height = len(lines)
        try:
            if self._drawn:
                if self._drawn > 1:
                    sys.stdout.write(f"\x1b[{self._drawn - 1}A")
                sys.stdout.write("\r")
            for index, line in enumerate(lines):
                sys.stdout.write("\x1b[2K" + line)
                if index < height - 1:
                    sys.stdout.write("\n")
            sys.stdout.flush()
        except (OSError, ValueError):
            return
        self._drawn = height

    def erase_locked(self) -> None:
        if self._drawn <= 0:
            return
        try:
            if self._drawn > 1:
                sys.stdout.write(f"\x1b[{self._drawn - 1}A")
            sys.stdout.write("\r")
            for index in range(self._drawn):
                sys.stdout.write("\x1b[2K")
                if index < self._drawn - 1:
                    sys.stdout.write("\n")
            if self._drawn > 1:
                sys.stdout.write(f"\x1b[{self._drawn - 1}A\r")
            else:
                sys.stdout.write("\r")
            sys.stdout.flush()
        except (OSError, ValueError):
            pass
        finally:
            self._drawn = 0

    def stop(self, keep: bool = False) -> None:
        # Draw once more before stopping so the last completed episodes show 100%. 结束前再画一次，确保最后完成的那几集显示为100%
        if self.enabled and not self._stop.is_set():
            self._draw()
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with PRINT_LOCK:
            if keep and self.enabled and self._drawn > 0:
                try:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                except (OSError, ValueError):
                    pass
                self._drawn = 0
            else:
                self.erase_locked()
