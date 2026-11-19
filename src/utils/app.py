"""Main download flow: fetch anime page -> choose source -> resolve play pages -> download episodes in parallel. 下载主流程：抓取番剧页 -> 选择源 -> 解析播放页 -> 线程池并行下载。"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError, ThreadPoolExecutor

from playwright.sync_api import sync_playwright

from .config import (
    BASE_URL,
    DEFAULT_WORKERS,
    HEADERS,
    REQUEST_DELAY,
)
from .console import (
    DownloadCancelled,
    STOP_EVENT,
    ensure_running,
    log,
    request_stop,
    set_progress,
)
from .download import download_video
from .net import fetch_html
from .parse import (
    order_sources,
    parse_episodes,
    parse_sources,
    select_source,
    with_source,
)
from .progress import MultiProgress
from .resolver import VideoResolver


def _safe_save_dir(base_dir: str, folder_name: str) -> str:
    base = os.path.abspath(base_dir)
    save_dir = os.path.abspath(os.path.join(base, folder_name))
    if os.path.normcase(os.path.commonpath([base, save_dir])) != os.path.normcase(base):
        raise ValueError(f"Output directory must be within the current working directory. 保存目录必须位于当前工作目录内: {folder_name}")
    return save_dir


def _build_tasks(
    episodes: list[dict], save_dir: str, folder_name: str, chosen: dict
) -> tuple[list[dict], int]:
    """Build download tasks; skip targets that already exist (MP4 or TS when ffmpeg is unavailable). 生成待下载任务；目标文件（mp4 或无 ffmpeg 时的 ts）已存在则跳过。"""
    tasks: list[dict] = []
    skipped = 0
    total = len(episodes)
    for idx, episode in enumerate(episodes, start=1):
        output_path = os.path.join(save_dir, f"{folder_name}-{idx}.mp4")
        existing_file = next(
            (
                path for path in (output_path, os.path.splitext(output_path)[0] + ".ts")
                if os.path.isfile(path) and os.path.getsize(path) > 0
            ),
            None,
        )
        if existing_file:
            skipped += 1
            continue
        episode = dict(episode)
        episode["url"] = with_source(episode["url"], chosen["code"])
        tasks.append({"idx": idx, "total": total, "ep": episode, "output": output_path})
    return tasks, skipped


def _episode_id(task: dict) -> int:
    play_id = task["ep"].get("play_id")
    return play_id if isinstance(play_id, int) else task["idx"]


def run(
    anime_id: str,
    folder_name: str,
    source: str | None = None,
    workers: int = DEFAULT_WORKERS,
    headful: bool = False,
    base_dir: str | None = None,
    input_fn: Callable[[str], str] | None = None,
) -> int:
    """Run a complete download and return the process exit code. 执行一次完整下载，返回进程退出码。"""
    workers = max(1, workers)
    base = os.path.abspath(base_dir or os.getcwd())
    try:
        save_dir = _safe_save_dir(base, folder_name)
    except ValueError as exc:
        log(f"[!]{exc}")
        return 1
    os.makedirs(save_dir, exist_ok=True)

    page_url = f"{BASE_URL}/anime/{anime_id}"
    html = fetch_html(page_url)
    ensure_running()
    episodes = parse_episodes(html, anime_id)
    if not episodes:
        log("[!] No episodes were found; check that the anime ID is correct. 没有解析到任何剧集，请检查番剧 ID 是否正确。")
        return 1
    sources = parse_sources(html)
    if not sources:
        # If page data has no source list, infer sources from the ?source= parameter in episode links. 页面数据里没有源列表时，退化为从剧集链接的 ?source= 参数推断
        codes: list[str] = []
        for episode in episodes:
            code = episode.get("source")
            if code and code not in codes:
                codes.append(code)
        sources = [
            {"code": code, "name": code, "episode_count": len(episodes)} for code in codes
        ]
        if sources:
            log("[!] No source list found in page data; inferred sources from episode links. 页面数据中没有源列表，已根据剧集链接推断下载源")

    chosen = select_source(
        sources, episodes[0].get("source"), source, len(episodes), input_fn=input_fn
    )
    ensure_running()
    source_order = order_sources(sources, chosen)

    tasks, skipped = _build_tasks(episodes, save_dir, folder_name, chosen)
    if not tasks:
        log(f"[*] All episodes have already been downloaded; nothing to do. Directory: {save_dir} 所有剧集均已下载，无需处理。目录: {save_dir}")
        return 0

    progress = MultiProgress([])
    set_progress(progress)
    log("Downloading. Press q or Ctrl+C to stop. 下载中，按 q 或 Ctrl+C 停止。")

    succeeded = 0
    cancelled = 0
    failed: list[dict] = []
    futures: dict = {}
    episode_ids = {_episode_id(task) for task in tasks}
    submitted_episode_ids: set[int] = set()
    count_lock = threading.Lock()

    def _finish(future) -> None:
        """Summarize results as soon as a download thread ends (callback runs in the download thread). 下载线程结束时立即汇总结果（回调在下载线程里执行）。"""
        nonlocal succeeded, cancelled
        task = futures.get(future)
        if task is None:
            return
        key = os.path.basename(task["output"])
        try:
            future.result()
        except CancelledError:
            with count_lock:
                cancelled += 1
            progress.fail(key, cancelled=True)
        except DownloadCancelled:
            with count_lock:
                cancelled += 1
            progress.fail(key, cancelled=True)
            log(f"[×] Stopped: episode {task['idx']}. 已停止: 第{task['idx']}集")
        except Exception as exc:
            progress.fail(key)
            with count_lock:
                failed.append(task)
            log(f"[!] Episode {task['idx']} download failed: {exc} 第{task['idx']}集下载失败: {exc}")
        else:
            with count_lock:
                succeeded += 1
            progress.complete(key)

    pool = ThreadPoolExecutor(max_workers=workers)
    progress_started = False
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not headful)
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                extra_http_headers={"Referer": HEADERS["Referer"]},
                viewport={"width": 1280, "height": 800},
            )
            resolver = VideoResolver(context)
            try:
                resolver.warmup()

                for task in tasks:
                    if STOP_EVENT.is_set():
                        break
                    episode_id = _episode_id(task)
                    if episode_id in submitted_episode_ids:
                        skipped += 1
                        log(f"[*] Episode {task['idx']}: download already submitted; skipping duplicate task. 第 {task['idx']} 集：下载请求已提交，跳过重复任务")
                        continue
                    episode = task["ep"]
                    resolved: tuple[str, str] | None = None
                    for candidate in source_order:
                        if STOP_EVENT.is_set():
                            break
                        play_url = with_source(episode["url"], candidate["code"])
                        video_src = resolver.resolve(play_url)
                        if video_src:
                            resolved = (video_src, play_url)
                            break
                    if STOP_EVENT.is_set():
                        break
                    if not resolved:
                        log(f"[!] Episode {task['idx']} could not be resolved from any source; skipping. 第 {task['idx']} 集：所有下载源均解析失败，跳过")
                        with count_lock:
                            failed.append(task)
                        continue

                    video_src, play_url = resolved
                    key = os.path.basename(task["output"])
                    progress.add_task(key, f"Episode {task['idx']} (第{task['idx']}集)")
                    if not progress_started:
                        progress.start()
                        progress_started = True
                    future = pool.submit(
                        download_video,
                        video_src,
                        task["output"],
                        play_url,
                        f"Episode {task['idx']} (第{task['idx']}集)",
                        progress,
                    )
                    futures[future] = task
                    submitted_episode_ids.add(episode_id)
                    future.add_done_callback(_finish)
                    time.sleep(REQUEST_DELAY)
            finally:
                resolver.close()
                context.close()
                browser.close()
    except KeyboardInterrupt:
        request_stop("Ctrl+C")
    finally:
        try:
            if STOP_EVENT.is_set():
                for future in futures:
                    future.cancel()
                pool.shutdown(wait=True, cancel_futures=True)
            else:
                pool.shutdown(wait=True)
        except KeyboardInterrupt:
            request_stop("Ctrl+C")
            for future in futures:
                future.cancel()
            pool.shutdown(wait=True, cancel_futures=True)
        finally:
            if STOP_EVENT.is_set():
                with count_lock:
                    cancelled += len(episode_ids - submitted_episode_ids)
            progress.stop(keep=not STOP_EVENT.is_set())
            set_progress(None)

    stopped = STOP_EVENT.is_set()
    log(f"[*] Succeeded: {succeeded}, skipped: {skipped}, failed: {len(failed)}, cancelled: {cancelled}. 成功 {succeeded} 集，跳过 {skipped} 集，失败 {len(failed)} 集，取消 {cancelled} 集")
    if failed:
        log("[!] Failed episodes (失败列表):")
        for task in failed:
            log(f"- Episode {task['idx']}: {os.path.basename(task['output'])} (第 {task['idx']} 集)")

    if stopped:
        log(f"[!] Download stopped; incomplete episodes can be resumed the next time you run the program. Directory: {save_dir} 下载已停止，未完成的剧集下次运行会继续。目录: {save_dir}")
        return 130
    if failed:
        log(f"[*] Files are located at: {save_dir} 文件位于: {save_dir}")
        return 1

    log(f"[*] All done. Files are located at: {save_dir} 全部处理完毕，文件位于: {save_dir}")
    return 0
