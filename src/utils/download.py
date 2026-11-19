"""Video downloads: direct MP4 links (resumable) and HLS (segment downloads + ffmpeg remuxing). 视频下载：mp4 直链（断点续传）与 HLS（分片下载 + ffmpeg 转封装）。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from urllib.parse import urljoin, urlsplit

import requests

from .config import CHUNK_SIZE, DOWNLOAD_READ_TIMEOUT, MAX_RETRIES
from .console import (
    DownloadCancelled,
    STOP_EVENT,
    cancellable_response,
    ensure_running,
    log,
)
from .net import fetch_bytes, fetch_text, request_headers
from .progress import MultiProgress


def is_hls_url(url: str) -> bool:
    """Check whether a URL is an HLS (m3u8) playlist. 判断地址是否为 HLS（m3u8）播放列表。"""
    parts = urlsplit(url)
    return parts.path.lower().endswith(".m3u8") or ".m3u8" in parts.query.lower()


def _parse_m3u8(text: str) -> dict:
    """Parse m3u8: master playlists return variants; media playlists return segments. 解析 m3u8：master 返回清晰度列表，media 返回分片列表。"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    variants: list[tuple[int, str]] = []
    pending_bandwidth: int | None = None
    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF"):
            match = re.search(r"BANDWIDTH=(\d+)", line)
            pending_bandwidth = int(match.group(1)) if match else 0
        elif line.startswith("#"):
            continue
        elif pending_bandwidth is not None:
            variants.append((pending_bandwidth, line))
            pending_bandwidth = None
    if variants:
        return {"type": "master", "variants": variants}

    segments: list[str] = []
    init_url: str | None = None
    encrypted = False
    for line in lines:
        if line.startswith("#EXT-X-KEY") and "METHOD=AES-128" in line.upper():
            encrypted = True
        elif line.startswith("#EXT-X-MAP"):
            match = re.search(r'URI="([^"]+)"', line)
            if match:
                init_url = match.group(1)
        elif not line.startswith("#"):
            segments.append(line)
    return {"type": "media", "segments": segments, "init": init_url, "encrypted": encrypted}


def _resolve_hls_segments(playlist_url: str, referer: str | None, depth: int = 0) -> list[str]:
    """Expand a master playlist using its highest-bitrate variant and return ordered segment URLs. 展开 master 播放列表（取最高码率），返回按顺序排列的分片地址。"""
    if depth > 3:
        raise RuntimeError("m3u8 playlist nesting is too deep (m3u8 嵌套层级过深)")
    info = _parse_m3u8(fetch_text(playlist_url, referer))
    if info["type"] == "master":
        variants = sorted(info["variants"], key=lambda item: item[0], reverse=True)
        if not variants:
            raise RuntimeError("No available variants in the m3u8 master playlist (m3u8 master 中没有可用清晰度)")
        return _resolve_hls_segments(urljoin(playlist_url, variants[0][1]), referer, depth + 1)
    if info["encrypted"]:
        raise RuntimeError("This HLS stream uses unsupported AES-128 encryption (该 HLS 流使用 AES-128 加密，暂不支持)")
    segments = [urljoin(playlist_url, item) for item in info["segments"]]
    if not segments:
        raise RuntimeError("No segment URLs found in the m3u8 playlist (m3u8 中没有分片地址)")
    if info["init"]:
        segments.insert(0, urljoin(playlist_url, info["init"]))
    return segments


def _remux_to_mp4(ts_path: str, output_path: str) -> str:
    """Remux TS to MP4 with ffmpeg; keep the file as .ts if ffmpeg is unavailable. 用 ffmpeg 把 TS 转封装为 mp4；没有 ffmpeg 时保留 .ts。"""
    ensure_running()
    ffmpeg = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        ts_target = os.path.splitext(output_path)[0] + ".ts"
        os.replace(ts_path, ts_target)
        log(f"[!] ffmpeg not found; saved as a TS file: {os.path.basename(ts_target)} 未找到 ffmpeg，已保存为 TS 文件")
        return ts_target

    temp_mp4 = output_path + ".part.mp4"
    base_cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", ts_path, "-c", "copy"]
    commands = [
        base_cmd + ["-bsf:a", "aac_adtstoasc", temp_mp4],
        base_cmd + [temp_mp4],
    ]
    last_error = ""
    for command in commands:
        if os.path.exists(temp_mp4):
            os.remove(temp_mp4)
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            while True:
                ensure_running()
                try:
                    _, stderr = proc.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    continue
            ensure_running()
        except DownloadCancelled:
            proc.terminate()
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
            if os.path.exists(temp_mp4):
                os.remove(temp_mp4)
            raise

        if proc.returncode == 0 and os.path.isfile(temp_mp4) and os.path.getsize(temp_mp4) > 0:
            os.replace(temp_mp4, output_path)
            return output_path
        last_error = (stderr or "").strip()[-400:]
    raise RuntimeError(f"ffmpeg remux failed: {last_error} ffmpeg 转封装失败")


def download_hls(
    playlist_url: str,
    output_path: str,
    referer: str | None = None,
    label: str = "",
    bar: MultiProgress | None = None,
    max_retries: int = MAX_RETRIES,
) -> str:
    """Download HLS: fetch segments in order -> concatenate into TS -> remux to MP4 with ffmpeg.
    Segment failures are retried; .part.ts and the progress file are used to resume after whole-download retries.
    下载 HLS：按序拉取分片 -> 拼接为 TS -> ffmpeg 转封装 mp4。
    分片级失败会自动重试；整体失败重试时依靠 .part.ts 与进度文件续传。
    """
    temp_ts = output_path + ".part.ts"
    state_path = temp_ts + ".json"
    key = os.path.basename(output_path)
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        ensure_running()
        try:
            segments = _resolve_hls_segments(playlist_url, referer)
            done = 0
            written = 0
            if os.path.exists(temp_ts) and os.path.exists(state_path):
                try:
                    with open(state_path, encoding="utf-8") as state_file:
                        state = json.load(state_file)
                    if (state.get("total") == len(segments)
                            and os.path.getsize(temp_ts) == state.get("bytes")):
                        done = int(state.get("done", 0))
                        written = int(state.get("bytes", 0))
                except (OSError, ValueError, TypeError):
                    done = 0

            if done == 0:
                open(temp_ts, "wb").close()
                written = 0
            if bar is not None:
                bar.update(key, done / len(segments))

            with open(temp_ts, "ab") as file_obj:
                for index in range(done, len(segments)):
                    ensure_running()
                    data = fetch_bytes(segments[index], referer)
                    file_obj.write(data)
                    file_obj.flush()
                    written += len(data)
                    with open(state_path, "w", encoding="utf-8") as state_file:
                        json.dump(
                            {"done": index + 1, "bytes": written, "total": len(segments)},
                            state_file,
                        )
                    if bar is not None:
                        bar.add_bytes(key, len(data))
                        bar.update(key, (index + 1) / len(segments))

            result = _remux_to_mp4(temp_ts, output_path)
            for path in (temp_ts, state_path):
                if os.path.exists(path):
                    os.remove(path)
            return result
        except DownloadCancelled:
            raise
        except Exception as exc:
            if STOP_EVENT.is_set():
                raise DownloadCancelled("Stopped (已停止)") from exc
            last_error = exc
            log(f"    [!] {label} HLS download failed ({attempt}/{max_retries}): {exc} HLS 下载失败（{attempt}/{max_retries}）")
            if attempt < max_retries:
                if STOP_EVENT.wait(min(2 ** attempt, 10)):
                    raise DownloadCancelled("Stopped (已停止)") from exc

    raise RuntimeError(f"HLS download failed after {max_retries} retries: {last_error} HLS 重试 {max_retries} 次后仍失败")


def download_video(
    video_url: str,
    output_path: str,
    referer: str | None = None,
    label: str = "",
    bar: MultiProgress | None = None,
    max_retries: int = MAX_RETRIES,
) -> None:
    """Stream a video download; use segments for HLS and resume support for direct links. 流式下载视频；HLS 走分片下载，直链支持断点续传。"""
    key = os.path.basename(output_path)
    if bar is not None:
        bar.start_task(key)

    if is_hls_url(video_url):
        download_hls(video_url, output_path, referer=referer, label=label,
                     bar=bar, max_retries=max_retries)
        return

    temp_path = output_path + ".part"
    headers = request_headers(referer)
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        ensure_running()
        existing = os.path.getsize(temp_path) if os.path.exists(temp_path) else 0
        request_headers_with_range = dict(headers)
        if existing:
            request_headers_with_range["Range"] = f"bytes={existing}-"

        try:
            resp = requests.get(
                video_url,
                headers=request_headers_with_range,
                stream=True,
                timeout=(5, DOWNLOAD_READ_TIMEOUT),
                verify=False,
            )
            with cancellable_response(resp), resp:
                if resp.status_code == 416:
                    os.remove(temp_path)
                    raise RuntimeError("Resume request rejected (HTTP 416); restarting download (续传请求被拒绝（HTTP 416），将重新下载)")
                resp.raise_for_status()

                if resp.status_code != 206:
                    existing = 0
                remaining = int(resp.headers.get("content-length") or 0)
                total = existing + remaining if remaining else 0
                downloaded = existing

                with open(temp_path, "ab" if existing else "wb") as file_obj:
                    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        ensure_running()
                        file_obj.write(chunk)
                        downloaded += len(chunk)
                        if bar is not None:
                            bar.add_bytes(key, len(chunk))
                            if total > 0:
                                bar.update(key, downloaded / total)

                if total > 0 and downloaded != total:
                    raise RuntimeError(f"Incomplete file ({downloaded}/{total} bytes) (文件不完整（{downloaded}/{total} 字节）)")
                if downloaded <= 0:
                    raise RuntimeError("Downloaded content is empty (下载内容为空)")

            os.replace(temp_path, output_path)
            return
        except DownloadCancelled:
            raise
        except Exception as exc:
            if STOP_EVENT.is_set():
                raise DownloadCancelled("Stopped (已停止)") from exc
            last_error = exc
            log(f"    [!] {label} download failed ({attempt}/{max_retries}): {exc} 下载失败（{attempt}/{max_retries}）")
            if attempt < max_retries:
                if STOP_EVENT.wait(min(2 ** attempt, 10)):
                    raise DownloadCancelled("Stopped (已停止)") from exc

    raise RuntimeError(f"Download failed after {max_retries} retries: {last_error} 下载重试 {max_retries} 次后仍失败")
