"""Play-page resolution: open pages with Playwright and extract video URLs. 播放页解析：用 Playwright 打开页面并提取视频地址。"""

from __future__ import annotations

import time
from urllib.parse import urljoin

from .config import BASE_URL, MAX_RETRIES, PAGE_TIMEOUT_MS, PLAYER_TIMEOUT_MS
from .console import STOP_EVENT, log


def wait_for_video_src(page) -> str | None:
    """Wait for a URL to be written to <video>/<source>, then read it.
    document.querySelector('video') cannot access it, so use a Playwright locator.
    等待 <video>/<source> 的地址真正写入，再读取地址。
    document.querySelector('video') 取不到，必须用 Playwright locator 穿透。
    """
    if STOP_EVENT.is_set():
        return None
    video = page.locator("video").first
    video.wait_for(state="attached", timeout=PLAYER_TIMEOUT_MS)

    deadline = time.time() + PLAYER_TIMEOUT_MS / 1000
    while time.time() < deadline:
        if STOP_EVENT.is_set():
            return None
        src = video.evaluate(
            """
            (el) => {
                const source = el.querySelector('source');
                let src = (source && source.getAttribute('src')) || el.getAttribute('src') || '';
                src = (src || '').trim();
                if (!src && /^https?:/i.test(el.currentSrc || '')) {
                    src = el.currentSrc.trim();
                }
                return src || null;
            }
            """
        )
        if src:
            return src
        page.wait_for_timeout(300)
    return None


class VideoResolver:
    """
    On the first visit, some sources may return HTTP 500 or render the player incompletely; retrying often helps.
    However, page.reload() may be blocked by the browser (net::ERR_BLOCKED_BY_CLIENT),
    so retries close the old page, open a new page in the same context, and navigate to the URL again.
    首次访问某些下载源时页面可能返回 500 或播放器渲染不完整，刷新一次通常就好。
    但直接 page.reload() 有时会被浏览器拦截（net::ERR_BLOCKED_BY_CLIENT），
    所以重试时关闭旧页面、在同一个 context 中新建页面后再 goto。
    """

    def __init__(self, context) -> None:
        self._context = context
        self._page = context.new_page()

    def close(self) -> None:
        try:
            self._page.close()
        except Exception:
            pass

    def _reset_page(self) -> None:
        self.close()
        self._page = self._context.new_page()

    def warmup(self) -> None:
        """Warm up the home page to improve the first play-page visit. 预热首页，尽量让第一次打开播放页顺利。"""
        try:
            self._page.goto(BASE_URL + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        except Exception as exc:
            log(f"[!] Home page warm-up failed (ignored): {exc} 预热首页失败（忽略）")

    def resolve(self, play_url: str, attempts: int = MAX_RETRIES) -> str | None:
        for attempt in range(1, attempts + 1):
            if STOP_EVENT.is_set():
                return None
            try:
                response = self._page.goto(
                    play_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS
                )
                if response is not None and response.status >= 500:
                    raise RuntimeError(f"Play page returned HTTP {response.status} (播放页返回 HTTP {response.status})")
                src = wait_for_video_src(self._page)
                if not src:
                    raise RuntimeError("No usable video URL found on the page (页面中没有可用的视频地址)")
                return urljoin(play_url, src)
            except Exception as exc:
                if STOP_EVENT.is_set():
                    return None
                log(f"    [!] Failed to resolve play page (attempt {attempt}/{attempts}): {exc} 第 {attempt}/{attempts} 次解析播放页失败")
                if attempt < attempts:
                    time.sleep(1.5)
                    self._reset_page()
        return None
