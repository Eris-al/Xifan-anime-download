"""Anime page parsing: episode lists, sources, and source parameter handling. 番剧页面解析：剧集列表、播放源与 source 参数处理。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .config import BASE_URL
from .console import log


def get_source_code(url: str) -> str | None:
    """Get the source parameter from a play-page URL. 从播放页 URL 中取出 source 参数。"""
    query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
    return query.get("source") or None


def _next_flight_text(html: str) -> str:
    """Join Next.js RSC self.__next_f.push data into readable page data. 拼接 Next.js RSC 的 self.__next_f.push 数据，得到可读的页面数据。"""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html, re.S)
    parts = []
    for chunk in chunks:
        try:
            parts.append(json.loads(chunk))
        except json.JSONDecodeError:
            continue
    return "".join(parts)


def _extract_json_array(text: str, start: int) -> list | None:
    """Extract a JSON array from text[start] (which points to '[') by matching brackets. 从 text[start]（指向 '['）开始，按括号配对截取一个 JSON 数组。"""
    if start >= len(text) or text[start] != "[":
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:index + 1])
                except json.JSONDecodeError:
                    return None
    return None


def parse_sources(html: str) -> list[dict]:
    """Parse all sources (code/name/episode count) from anime page data. 从番剧页面数据中解析所有播放源（code/name/集数）。"""
    flight = _next_flight_text(html)
    if not flight:
        return []
    marker = '"sources":'
    position = flight.find(marker)
    while position != -1:
        data = _extract_json_array(flight, position + len(marker))
        if isinstance(data, list) and data:
            sources = []
            seen = set()
            for item in data:
                if not isinstance(item, dict):
                    continue
                code = item.get("code")
                if not code or code in seen:
                    continue
                episodes = item.get("episodes") or []
                seen.add(code)
                sources.append({
                    "code": code,
                    "name": item.get("name") or code,
                    "episode_count": len(episodes),
                })
            if sources:
                return sources
        position = flight.find(marker, position + 1)
    return []


def parse_episodes(html: str, anime_id: str) -> list[dict]:
    """Parse the episode list from an anime page and sort by play_id. 解析番剧主页的剧集列表，按 play_id 排序。"""
    soup = BeautifulSoup(html, "html.parser")
    container = next(
        (
            div for div in soup.find_all("div", class_="mt-5")
            if div.get("class") == ["mt-5"]
        ),
        None,
    )
    if container is not None:
        anchors = [li.find("a") for li in container.select("ul > li")]
        anchors = [a for a in anchors if a is not None and a.get("href")]
    else:
        log("    [!] Episode list container not found; searching the page for play links instead. 未找到剧集列表容器，改为在页面中搜索播放链接")
        anchors = soup.find_all("a", href=True)

    pattern = re.compile(rf"/anime/{re.escape(anime_id)}/play/(\d+)")
    episodes: dict[int, dict] = {}
    for a_tag in anchors:
        href = a_tag["href"].strip()
        match = pattern.search(href)
        if not match:
            continue
        play_id = int(match.group(1))
        label = a_tag.get_text(strip=True) or str(play_id)
        full_url = urljoin(BASE_URL, href)
        previous = episodes.get(play_id)
        if previous is None or len(label) > len(previous["label"]):
            episodes[play_id] = {
                "play_id": play_id,
                "url": full_url,
                "label": label,
                "source": get_source_code(full_url),
            }

    if not episodes:
        raise RuntimeError("No episodes found; the page structure may have changed. 未找到任何剧集，页面结构可能已变化。")

    return sorted(episodes.values(), key=lambda item: item["play_id"])


def with_source(url: str, source_code: str) -> str:
    """Replace the source parameter in a play-page URL with the specified source. 把播放页 URL 的 source 参数替换为指定源。"""
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key != "source"]
    query.append(("source", source_code))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def select_source(
    sources: list[dict],
    current_code: str | None,
    requested: str | None,
    episode_count: int,
    input_fn: Callable[[str], str] | None = None,
) -> dict:
    """Choose a download source: command-line option > interactive selection > first source by default. 选择下载源：命令行指定 > 交互选择 > 默认第一个。"""
    sources = [source for source in sources if source.get("code")]
    if not sources:
        code = (requested or current_code or "").strip()
        if not code:
            raise RuntimeError("Could not identify any download sources; the page structure may have changed. 未能识别任何下载源，页面结构可能已变化。")
        sources = [{"code": code, "name": code, "episode_count": episode_count}]

    if requested:
        text = str(requested).strip()
        if text.isdigit():
            index = int(text)
            if 1 <= index <= len(sources):
                return sources[index - 1]
        for source in sources:
            if source["code"].lower() == text.lower():
                return source
        codes = "、".join(source["code"] for source in sources)
        raise SystemExit(f"[!] Invalid download source: {requested} (available: {codes}). 无效的下载源：{requested}（可选：{codes}）")

    if len(sources) == 1:
        return sources[0]

    log("\nAvailable download sources (可用下载源):")
    for index, source in enumerate(sources, start=1):
        log(f"  [{index}] {source['name']}  (source={source['code']}, {source['episode_count']} episodes / 集)")
    log("Tip: If a source returns HTTP 500 or cannot be parsed, try another; the program also tries backup sources automatically. 提示: 某个源访问 500/解析失败时，可换其他源；程序也会自动尝试备用源。")

    while True:
        try:
            reader = input_fn or input
            raw = reader(f"Choose a download source [1-{len(sources)}, default 1] (请选择下载源 [1-{len(sources)}，默认 1]): ").strip()
        except EOFError:
            return sources[0]
        if not raw:
            return sources[0]
        if raw.isdigit() and 1 <= int(raw) <= len(sources):
            return sources[int(raw) - 1]
        for source in sources:
            if source["code"].lower() == raw.lower():
                return source
        log(f"    [!] Invalid input: {raw}. Enter a number or source code. 无效输入: {raw}，请输入序号或 source 代码")


def order_sources(sources: list[dict], chosen: dict) -> list[dict]:
    """Put the chosen source first and treat the rest as backups. 选中的源排在最前，其余作为备用源。"""
    ordered = [chosen]
    for source in sources:
        if source["code"] != chosen["code"]:
            ordered.append(source)
    return ordered
