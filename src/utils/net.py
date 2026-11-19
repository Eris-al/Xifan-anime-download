"""HTTP request helpers for fetching pages, m3u8 text, and HLS segments. HTTP 请求封装：页面抓取、m3u8 文本、HLS 分片。"""

from __future__ import annotations

import time

import requests
import urllib3

from .config import BASE_URL, DOWNLOAD_READ_TIMEOUT, HEADERS, MAX_RETRIES
from .console import (
    DownloadCancelled,
    STOP_EVENT,
    cancellable_response,
    ensure_running,
    log,
)

# Some CDN certificate chains are incomplete; suppress repeated warnings when certificate verification is disabled. 部分CDN证书链不完整，关闭证书校验时不要反复打印警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def request_headers(referer: str | None = None) -> dict:
    headers = HEADERS.copy()
    headers["Referer"] = referer or (BASE_URL + "/")
    headers["Accept"] = "*/*"
    return headers


def fetch_html(url: str, retries: int = MAX_RETRIES) -> str:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        ensure_running()
        try:
            resp = requests.get(
                url,
                headers=HEADERS,
                timeout=(5, DOWNLOAD_READ_TIMEOUT),
                stream=True,
            )
            with cancellable_response(resp), resp:
                if resp.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                resp.raise_for_status()
                chunks = []
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    ensure_running()
                    if chunk:
                        chunks.append(chunk)
                return b"".join(chunks).decode(
                    resp.encoding or "utf-8", errors="replace"
                )
        except Exception as exc:
            if STOP_EVENT.is_set():
                raise DownloadCancelled("Stopped (已停止)") from exc
            last_error = exc
            if attempt < retries:
                wait = 2 ** attempt
                log(f"    [!] Request failed ({attempt}/{retries}): {exc}; retrying in {wait}s. 访问失败（{attempt}/{retries}）: {exc}，{wait}s 后重试")
                time.sleep(wait)
    raise RuntimeError(f"Request to {url} failed: {last_error} 访问 {url} 失败")


def fetch_text(url: str, referer: str | None, retries: int = MAX_RETRIES) -> str:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        ensure_running()
        try:
            resp = requests.get(
                url,
                headers=request_headers(referer),
                timeout=(5, DOWNLOAD_READ_TIMEOUT),
                stream=True,
                verify=False,
            )
            with cancellable_response(resp), resp:
                resp.raise_for_status()
                chunks = []
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    ensure_running()
                    if chunk:
                        chunks.append(chunk)
                return b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
        except Exception as exc:
            if STOP_EVENT.is_set():
                raise DownloadCancelled("Stopped (已停止)") from exc
            last_error = exc
            if attempt < retries:
                if STOP_EVENT.wait(2 ** attempt):
                    raise DownloadCancelled("Stopped (已停止)") from exc
    raise RuntimeError(f"Failed to fetch m3u8: {url} -> {last_error} 获取 m3u8 失败")


def fetch_bytes(url: str, referer: str | None, retries: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        ensure_running()
        try:
            resp = requests.get(
                url,
                headers=request_headers(referer),
                timeout=(5, DOWNLOAD_READ_TIMEOUT),
                stream=True,
                verify=False,
            )
            with cancellable_response(resp), resp:
                resp.raise_for_status()
                chunks = []
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    ensure_running()
                    if chunk:
                        chunks.append(chunk)
                data = b"".join(chunks)
            if not data:
                raise RuntimeError("Segment content is empty (分片内容为空)")
            return data
        except Exception as exc:
            if STOP_EVENT.is_set():
                raise DownloadCancelled("Stopped (已停止)") from exc
            last_error = exc
            if attempt < retries:
                if STOP_EVENT.wait(attempt):
                    raise DownloadCancelled("Stopped (已停止)") from exc
    raise RuntimeError(f"Failed to download segment: {url} -> {last_error} 分片下载失败")
