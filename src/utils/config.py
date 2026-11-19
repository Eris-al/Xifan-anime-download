"""Global configuration: site URL, request headers, timeouts, and retry settings. 全局配置：站点地址、请求头、超时与重试参数。"""

from __future__ import annotations

BASE_URL = "https://next.xifanacg.com"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": BASE_URL + "/",
}

REQUEST_DELAY = 1.0          # Delay between resolving play pages (seconds). 两次播放页解析之间的间隔（秒）
DEFAULT_WORKERS = 3          # Default number of parallel downloads. 默认并行下载数
MAX_RETRIES = 3              # Retries for page requests, play-page resolution, and downloads. 网页访问 / 播放页解析 / 下载的重试次数
PAGE_TIMEOUT_MS = 30_000     # Page navigation timeout. 打开页面的超时
PLAYER_TIMEOUT_MS = 20_000   # Timeout for the player to expose a video URL. 等待播放器出现视频地址的超时
CHUNK_SIZE = 1024 * 256      # Read chunk size for direct downloads. 直链下载的读取块大小
DOWNLOAD_READ_TIMEOUT = 5   # Limit blocking during reads so stop requests are handled promptly. 限制下载读取阻塞时间，以便及时响应停止请求
