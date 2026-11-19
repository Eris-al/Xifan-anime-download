"""Anime downloader for Xifan Video (next.xifanacg.com) — command-line entry point. 稀饭视频（next.xifanacg.com）番剧下载工具 —— 命令行入口。

Features: 功能：
  1. Automatically reads all sources on an anime page and lets you choose one before downloading (source=xfxf1 / AL / CS ...). 自动读取番剧页面上的全部播放源，下载前可选择源（source=xfxf1 / AL / CS ...）；
  2. Skips existing files; direct MP4 downloads can resume from .part files. 目标文件已存在时跳过；mp4 直链支持 .part 断点续传；
  3. Automatically downloads m3u8 segments from HLS sources such as CS and remuxes them to MP4 with ffmpeg. CS 等 HLS 源自动下载 m3u8 分片，并用 ffmpeg 转封装为 mp4；
  4. Press Ctrl+C to stop; temporary files already written are kept for resuming. 下载中按 Ctrl+C 停止；已写入的临时文件保留以便续传。

Usage: 用法：
  python src/main.py <anime_id> "<anime_name>" -s 2 -j 4
  -s accepts a number (1/2/3) or source code (xfxf1/AL/CS); other sources are automatically retried as backups if the selected source fails. -s 支持序号（1/2/3）或 source 代码（xfxf1/AL/CS）；未指定的源会作为备用源自动重试。
  -j sets the number of parallel downloads (default: 3); direct MP4 downloads support resuming from .part files. -j 指定并行下载数，默认 3；mp4 直链支持 .part 断点续传。
  HLS remuxing requires ffmpeg (on PATH, or specified with the FFMPEG environment variable); without it, files are saved as .ts. HLS 转封装依赖 ffmpeg（PATH 中，或用环境变量 FFMPEG 指定路径）；找不到时保存为 .ts。
  Stop with Ctrl+C (ongoing HTTP requests will be closed if possible; press Ctrl+C again to force exit). 下载中停止：Ctrl+C（正在进行的 HTTP 请求会尝试立即关闭；再次 Ctrl+C 可强制结束进程）。
  The progress bar is enabled only in terminals (TTY) by default; it is disabled when output is redirected to a file. Use XIFAN_PROGRESS=0/1 to force it off/on. 进度条默认只在终端（TTY）启用；重定向到文件时自动关闭，可用 XIFAN_PROGRESS=0/1 强制关/开。
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Queue

from utils.app import run
from utils.config import DEFAULT_WORKERS
from utils.console import DownloadCancelled, STOP_EVENT, STOP_WORDS, log, request_stop

try:  # Avoid crashing when a non-UTF-8 console (such as GBK) cannot render a character. GBK 等非 UTF-8 控制台遇到画不出的字符时不要直接崩溃
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, OSError):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Xifan Video anime downloader (稀饭视频番剧下载器)")
    parser.add_argument("anime_id", nargs="?", help="Anime ID (番剧ID)")
    parser.add_argument("folder", nargs="?", help="Output folder name (保存文件夹名称)")
    parser.add_argument("-s", "--source", help="Download source number (1/2/3) or code (e.g. xfxf1/AL/CS) (下载源序号（1/2/3）或 source 代码（如 xfxf1/AL/CS）)")
    parser.add_argument("-j", "--workers", type=int, default=DEFAULT_WORKERS, help="Number of parallel downloads; default: 3 (并行下载数，默认 3)")
    parser.add_argument("--headful", action="store_true", help="Show the browser window for debugging (显示浏览器窗口（调试用）)")
    return parser.parse_args(argv)


def run_with_controls(args: argparse.Namespace, anime_id: str, folder_name: str) -> int:
    STOP_EVENT.clear()
    input_lines: Queue[str | None | Exception] = Queue()
    prompts: Queue[tuple[str, Queue[str | None]]] = Queue()
    interrupt_received = threading.Event()
    interrupt_count = 0

    def request_input(prompt: str) -> str:
        if STOP_EVENT.is_set():
            raise EOFError
        replies: Queue[str | None] = Queue(maxsize=1)
        prompts.put((prompt, replies))
        answer = replies.get()
        if answer is None:
            raise EOFError
        return answer

    def read_input() -> None:
        while True:
            try:
                line = sys.stdin.readline()
            except (OSError, ValueError) as exc:
                input_lines.put(exc)
                return
            if not line:
                input_lines.put(None)
                return
            input_lines.put(line.rstrip("\r\n"))

    def handle_sigint(signum, frame) -> None:
        nonlocal interrupt_count
        interrupt_count += 1
        if interrupt_count > 1:
            os._exit(130)
        interrupt_received.set()

    previous_sigint_handler = signal.signal(signal.SIGINT, handle_sigint)
    input_thread = threading.Thread(target=read_input, name="command-input", daemon=True)
    try:
        input_thread.start()
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="download-runner") as executor:
            future = executor.submit(
                run,
                anime_id,
                folder_name,
                source=args.source,
                workers=max(1, args.workers),
                headful=args.headful,
                input_fn=request_input,
            )
            active_prompt: tuple[str, Queue[str | None]] | None = None
            input_closed = False
            input_error: Exception | None = None

            while not future.done():
                if interrupt_received.is_set():
                    interrupt_received.clear()
                    request_stop("Ctrl+C")
                    if active_prompt is not None:
                        active_prompt[1].put(None)
                        active_prompt = None

                if active_prompt is None:
                    try:
                        active_prompt = prompts.get_nowait()
                    except Empty:
                        pass
                    else:
                        if input_closed or STOP_EVENT.is_set():
                            active_prompt[1].put(None)
                            active_prompt = None
                        else:
                            print(active_prompt[0], end="", flush=True)

                try:
                    line = input_lines.get(timeout=0.1)
                except Empty:
                    continue

                if isinstance(line, Exception):
                    request_stop("Terminal input failed (终端输入失败)")
                    if active_prompt is not None:
                        active_prompt[1].put(None)
                        active_prompt = None
                    input_closed = True
                    input_error = line
                elif line is None:
                    input_closed = True
                    if active_prompt is not None:
                        active_prompt[1].put(None)
                        active_prompt = None
                elif active_prompt is not None:
                    if line.strip().lower() in STOP_WORDS:
                        request_stop(f"Terminal input (终端输入) {line.strip()!r}")
                        active_prompt[1].put(None)
                    else:
                        active_prompt[1].put(line)
                    active_prompt = None
                elif line.strip().lower() in STOP_WORDS:
                    request_stop(f"Terminal input (终端输入) {line.strip()!r}")

            if input_error is not None:
                raise input_error
            try:
                return future.result()
            except DownloadCancelled:
                if STOP_EVENT.is_set():
                    return 130
                raise
    finally:
        signal.signal(signal.SIGINT, previous_sigint_handler)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    anime_id = (args.anime_id or input("Enter anime ID (请输入番剧ID): ")).strip()
    folder_name = (args.folder or input("Enter output folder name (请输入保存文件夹名称): ")).strip()
    if not anime_id or not folder_name:
        log("[!] Anime ID and folder name cannot be empty. 番剧ID和文件夹名不能为空。")
        return 1

    return run_with_controls(args, anime_id, folder_name)


if __name__ == "__main__":
    sys.exit(main())
