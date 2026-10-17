import argparse
import base64
import hashlib
import io
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def _force_utf8_stdout():
    """服务端/locale非UTF-8时, 强制 stdout/stderr 用 UTF-8 包装.

    服务器(尤其是 Docker / cron / SSH 非交互 shell) 常是 C locale,
    Python 默认用系统编码写 stdout 会把中文写成 ◆ 或直接抛出 UnicodeEncodeError.
    用 reconfigure + TextIOWrapper 双保险.
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
                continue
        except Exception:
            pass
        try:
            buf = getattr(stream, "buffer", None)
            if buf is not None:
                wrapped = io.TextIOWrapper(
                    buf, encoding="utf-8", errors="replace", line_buffering=True
                )
                setattr(sys, stream_name, wrapped)
        except Exception:
            pass

_force_utf8_stdout()

warnings.filterwarnings("ignore", message="Unverified HTTPS request")


_logger = logging.getLogger("novel_crawler")


def setup_logging(level=logging.INFO):
    """初始化日志配置. 默认 INFO, 传 logging.DEBUG 开启章节细节."""
    global _logger
    if _logger.handlers:
        _logger.handlers.clear()
    _logger.setLevel(level)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setLevel(level)

    if level <= logging.DEBUG:
        fmt = "%(asctime)s [%(levelname)-5s] %(message)s"
        datefmt = "%H:%M:%S"
    else:
        fmt = "%(message)s"
        datefmt = ""

    handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    _logger.addHandler(handler)
    _logger.propagate = False


def get_logger():
    return _logger


class _ThrottledProgress:
    """线程安全的节流进度条 —— 控制每 2 秒最多刷新一次."""

    def __init__(self, interval=2.0):
        self.interval = interval
        self._lock = threading.Lock()
        self._last_print = 0.0
        self._last_msg = None

    def update(self, done, total, extra=""):
        """尝试刷新进度条. 未满节流时间则跳过, 最后一条必定输出."""
        now = time.monotonic()
        pct = done / total * 100 if total else 100
        msg = f"\r  进度: {done}/{total}  {pct:5.1f}%  {extra}"

        with self._lock:
            self._last_msg = msg
            if now - self._last_print < self.interval:
                return
            self._last_print = now
            sys.stdout.write(msg)
            sys.stdout.flush()

    def finalize(self):
        """结束时强制刷新最后一条进度 (确保 100% 显示)."""
        with self._lock:
            if self._last_msg:
                sys.stdout.write(self._last_msg + "\n")
                sys.stdout.flush()
                self._last_msg = None


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 2


SITE_PROFILES = {
    "biqukan": {
        "name": "笔趣看",
        "description_selector": "#intro",
        "author_selector": "#info h1 small, #info small",
        "catalog_selector": ".mulu_list a",
        "content_selector": "#content",
        "title_selector": "#info h1, #bookname h1",
        "encoding": "utf-8",
    },
    "biquge": {
        "name": "笔趣阁",
        "description_selector": "#intro",
        "author_selector": "#info p",
        "catalog_selector": "#list dl dd a",
        "content_selector": "#content",
        "title_selector": "#booktitle",
        "encoding": "utf-8",
    },
    "qidian": {
        "name": "起点中文网 (需处理反爬)",
        "description_selector": ".book-intro",
        "author_selector": ".book-info h1 a",
        "catalog_selector": ".volume-wrap .cf a",
        "content_selector": ".read-content",
        "title_selector": ".book-info h1",
        "encoding": "utf-8",
    },
    "zongheng": {
        "name": "纵横中文网 (需处理反爬)",
        "description_selector": ".bookSum .book_text",
        "author_selector": ".bookSum .book_name a",
        "catalog_selector": ".chapter-list a",
        "content_selector": ".content",
        "title_selector": ".bookSum h2",
        "encoding": "utf-8",
    },
    "generic": {
        "name": "通用模式",
        "description_selector": None,
        "author_selector": None,
        "catalog_selector": None,
        "content_selector": None,
        "title_selector": None,
        "encoding": None,
    },
}


AUTO_SITES = [
    {
        "name": "笔趣看",
        "entry_urls": [
            "http://www.biqukan.la/fenlei/1/1.html",
            "http://www.biqukan.la/fenlei/2/1.html",
            "http://www.biqukan.la/fenlei/3/1.html",
            "http://www.biqukan.la/fenlei/4/1.html",
            "http://www.biqukan.la/fenlei/5/1.html",
            "http://www.biqukan.la/fenlei/6/1.html",
            "http://www.biqukan.la/fenlei/7/1.html",
        ],
        "site_key": "biqukan",
    },
    {
        "name": "八一中文网",
        "entry_urls": [
            "https://www.81zw.com/xuanhuan/",
            "https://www.81zw.com/dushi/",
            "https://www.81zw.com/",
        ],
        "site_key": "biquge",
    },
]


def auto_run(output_dir="auto_output", max_novels_per_site=None,
             max_workers=8, delay=0.15, hhc_path=None, compile_chm=True,
             max_chapters=0):
    """全自动模式: 遍历所有备选小说站点, 每站全站爬取所有小说.

    每个站点配置多个 entry_urls (分类页/排行榜页/首页), 依次 fallback
    直到 discover_novel_links() 能找到至少 3 本书.
    """
    print("=" * 60)
    print("  全自动小说爬虫 - Auto Batch Novel Crawler")
    print("  无参启动, 遍历所有备选小说站点")
    print("=" * 60)
    print(f"输出根目录:   {output_dir}")
    print(f"每站最多:     {'不限' if max_novels_per_site is None else max_novels_per_site} 本")
    print(f"编译CHM:      {'是' if compile_chm else '否'}")
    print(f"并发数:       {max_workers}")
    print("=" * 60)

    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    total_success = 0
    total_fail = 0
    site_results = []

    for site_cfg in AUTO_SITES:
        site_name = site_cfg["name"]
        entry_urls = site_cfg.get("entry_urls", [])
        site_key = site_cfg["site_key"]

        print(f"\n{'#' * 60}")
        print(f"# 站点: {site_name}")
        print(f"# 入口: {', '.join(entry_urls[:3])}")
        print(f"{'#' * 60}")

        site_out = out_root / safe_filename(site_name)
        site_out.mkdir(parents=True, exist_ok=True)

        print(f"  入口 ({len(entry_urls)} 个): {', '.join(entry_urls[:3])}{' ...' if len(entry_urls)>3 else ''}")

        working_entries = []
        for entry in entry_urls:
            print(f"  探测入口: {entry}", end=" ... ", flush=True)
            found = discover_novel_links(entry, max_novels=3, delay=delay)
            if len(found) >= 1:
                working_entries.append(entry)
                print(f"OK ({len(found)} 本)")
            else:
                print(f"跳过 (无小说)")

        if not working_entries:
            print(f"  [失败] {site_name} 所有入口均不可用")
            site_results.append((site_name, "N/A", 0, False))
            total_fail += 1
            continue

        print(f"  [OK] {len(working_entries)} 个入口可用, 开始全站 discover")

        try:
            ok = batch_crawl(
                start_urls=working_entries,
                output_dir=str(site_out),
                site_key=site_key,
                max_novels=max_novels_per_site,
                max_workers=max_workers,
                delay=delay,
                hhc_path=hhc_path,
                compile_chm=compile_chm,
                max_chapters=max_chapters,
            )

            existing_chms = list(site_out.glob("*.chm"))
            existing_htmls = [f for f in site_out.glob("*.html") if compile_chm]
            count = len(existing_chms) + len(existing_htmls)

            site_results.append((site_name, str(len(working_entries)) + " entries", count, ok))
            total_success += count
            if not ok:
                total_fail += 1

        except KeyboardInterrupt:
            print("\n用户中断, 已爬取站点保留")
            break
        except Exception as e:
            print(f"  [站点异常] {site_name}: {e}")
            import traceback
            traceback.print_exc()
            site_results.append((site_name, working_entries, 0, False))
            total_fail += 1

        time.sleep(delay * 2)

    print(f"\n\n{'='*60}")
    print(f"  全自动爬取完成!")
    print(f"  成功生成: {total_success} 本")
    print(f"  站点失败: {total_fail} 个")
    print(f"{'='*60}")
    print(f"  站点汇总:")
    print(f"  {'站点':<12} {'入口URL':<40} {'数量':>5} {'状态':>4}")
    print(f"  {'-'*64}")
    for name, url, count, ok in site_results:
        status = "OK" if ok else "FAIL"
        print(f"  {name:<12} {str(url)[:38]:<40} {count:>5} {status:>4}")
    print(f"\n  总输出目录: {out_root.resolve()}")
    print(f"{'='*60}")
    return total_success > 0


def safe_filename(name):
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = name.strip().strip(".")
    return name or "untitled"


def clean_text(text):
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_FILE_URL_EXTS = (".html", ".htm", ".shtml", ".asp", ".aspx", ".php", ".jsp", ".do", ".action")


def resolve_url(base, href):
    """智能 URL 拼接 —— 修复 Python urljoin 经典坑.

    当 base URL 路径看起来是文件时 (如 /book/123456.html),
    标准 urljoin 会把父目录当作 base, 导致相对章节链接丢失小说 ID:

        urljoin("http://x.com/book/123456.html", "789012.html")
        → "http://x.com/book/789012.html"    ← 丢失 123456！全部 404

    本函数会把文件 URL 的 stem 当作"虚拟目录":

        resolve_url("http://x.com/book/123456.html", "789012.html")
        → "http://x.com/book/123456/789012.html"    ← 正确
    """
    if not href:
        return base
    href = href.split("#", 1)[0].split("?", 1)[0]

    # 绝对 URL (带 scheme) → 直接返回
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", href):
        return href

    parsed = urlparse(base)
    path = parsed.path or "/"

    # base 本身就是目录或以 / 结尾 → 标准 urljoin 没问题
    if path.endswith("/"):
        return urljoin(base, href)

    # href 以 / 开头 (绝对路径) → 标准 urljoin 没问题
    if href.startswith("/"):
        return urljoin(base, href)

    # href 包含 ../ 或 ./ → 直接 urljoin 让它解析
    if href.startswith(("..", "./")):
        return urljoin(base, href)

    # 检查 base 路径是否像 "文件" (有扩展名)
    path_lower = path.lower()
    is_file_like = path_lower.endswith(_FILE_URL_EXTS)

    if not is_file_like:
        return urljoin(base, href)

    # === 核心修复: base 是文件 URL, href 是相对路径 ===
    # 把 /book/123456.html + 789012.html
    # 变成 /book/123456/789012.html

    last_slash = path.rfind("/")
    if last_slash < 0:
        # 没有 /, 直接拼
        return f"{parsed.scheme}://{parsed.netloc}/{href}"

    dir_part = path[:last_slash + 1]       # /book/
    filename = path[last_slash + 1:]        # 123456.html
    dot_idx = filename.rfind(".")
    stem = filename[:dot_idx] if dot_idx > 0 else filename  # 123456

    return f"{parsed.scheme}://{parsed.netloc}{dir_part}{stem}/{href}"


IMG_PLACEHOLDER_RE = re.compile(r"\s*\[\[IMG:(\d+)\]\]\s*")


def text_to_html_paragraphs(text, images=None):
    if not text:
        return ""
    if images is None:
        images = []

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    segments = []
    last = 0
    for m in IMG_PLACEHOLDER_RE.finditer(text):
        if m.start() > last:
            segments.append(("text", text[last:m.start()]))
        idx = int(m.group(1))
        segments.append(("img", idx))
        last = m.end()
    if last < len(text):
        segments.append(("text", text[last:]))

    html_parts = []
    for seg_type, seg_val in segments:
        if seg_type == "img":
            img_idx = seg_val
            if 0 <= img_idx < len(images):
                img = images[img_idx]
                src = img.get("local") or img.get("url", "")
                alt = img.get("alt", "").replace('"', "&quot;")
                alt_attr = f' alt="{alt}"' if alt else ""
                html_parts.append(
                    f'<figure style="text-align:center;margin:1em 0;">'
                    f'<img src="{src}"{alt_attr} '
                    f'style="max-width:100%;height:auto;" />'
                    f'</figure>'
                )
            continue

        seg_val = seg_val.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        paragraphs = re.split(r"\n\s*\n", seg_val.strip())
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            p = p.replace("\n", "<br>\n")
            html_parts.append(f"<p>{p}</p>")

    return "\n".join(html_parts)


BLOCK_TAGS = {
    "p", "div", "section", "article", "li", "blockquote",
    "pre", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td",
    "header", "footer", "nav", "aside", "figure", "figcaption",
}


def extract_preserve_paragraphs(element, base_url=""):
    if not element:
        return "", []
    parts = []
    images = []

    def _resolve_img_url(src):
        if not src:
            return ""
        src = src.strip()
        if src.startswith(("http://", "https://", "//", "data:")):
            if src.startswith("//"):
                return "https:" + src
            return src
        if base_url:
            return resolve_url(base_url, src)
        return src

    def _walk(node, is_root=True):
        if isinstance(node, str):
            t = node.strip()
            if t:
                parts.append(t)
            return
        if not hasattr(node, "name") or node.name is None:
            return
        if node.name in ("script", "style", "noscript", "iframe", "ins"):
            return
        if node.name == "br":
            parts.append("\n")
            return
        if node.name == "hr":
            parts.append("\n\n")
            return
        if node.name == "img":
            src = node.get("src") or node.get("data-src") or node.get("data-original") or ""
            real_src = _resolve_img_url(src)
            alt = node.get("alt", "") or ""
            if real_src and real_src.startswith(("http://", "https://", "data:")):
                images.append({"url": real_src, "alt": alt.strip()})
                parts.append(f"\n\n[[IMG:{len(images) - 1}]]\n\n")
            return
        if node.name in BLOCK_TAGS:
            if not is_root:
                parts.append("\n\n")
            for child in node.children:
                _walk(child, False)
            if not is_root:
                parts.append("\n\n")
            return
        for child in node.children:
            _walk(child, False)

    _walk(element, True)
    text = "".join(parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), images


def detect_site_by_url(url):
    host = urlparse(url).netloc.lower()
    if "qidian" in host:
        return "qidian"
    if "zongheng" in host:
        return "zongheng"
    if "17k" in host:
        return "generic"
    if "biqukan" in host:
        return "biqukan"
    if "biquge" in host or "bqg" in host:
        return "biquge"
    return "generic"


def discover_novel_links(start_urls, max_novels=None, delay=0.5, progress_cb=None):
    """全站爬取所有小说目录页链接 (不限深度, 支持多入口并行启动).

    多入口策略: 把多个分类页/排行榜页作为 BFS 种子, 共享 seen_books / seen_pages,
    确保所有分类 + 所有分页都被遍历. 同站点任意链接都跟进.

    Args:
        start_urls:  单个或多个起始 URL (list[str] 或 str) - 分类页/排行榜页/首页均可
        max_novels: 最大小说数量, None 表示不限
        delay:      每页请求间隔
        progress_cb: 每发现一本小说时回调 cb(发现序号, title, url)

    Returns:
        list[dict]: [{"title": "xxx", "url": "http://..."}]
    """
    if isinstance(start_urls, str):
        start_urls = [start_urls]

    if not start_urls:
        return []

    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    import warnings
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

    parsed = urlparse(start_urls[0])
    host = parsed.netloc.lower()

    book_link_re = re.compile(r"/(?:book|xiaoshuo|novel|zuopin)/(\d+)", re.I)
    chapter_noise_re = re.compile(r"(?:chapter|read|content)(?:/|$)|/\d+/\d{5,}\.html$", re.I)
    category_re = re.compile(r"/(?:fenlei|category|class|sort|type)/", re.I)
    paging_re = re.compile(r"(?:下一页|next|>\s*$|[?&]page=\d+|/\d+\.html$|-\d+\.html$)", re.I)

    seen_books = set()
    seen_book_ids = set()
    novels = []
    seen_pages = set()
    queue = []
    for s in start_urls:
        queue.append((s, 0))

    def _is_same_host(url):
        return urlparse(url).netloc.lower() == host

    page_count = 0
    MAX_PAGES = 3000
    PROGRESS_EVERY = 50
    _log = get_logger()

    _log.debug("[发现] %d 个入口, 同站点 %s", len(start_urls), host)

    while queue:
        if max_novels and len(novels) >= max_novels:
            break

        url, depth = queue.pop(0)
        if url in seen_pages:
            continue
        seen_pages.add(url)
        page_count += 1

        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, verify=False)
            resp.encoding = resp.apparent_encoding
            text = resp.text
        except Exception:
            continue

        if len(text) < 500:
            continue

        soup = BeautifulSoup(text, "html.parser")
        sub_links = []

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            abs_url = resolve_url(url, href)
            abs_url = abs_url.split("?", 1)[0]
            if not _is_same_host(abs_url):
                continue
            if abs_url in seen_books or abs_url in seen_pages:
                continue
            if any(abs_url == q[0] for q in queue):
                continue

            path = urlparse(abs_url).path.lower()

            if book_link_re.search(path) and not chapter_noise_re.search(path):
                m = book_link_re.search(path)
                book_id = m.group(1) if m else ""
                id_key = f"{urlparse(abs_url).netloc}/{book_id}"

                if abs_url in seen_books or id_key in seen_book_ids:
                    continue
                seen_books.add(abs_url)
                if book_id:
                    seen_book_ids.add(id_key)

                title = (a.get_text(" ", strip=True) or "").strip()[:80]
                novels.append({"title": title, "url": abs_url})
                if progress_cb:
                    progress_cb(len(novels), title, abs_url)
                if max_novels and len(novels) >= max_novels:
                    break
            else:
                label = a.get_text(" ", strip=True)
                is_category = bool(category_re.search(path)) and not chapter_noise_re.search(path)
                is_paging = bool(paging_re.search(href) or paging_re.search(path))
                is_page_link = re.search(r"^(\d+|下一页|末页|尾页|next page?)$", label, re.I) is not None
                is_aggregate_page = bool(re.search(r"(top|rank|quanben|完本|排行|热门|推荐)", path, re.I))

                should_follow = (
                    (is_category and depth < 8)
                    or ((is_paging or is_page_link) and depth < 6)
                    or (depth < 2 and path.count("/") <= 2 and not is_aggregate_page)
                )
                if should_follow:
                    sub_links.append((abs_url, depth + 1))

        for sl in sub_links:
            queue.append(sl)

        if page_count % PROGRESS_EVERY == 0:
            _log.debug("[发现] 爬 %d 页 找到 %d 本 队列剩 %d", page_count, len(novels), len(queue))

        if page_count > MAX_PAGES:
            _log.debug("[发现] 达上限 %d, 停止", MAX_PAGES)
            break

        time.sleep(delay)

    _log.info("[发现] 完成: %d 页面, %d 本小说", page_count, len(novels))
    return novels


MANIFEST_FILENAME = "_manifest.json"
STATUS_TODO = "todo"
STATUS_DONE = "done"
STATUS_FAIL = "fail"


def load_manifest(out_root):
    path = Path(out_root) / MANIFEST_FILENAME
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "novels" in data:
            return data["novels"]
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def save_manifest(out_root, novels):
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(novels),
        "novels": novels,
    }
    (out_root / MANIFEST_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def is_novel_completed(out_root, novel):
    """判断一本小说是否已完成爬取 - 检查 chm 文件 或 html 目录 是否有效."""
    url = novel.get("url", "")
    title = novel.get("title", "")

    if novel.get("status") == STATUS_DONE and novel.get("completed_at"):
        return True

    if novel.get("output_file"):
        f = Path(novel["output_file"])
        if f.exists() and f.stat().st_size > 100:
            return True

    out_root = Path(out_root)
    candidates = []
    if title:
        candidates.append(out_root / f"{safe_filename(title)}.chm")
        candidates.append(out_root / safe_filename(title) / "index.html")
    url_id = re.search(r"/(\d+)/?$", url)
    if url_id:
        candidates.append(out_root / f"book_{url_id.group(1)}.chm")

    for c in candidates:
        if c.exists() and c.stat().st_size > 100:
            return True
    return False


def merge_discovered_with_manifest(existing, new_novels):
    """把 discover 新发现的小说合并进旧 manifest, 保留旧的 status."""
    existing_urls = {n.get("url"): n for n in existing}
    merged = []
    for n in new_novels:
        url = n.get("url", "")
        if url in existing_urls:
            old = existing_urls[url]
            merged.append({**n, "title": old.get("title") or n.get("title"),
                          "status": old.get("status", STATUS_TODO),
                          "completed_at": old.get("completed_at"),
                          "output_file": old.get("output_file")})
        else:
            merged.append({**n, "status": STATUS_TODO})
    for url, old in existing_urls.items():
        if url not in {n.get("url") for n in merged}:
            merged.append(old)
    return merged


def _run_discovery(start_urls, out_root, existing_manifest, max_novels, delay, manifest_path):
    """执行 discover 并合并写入 manifest. 返回 novel 列表 (失败返回 None)."""
    print(f"\n[阶段] 全站发现小说链接 (不限深度)...")

    def _on_found(idx, title, url):
        pass

    discovered = discover_novel_links(
        start_urls, max_novels=max_novels, delay=delay, progress_cb=_on_found
    )

    if not discovered:
        print("  [失败] 未发现任何小说链接, 请检查起始URL")
        print("         笔趣看示例: http://www.biqukan.la/")
        print("                    http://www.biqukan.la/fenlei/1/1.html")
        print("         或传入多个分类页 URL 列表")
        return None

    novels = merge_discovered_with_manifest(existing_manifest, discovered)
    if max_novels:
        novels = novels[:max_novels]

    save_manifest(out_root, novels)

    print(f"\n  共 {len(novels)} 本小说已写入清单: {manifest_path}")
    for i, n in enumerate(novels[:5], 1):
        print(f"    [{i}] {n.get('title', '(无标题)')}  {n['url']}")
    if len(novels) > 5:
        print(f"    ... 还有 {len(novels) - 5} 本")
    return novels


def batch_crawl(start_urls, output_dir="batch_output", site_key=None,
                max_novels=None, max_workers=8, delay=0.5, hhc_path=None,
                compile_chm=True, max_chapters=0, skip_discovery=False):
    """批量爬取模式: manifest → 逐本爬取 → (全部完成后) 重新探索.

    策略:
      - 有 manifest 且有待爬小说 → 跳过 discover, 直接爬取
      - 无 manifest 或全部已完成 → 执行 discover
      - 爬完当前 manifest 全部完成后 → 重新 discover 看有没有新小说

    - max_novels=None 表示全站不限数量
    """
    if isinstance(start_urls, str):
        start_urls = [start_urls]

    print("=" * 60)
    print("  批量小说爬虫 - Batch Novel Crawler")
    print("=" * 60)
    print(f"起始URL:   {len(start_urls)} 个入口")
    for u in start_urls[:5]:
        print(f"           {u}")
    if len(start_urls) > 5:
        print(f"           ... 还有 {len(start_urls)-5} 个")
    print(f"输出目录:  {output_dir}")
    print(f"每本输出到: {output_dir}/<小说标题>.chm")
    print(f"最多小说数: {'不限' if max_novels is None else max_novels}")
    print(f"编译CHM:   {'是' if compile_chm else '否'}")
    print("=" * 60)

    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    manifest_path = out_root / MANIFEST_FILENAME

    existing_manifest = load_manifest(out_root)
    if existing_manifest:
        done_now = sum(1 for n in existing_manifest if is_novel_completed(out_root, n))
        todo_now = sum(1 for n in existing_manifest if not is_novel_completed(out_root, n))
        print(f"\n  检测到已有清单 {manifest_path}")
        print(f"  共 {len(existing_manifest)} 本 | 已完成 {done_now} | 待爬 {todo_now}")
    else:
        print(f"\n  未检测到已有清单, 将执行 discover")

    # === 决定是否执行 discover ===
    if skip_discovery and existing_manifest:
        novels = existing_manifest
        print(f"\n  [跳过发现] 使用已有清单")
    elif existing_manifest:
        done_now = sum(1 for n in existing_manifest if is_novel_completed(out_root, n))
        todo_now = sum(1 for n in existing_manifest if not is_novel_completed(out_root, n))

        if todo_now > 0:
            # 有待爬的 → 跳过 discover, 先专注爬完
            novels = existing_manifest
            print(f"\n  [跳过发现] 清单中有 {todo_now} 本待爬, 先执行爬取")
        else:
            # 全部完成 → 重新 discover 看看有没有新小说
            print(f"\n  [全部完成] 清单中 {done_now} 本均已完成, 执行重新探索...")
            result = _run_discovery(start_urls, out_root, existing_manifest,
                                    max_novels, delay, manifest_path)
            if result is None:
                return False
            novels = result
    else:
        # 无 manifest → 首次 discover
        result = _run_discovery(start_urls, out_root, [], max_novels, delay, manifest_path)
        if result is None:
            return False
        novels = result

    done_now = sum(1 for n in novels if is_novel_completed(out_root, n))
    todo = [n for n in novels if not is_novel_completed(out_root, n)]
    _log = get_logger()

    _log.info("开始爬取: 已完成 %d, 待爬 %d", done_now, len(todo))

    total = len(novels)
    success_count = done_now
    fail_count = 0
    skipped_count = 0

    for idx, novel in enumerate(todo, 1):
        catalog_url = novel["url"]
        existing_title = novel.get("title", "")
        _log.info("[%d/%d] %s", idx, len(todo), existing_title or catalog_url)

        if is_novel_completed(out_root, novel):
            _log.info("  → 跳过 (已完成)")
            skipped_count += 1
            continue

        crawler = NovelCrawler(
            catalog_url=catalog_url,
            output_dir=None,
            site_key=site_key,
            max_workers=max_workers,
            delay=delay,
            hhc_path=hhc_path,
            max_chapters=max_chapters,
        )

        if not crawler.crawl():
            _log.warning("  → 失败: 未能抓到章节")
            novel["status"] = STATUS_FAIL
            fail_count += 1
            save_manifest(out_root, novels)
            time.sleep(delay)
            continue

        novel_title = crawler.novel_title or existing_title
        novel["title"] = novel_title
        title_safe = safe_filename(novel_title) or f"novel_{idx}"

        existing_output = novel.get("output_file")
        if existing_output and Path(existing_output).exists():
            _log.info("  → 跳过 (已存在): %s", existing_output)
            novel["status"] = STATUS_DONE
            novel["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            skipped_count += 1
            save_manifest(out_root, novels)
            continue

        crawler.output_dir = out_root
        crawler._ensure_dirs()

        target_output = ""
        try:
            crawler.generate_html()
            crawler.generate_single_html()
            hhp_path = crawler.generate_hhc_project()
            if compile_chm:
                ok = crawler.compile_chm(hhp_path)
                if ok:
                    crawler._cleanup()
                    success_count += 1
                    target_chm = out_root / f"{title_safe}.chm"
                    if target_chm.exists():
                        target_output = str(target_chm)
                    _log.info("  ✓ CHM 编译成功: %s", target_chm)
                else:
                    fail_count += 1
                    _log.warning("  ✗ CHM 编译失败")
            else:
                success_count += 1
                html_index = out_root / title_safe / "index.html"
                if html_index.exists():
                    target_output = str(html_index)
                _log.info("  ✓ HTML 已生成 (未编译 CHM)")
        except Exception as e:
            _log.warning("  ✗ 异常: %s", e)
            fail_count += 1

        novel["status"] = STATUS_DONE if target_output else STATUS_FAIL
        novel["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S") if target_output else None
        novel["output_file"] = target_output
        save_manifest(out_root, novels)

        time.sleep(delay)

    import shutil as _shutil
    for sub in ("html", "chm"):
        d = out_root / sub
        if d.is_dir():
            try:
                _shutil.rmtree(str(d), ignore_errors=True)
            except Exception:
                pass
    for pat in ("*.html", "*.css", "*.hhc", "*.hhp", "*.hhk"):
        for f in out_root.glob(pat):
            try:
                f.unlink()
            except OSError:
                pass

    save_manifest(out_root, novels)

    done_final = sum(1 for n in novels if is_novel_completed(out_root, n))
    fail_final = sum(1 for n in novels if n.get("status") == STATUS_FAIL)

    print(f"\n{'='*60}")
    print(f"  批量爬取完成!")
    print(f"  总计: {total}  |  完成: {done_final}  |  失败: {fail_final}  |  本次跳过: {skipped_count}")
    print(f"  清单文件: {manifest_path}")
    print(f"  输出目录: {out_root.resolve()}")
    print(f"{'='*60}")
    return done_final > 0


class NovelCrawler:
    def __init__(self, catalog_url, output_dir="output", site_key=None,
                 max_workers=8, delay=0.15, hhc_path=None, max_chapters=0):
        self.catalog_url = catalog_url
        self.output_dir = Path(output_dir) if output_dir else None
        self.html_dir = None
        self.chm_dir = None
        self.max_workers = max_workers
        self.delay = delay
        self.hhc_path = hhc_path
        self.max_chapters = max_chapters

        if site_key is None:
            site_key = detect_site_by_url(catalog_url)
            print(f"自动检测站点类型: {site_key}")

        profile = SITE_PROFILES.get(site_key, SITE_PROFILES["generic"])
        self.site_key = site_key
        self.site_name = profile["name"]
        self.selectors = {k: v for k, v in profile.items()
                         if k not in ("name", "encoding")}
        self.encoding = profile.get("encoding")

        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

        self.novel_title = ""
        self.novel_author = ""
        self.novel_description = ""
        self.chapters = []
        self.chapter_data = []
        self.generic_chapter_selector = None
        self._consecutive_failures = 0
        self._banned = False
        self._banned_reason = ""
        self.log = get_logger()

        if self.output_dir is not None:
            self._ensure_dirs()

    def _ensure_dirs(self):
        if self.output_dir is None:
            return
        self.html_dir = self.output_dir / "html"
        self.chm_dir = self.output_dir / "chm"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.html_dir.mkdir(parents=True, exist_ok=True)
        self.chm_dir.mkdir(parents=True, exist_ok=True)

    def __setattr__(self, key, value):
        super().__setattr__(key, value)
        if key == "output_dir" and value is not None and hasattr(self, "chapter_data"):
            try:
                self._ensure_dirs()
            except Exception:
                pass

    BANNED_STATUS_CODES = {403, 429, 503, 418, 451, 999}
    BANNED_TEXT_PATTERNS = [
        "访问过于频繁", "请求过于频繁", "请求量过大", "请求太快",
        "您的IP已被", "ip已被封", "ip is banned", "access denied",
        "forbidden", "no soup for you",
        "cloudflare", "cf-ray", "ray id",
        "captcha", "challenge-platform",
    ]

    def _mark_banned(self, reason):
        if self._banned:
            return
        self._banned = True
        self._banned_reason = reason
        self.log.warning("! 封IP检测 [%s]: %s — 自动跳过此网站", self.site_name, reason)

    def _check_response_banned(self, status_code, text):
        if status_code in self.BANNED_STATUS_CODES:
            return f"HTTP {status_code} (封IP/限流)"
        if text:
            low = text.lower()
            for pat in self.BANNED_TEXT_PATTERNS:
                if pat.lower() in low:
                    return f"响应含封禁关键词 '{pat}'"
            if len(text) < 800:
                if "probe.js" in text and "alert" in text:
                    return "返回 probe.js 反爬探针脚本"
                if "waf" in low and "renderdata" in low:
                    return "返回 WAF 拦截页"
                if re.search(r"<title>.*(403|429|forbidden|block|ban)", low):
                    return f"短响应异常 (length={len(text)}, 疑似封IP)"
        return None

    def fetch(self, url, verify=False):
        if self._banned:
            return None

        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.get(
                    url, timeout=REQUEST_TIMEOUT, verify=verify,
                    allow_redirects=True
                )

                # === 4xx 客户端错误 (尤其是 404/410) —— 直接放弃, 不计入封 IP ===
                if 400 <= resp.status_code < 500:
                    self.log.debug("[%d] %s", resp.status_code, url[:90])
                    return None

                banned_reason = self._check_response_banned(resp.status_code, resp.text)
                if banned_reason:
                    self._mark_banned(banned_reason)
                    return None

                if resp.status_code == 202:
                    self.log.warning("[202] 反爬检测: %s", url[:80])

                resp.raise_for_status()
                if self.encoding:
                    resp.encoding = self.encoding
                elif not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = resp.apparent_encoding
                text = resp.text

                if len(text) < 500 or ("probe.js" in text and "alert" in text) \
                        or ("waf" in text.lower() and "renderData" in text):
                    self.log.debug("[短响应 %dB] %s", len(text), url[:80])

                self._consecutive_failures = 0
                return text
            except requests.exceptions.SSLError:
                if attempt == 0:
                    return self.fetch(url, verify=False)
                self._consecutive_failures += 1
                return None
            except requests.RequestException as e:
                err_text = str(e).lower()
                ip_err_hints = ("10054", "10060", "timed out", "connection reset",
                                "connection aborted", "connection refused",
                                "max retries", "remote host")
                if any(h in err_text for h in ip_err_hints):
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= 5:
                        self._mark_banned(
                            f"连续 {self._consecutive_failures} 次连接超时/重置 (IP可能被封)"
                        )
                        return None

                if attempt < MAX_RETRIES - 1:
                    self.log.debug("[重试 %d/%d] %s - %s", attempt + 1, MAX_RETRIES, url[:60], e)
                    time.sleep(RETRY_DELAY)
                else:
                    self.log.debug("[失败] %s - %s", url[:60], e)
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= 8:
                        self._mark_banned(
                            f"连续 {self._consecutive_failures} 次请求失败 (IP可能被封)"
                        )
                    return None

    def _safe_select_one(self, soup, selector):
        if not selector:
            return None
        try:
            return soup.select_one(selector)
        except Exception:
            return None

    def _safe_select(self, soup, selector):
        if not selector:
            return []
        try:
            return soup.select(selector)
        except Exception:
            return []

    def _generic_extract_title(self, soup):
        title_el = self._safe_select_one(soup, "h1") or \
                   self._safe_select_one(soup, "h2")
        if title_el:
            t = clean_text(title_el.get_text())
            if t and len(t) < 80:
                return t
        if soup.title and soup.title.string:
            t = clean_text(soup.title.string)
            for sep in ["最新章节", "全文阅读", "笔趣看", "笔趣阁", "免费阅读", "_"]:
                idx = t.find(sep)
                if idx > 0:
                    t = t[:idx]
            return clean_text(t)
        return ""

    def _generic_find_chapter_links(self, soup, base_url):
        seen = set()
        candidates = []
        all_a = soup.find_all("a", href=True)
        for a in all_a:
            href = a.get("href", "")
            text = clean_text(a.get_text())
            if not text or len(text) < 2:
                continue
            if not re.search(r"第[一二三四五六七八九十百千万\d]+章|序章|楔子|番外|后记", text):
                continue
            full_url = resolve_url(base_url, href)
            parsed = urlparse(full_url)
            if parsed.scheme not in ("http", "https"):
                continue
            if full_url in seen:
                continue
            seen.add(full_url)
            candidates.append({"title": text, "url": full_url})
        return candidates

    def _generic_find_content(self, soup, base_url=""):
        candidates_ids = ["content", "chaptercontent", "bookcontent", "booktext", "contentbox", "article"]
        for cid in candidates_ids:
            el = soup.find(id=cid)
            if el and len(el.get_text()) > 100:
                for tag in el.select("script, style, iframe, ins, a"):
                    tag.decompose()
                return extract_preserve_paragraphs(el, base_url)

        candidates_cls = ["content", "chaptercontent", "bookcontent", "booktext", "content-box"]
        for cls in candidates_cls:
            el = soup.find(class_=cls)
            if el and len(el.get_text()) > 100:
                for tag in el.select("script, style, iframe, ins, a"):
                    tag.decompose()
                return extract_preserve_paragraphs(el, base_url)

        divs = soup.find_all("div")
        longest_div = None
        max_len = 0
        for d in divs:
            text = d.get_text(strip=True)
            if len(text) > max_len and len(text) > 200:
                max_len = len(text)
                longest_div = d
        if longest_div:
            for tag in longest_div.select("script, style, iframe, ins, a"):
                tag.decompose()
            return extract_preserve_paragraphs(longest_div, base_url)
        return "", []

    def parse_catalog(self, html_content):
        soup = BeautifulSoup(html_content, "lxml")

        if self.site_key == "generic":
            self.novel_title = self._generic_extract_title(soup)
            all_a = self._generic_find_chapter_links(soup, self.catalog_url)
            self.chapters = [{"title": a["title"], "url": a["url"], "index": i + 1}
                            for i, a in enumerate(all_a)]
            if not self.chapters:
                self._parse_catalog_paginated()
        else:
            title_el = self._safe_select_one(soup, self.selectors["title_selector"])
            if title_el:
                self.novel_title = clean_text(title_el.get_text())
            else:
                self.novel_title = self._generic_extract_title(soup) or urlparse(self.catalog_url).netloc

            author_el = self._safe_select_one(soup, self.selectors["author_selector"])
            if author_el:
                self.novel_author = clean_text(author_el.get_text())

            desc_el = self._safe_select_one(soup, self.selectors["description_selector"])
            if desc_el:
                self.novel_description = clean_text(desc_el.get_text())

            self._parse_catalog_page(soup, self.catalog_url)

            if not self.chapters:
                print("  [!] 预设选择器未命中, 尝试通用模式...")
                all_a = self._generic_find_chapter_links(soup, self.catalog_url)
                self.chapters = [{"title": a["title"], "url": a["url"], "index": i + 1}
                                for i, a in enumerate(all_a)]

            if self.site_key != "generic":
                self._parse_catalog_paginated()

        if self.max_chapters > 0 and len(self.chapters) > self.max_chapters:
            self.log.debug("[截断] 章节数 %d > 限制 %d, 只保留前 %d 章", len(self.chapters), self.max_chapters, self.max_chapters)
            self.chapters = self.chapters[:self.max_chapters]

        self.log.info("小说: 《%s》 作者=%s 章节数=%d", self.novel_title, self.novel_author, len(self.chapters))

    def _parse_catalog_page(self, soup, page_url, depth=0, visited=None):
        if visited is None:
            visited = set()
        if page_url in visited or depth > 20:
            return
        visited.add(page_url)

        catalog_selector = self.selectors.get("catalog_selector")
        if not catalog_selector:
            return

        catalog_links = self._safe_select(soup, catalog_selector)
        seen = {c["url"] for c in self.chapters}
        for a in catalog_links:
            href = a.get("href", "")
            text = clean_text(a.get_text())
            if not href or not text or len(text) < 1:
                continue
            if not re.search(r"\d+\.html?$", href):
                continue
            skip_kw = ("javascript:", "#", "java")
            if any(href.lower().startswith(k) for k in skip_kw):
                continue
            full_url = resolve_url(page_url, href)
            if full_url in seen:
                continue
            seen.add(full_url)
            self.chapters.append({
                "title": text,
                "url": full_url,
                "index": len(self.chapters) + 1,
            })

        next_url = self._find_next_catalog_page(soup, page_url, depth=depth)
        if next_url and next_url not in visited:
            self.log.debug("[目录分页 #%d] → %s", depth + 1, next_url)
            html = self.fetch(next_url)
            if html:
                next_soup = BeautifulSoup(html, "lxml")
                self._parse_catalog_page(next_soup, next_url, depth + 1, visited)

    def _find_next_catalog_page(self, soup, current_url, depth=0):
        parsed = urlparse(current_url)
        base_path = parsed.path.rstrip("/")

        next_keywords = (
            "下一页", "尾页", "末页", "更多章节", "查看更多",
            "展开", "查看全部", "next page", "next",
        )
        candidates = []
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            text = a.get_text(" ", strip=True)
            if not href:
                continue
            text_low = text.lower()
            kw_hit = any(kw.lower() in text_low for kw in next_keywords)
            if not kw_hit:
                continue
            full = resolve_url(current_url, href)
            try:
                p = urlparse(full)
            except Exception:
                continue
            if p.netloc != parsed.netloc:
                continue
            candidates.append(full)

        next_regex = (
            re.escape(base_path) + r"[/_]?\d+\.html?$",
            re.escape(base_path) + r"/index_\d+\.html?$",
            re.escape(base_path) + r"/index\d+\.html?$",
        )
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if not href:
                continue
            full = resolve_url(current_url, href)
            try:
                p = urlparse(full)
            except Exception:
                continue
            if p.netloc != parsed.netloc:
                continue
            other_path = p.path.rstrip("/")
            for pat in next_regex:
                if re.match(pat, other_path) and full != current_url:
                    if full not in candidates:
                        candidates.append(full)
                    break

        for c in candidates:
            if c != current_url:
                return c

        if depth == 0 and parsed.netloc.startswith("www."):
            guess = f"{parsed.scheme}://{parsed.netloc}{base_path}/index_1.html"
            if guess != current_url:
                return guess
        return None

    def _collect_chapters_from_page(self, soup, page_url, selector=None):
        seen = {c["url"] for c in self.chapters}
        added = 0
        links = []
        selectors_to_try = []
        if selector:
            selectors_to_try.append(selector)
        selectors_to_try.extend([
            ".mulu_list a", "#list dl dd a", "#list a",
            "[class*='mulu'] a", "[class*='list'] a", "[class*='chapter'] a",
            ".volume a", ".dir a",
        ])
        for sel in selectors_to_try:
            links = self._safe_select(soup, sel)
            if links:
                break
        if not links:
            links = soup.find_all("a", href=True)
        for a in links:
            href = a.get("href", "")
            text = clean_text(a.get_text())
            if not href or not text or len(text) < 1:
                continue
            if not re.search(r"\d+\.html?$", href):
                continue
            skip_kw = ("javascript:", "#", "java")
            if any(href.lower().startswith(k) for k in skip_kw):
                continue
            full_url = resolve_url(page_url, href)
            if full_url in seen:
                continue
            seen.add(full_url)
            self.chapters.append({
                "title": text,
                "url": full_url,
                "index": len(self.chapters) + 1,
            })
            added += 1
        return added

    def _parse_catalog_paginated(self):
        """移动端 / 替代域名兜底补全目录 (追加模式)."""
        parsed = urlparse(self.catalog_url)
        mobile_urls = []
        if parsed.netloc.startswith("www."):
            mobile_urls.append(
                f"{parsed.scheme}://m.{parsed.netloc[4:]}{parsed.path}"
            )
        mobile_urls.append(
            f"{parsed.scheme}://wap.{parsed.netloc}{parsed.path}"
        )

        existing_urls = {c["url"] for c in self.chapters}
        before = len(self.chapters)
        for m_url in mobile_urls:
            self.log.debug("[兜底] 尝试移动端目录: %s", m_url)
            visited = set()
            self._mobile_recurse(m_url, visited, existing_urls, depth=0)
            if len(self.chapters) > before:
                break

    def _mobile_recurse(self, page_url, visited, existing_urls, depth=0):
        if depth > 10 or page_url in visited:
            return
        visited.add(page_url)
        html = self.fetch(page_url)
        if not html:
            return
        soup = BeautifulSoup(html, "lxml")

        added = self._collect_chapters_from_page(soup, page_url)
        if added > 0:
            self.log.debug("[兜底+%d] 新增 %d 章", depth, added)

        parsed = urlparse(page_url)
        next_url = None
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            text = a.get_text(" ", strip=True)
            if not href:
                continue
            full = resolve_url(page_url, href)
            try:
                p = urlparse(full)
            except Exception:
                continue
            if p.netloc != parsed.netloc:
                continue
            text_low = text.lower()
            if any(kw in text_low for kw in ("下一页", "尾页", "末页", "更多章节", "查看更多", "展开", "next")):
                next_url = full
                break
            other_path = p.path.rstrip("/")
            base = parsed.path.rstrip("/")
            if re.match(re.escape(base) + r"/index_\d+\.html?$", other_path) \
                    and full != page_url and full not in visited:
                next_url = full
                break
        if next_url and next_url not in visited:
            self._mobile_recurse(next_url, visited, existing_urls, depth + 1)

    def _decode_biqukan_content(self, soup):
        html_content = soup.select_one("#htmlContent")
        if not html_content:
            return ""

        decoded_parts = []
        script_tags = html_content.find_all("script")
        for script in script_tags:
            text = script.get_text() or ""
            matches = re.findall(
                r"(?:qsbs|xx).?\.?bb\s*\(\s*['\"]([A-Za-z0-9+/=]+)['\"]\s*\)",
                text, re.IGNORECASE
            )
            if not matches:
                matches = re.findall(
                    r"document\.writeln\s*\(\s*['\"]([A-Za-z0-9+/=]+)['\"]\s*\)",
                    text, re.IGNORECASE
                )
            for b64 in matches:
                try:
                    decoded = base64.b64decode(b64).decode("utf-8")
                    decoded_parts.append(decoded)
                except Exception:
                    continue

        if not decoded_parts:
            return ""

        combined_html = "".join(decoded_parts)
        para_texts = re.split(r"<br\s*/?>|<p[^>]*>|</p>", combined_html, flags=re.IGNORECASE)
        clean_paras = []
        for p in para_texts:
            p = re.sub(r"<[^>]+>", "", p)
            p = p.replace("&nbsp;", " ").replace("&amp;", "&")
            p = p.strip()
            if p and len(p) > 1:
                clean_paras.append(p)

        if clean_paras:
            return "\n\n".join(clean_paras)

        all_text = re.sub(r"<[^>]+>", "", combined_html)
        all_text = all_text.replace("&nbsp;", " ").replace("&amp;", "&")
        return clean_text(all_text)

    def _extract_content_from_soup(self, soup, base_url):
        """统一从 BeautifulSoup 对象中提取正文文本和图片."""
        if self.site_key == "biqukan":
            content_text = self._decode_biqukan_content(soup)
            images = []
            if not content_text:
                content_text, images = self._generic_find_content(soup, base_url)
            return content_text, images
        if self.site_key == "generic":
            return self._generic_find_content(soup, base_url)

        content_el = self._safe_select_one(soup, self.selectors["content_selector"])
        if content_el and len(content_el.get_text()) > 50:
            for tag in content_el.select("script, style, iframe, ins"):
                tag.decompose()
            return extract_preserve_paragraphs(content_el, base_url)
        return self._generic_find_content(soup, base_url)

    def _find_chapter_page_links(self, soup, base_url):
        """检测章节页面内的分页链接 (第2页, 下一页 等).

        策略保守: 只跟进页面上确实存在的显式链接, 绝不猜测 URL.
        对笔趣看这类全站不分页的小说站点, 此函数通常返回空列表.
        """
        host = urlparse(base_url).netloc.lower()
        candidates = []

        page_keywords = (
            "下一页", "下页", "第2页", "第3页", "第4页", "第5页",
            "2/2", "2/3", "3/3", "剩余", "展开全部", "继续阅读",
            "next page",
        )

        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            text = a.get_text(" ", strip=True)
            if not href or not text:
                continue

            # 跳过章节导航 (上一章/下一章/返回/目录/首页 等)
            if re.search(r"[上下][一]?[章节篇回集卷]", text) or \
               re.search(r"返回|目录|首页|评论|投票|打赏|收藏|书架", text):
                continue

            text_low = text.lower()
            if not any(kw.lower() in text_low for kw in page_keywords):
                continue

            full = resolve_url(base_url, href)
            if urlparse(full).netloc.lower() != host:
                continue
            if full == base_url:
                continue
            if full not in candidates:
                candidates.append(full)

        return candidates

    @staticmethod
    def _get_chapter_stem(url):
        """从章节 URL 提取章节唯一标识 (用于判断是否还在同一章).

        笔趣看分页格式: base.html → base_1.html → base_2.html → next_chapter.html
        所以 stem 要把 _N 后缀去掉.

        /book/2427/1220155.html     → 1220155
        /book/2427/1220155_1.html   → 1220155
        /book/2427/1220156.html     → 1220156
        """
        m = re.search(r"/(\d+)(?:_\d+)?(?:\.html?|\.htm|$)", url, re.I)
        return m.group(1) if m else urlparse(url).path

    @staticmethod
    def _find_next_page_href(soup, current_url):
        """在章节页面中找 '下一章' 按钮的 href.

        笔趣看的 '下一章' 按钮在同章分页时指向 base_N.html, 翻完分页后才真正指向下一章.
        此函数只负责找页面上那个明确的导航链接, 不做任何猜测.

        返回: (href_text, absolute_url) 或 (None, None)
        """
        best_text = None
        best_href = None

        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True)
            href = a["href"].strip()
            if not href:
                continue

            # 精确匹配 "下一章" / "下一页" / "下一页阅读" 等
            if text in ("下一章", "下一页") or text.startswith("下一"):
                best_text = text
                best_href = href
                continue

        if not best_href:
            return None, None

        abs_url = resolve_url(current_url, best_href)
        return best_text, abs_url

    def fetch_chapter(self, chapter):
        """抓取单章正文 (沿着 '下一章' 按钮链式补全同章分页)."""
        time.sleep(self.delay)
        first_html = self.fetch(chapter["url"])
        if not first_html:
            return chapter["index"], "", chapter["title"], []

        chapter_url = chapter["url"]
        chapter_stem = self._get_chapter_stem(chapter_url)

        all_text_parts = []
        all_images = []
        fetched_urls = set()

        current_url = chapter_url
        current_html = first_html

        # 同章内最多翻 10 页 (防止异常循环)
        for page_count in range(10):
            if current_url in fetched_urls:
                break
            fetched_urls.add(current_url)

            soup = BeautifulSoup(current_html, "lxml")
            text, imgs = self._extract_content_from_soup(soup, current_url)

            if text and text not in all_text_parts:
                all_text_parts.append(text)
                all_images.extend(imgs)

            # 找下一页链接
            _, next_url = self._find_next_page_href(soup, current_url)
            if not next_url:
                break

            # 判断是否还在同章内 (stem 相同)
            next_stem = self._get_chapter_stem(next_url)
            if next_stem != chapter_stem:
                break

            if next_url in fetched_urls:
                break

            # 还有同章下一页 → 继续抓
            time.sleep(self.delay)
            next_html = self.fetch(next_url)
            if not next_html:
                break

            # 如果 fetch 后 URL 变了 (302 重定向), 检查是否还在同章
            # requests 的 allow_redirects=True 会自动跟随, 但 _get_chapter_stem
            # 是基于请求的 URL 不是响应的 final_url, 这里用 next_url 即可

            current_url = next_url
            current_html = next_html

        pages_fetched = len(fetched_urls)
        if pages_fetched > 1:
            self.log.debug("[分页补全] 第%d章 共 %d 页", chapter["index"], pages_fetched)

        full_text = "\n\n".join(all_text_parts) if all_text_parts else ""
        return chapter["index"], full_text, chapter["title"], all_images

    def crawl(self):
        if self._banned:
            self.log.warning("[%s] 已标记封IP, 跳过爬取", self.site_name)
            return False

        self.log.info("访问目录页: %s", self.catalog_url)
        catalog_html = self.fetch(self.catalog_url)
        if not catalog_html:
            if self._banned:
                self.log.warning("  原因: %s", self._banned_reason)
            self.log.warning("无法获取目录页")
            return False

        self.parse_catalog(catalog_html)

        if not self.chapters:
            if self._banned:
                self.log.warning("  原因: %s", self._banned_reason)
            self.log.warning("未找到任何章节! 请尝试换成 biqukan 等反爬较松的网站")
            return False

        total = len(self.chapters)
        self.log.info("爬取 %d 章  (线程 %d)...", total, self.max_workers)

        results = {}
        progress = _ThrottledProgress(interval=2.0)
        executor = ThreadPoolExecutor(max_workers=self.max_workers)
        try:
            futures = {
                executor.submit(self.fetch_chapter, ch): ch
                for ch in self.chapters
            }
            done_count = 0
            for future in as_completed(futures):
                if self._banned:
                    self.log.warning("封IP触发, 终止剩余 %d 章", len(futures) - done_count)
                    for f in futures:
                        f.cancel()
                    break
                idx, content, title, images = future.result()
                results[idx] = {"title": title, "content": content, "images": images}
                done_count += 1
                status = "OK" if content else "空"
                short_title = title[:25]
                extra = f"[{status}] {short_title}"
                progress.update(done_count, total, extra)
            progress.finalize()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        if self._banned:
            self.chapter_data = [results.get(i, {"title": f"第{i}章", "content": "", "images": []})
                                for i in range(1, total + 1)]
            success = sum(1 for c in self.chapter_data if c["content"])
            self.log.warning("爬取中断: 成功 %d/%d (封IP)", success, total)
            return False

        self.chapter_data = [results.get(i, {"title": f"第{i}章", "content": "", "images": []})
                            for i in range(1, total + 1)]

        self._download_all_images()

        success = sum(1 for c in self.chapter_data if c["content"])
        total_imgs = sum(len(c["images"]) for c in self.chapter_data)
        self.log.info("完成: 成功 %d/%d, 图片 %d 张", success, total, total_imgs)
        if success == 0:
            self.log.warning("所有章节均为空, 请检查目标网站是否可访问")
        return success > 0

    def _write(self, path, content):
        path.write_text(content, encoding="utf-8")

    def _download_all_images(self):
        if not self.chapter_data or not self.html_dir:
            return

        all_images = {}
        for i, ch in enumerate(self.chapter_data, 1):
            for j, img in enumerate(ch.get("images", [])):
                url = img["url"]
                if url not in all_images:
                    all_images[url] = {"chapter_idx": i, "img_idx": j, "alt": img.get("alt", "")}

        if not all_images:
            return

        img_dir = self.html_dir / "images"
        img_dir.mkdir(parents=True, exist_ok=True)

        self.log.debug("开始下载图片 %d 张", len(all_images))

        url_to_local = {}
        success_count = 0
        fail_count = 0

        for url, info in all_images.items():
            ext = ""
            if url.startswith("data:"):
                try:
                    header, payload = url.split(",", 1)
                    mime = header.split(";")[0]
                    if "/" in mime:
                        ext = "." + mime.split("/", 1)[1].split(";")[0]
                    raw = base64.b64decode(payload)
                    fname = f"data_{hashlib.md5(url.encode()).hexdigest()}{ext}"
                    fpath = img_dir / fname
                    fpath.write_bytes(raw)
                    url_to_local[url] = f"images/{fname}"
                    success_count += 1
                except Exception:
                    fail_count += 1
                continue

            parsed = urlparse(url)
            path_part = parsed.path or ""
            _, orig_ext = os.path.splitext(path_part)
            ext = orig_ext.lower() if orig_ext and len(orig_ext) <= 6 else ".jpg"

            hash_part = hashlib.md5(url.encode()).hexdigest()[:12]
            fname = f"img_{hash_part}{ext}"
            fpath = img_dir / fname

            if fpath.exists() and fpath.stat().st_size > 0:
                url_to_local[url] = f"images/{fname}"
                success_count += 1
                continue

            try:
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT, verify=False)
                if resp.status_code == 200 and len(resp.content) > 50:
                    fpath.write_bytes(resp.content)
                    url_to_local[url] = f"images/{fname}"
                    success_count += 1
                else:
                    fail_count += 1
            except Exception:
                fail_count += 1

        if fail_count == 0:
            self.log.debug("图片下载完成: %d 张成功", success_count)
        else:
            self.log.debug("图片下载完成: %d 成功, %d 失败 (HTML保留原图链接)", success_count, fail_count)

        for ch in self.chapter_data:
            for img in ch.get("images", []):
                url = img["url"]
                if url in url_to_local:
                    img["local"] = url_to_local[url]
                else:
                    img["local"] = ""

    def generate_html(self):
        self.log.debug("生成HTML文件到 %s", self.html_dir)

        css = (
            'body { font-family: "Microsoft YaHei", "SimSun", serif !important;'
            " font-size: 45px !important; line-height: 2;"
            " max-width: 100%; margin: 0; padding: 16px;"
            " color: #222; background: #fff; }\n"
            "p { font-family: inherit !important;"
            " font-size: 45px !important; line-height: 2 !important;"
            " text-indent: 2em; margin: 0.6em 0 !important;"
            " color: #222 !important; }\n"
            ".content { font-family: inherit !important;"
            " font-size: 45px !important; line-height: 2 !important;"
            " text-indent: 2em; white-space: pre-wrap;"
            " word-wrap: break-word; color: #222 !important; }\n"
            "h1 { text-align: center; font-size: 72px !important;"
            " color: #2c3e50; margin: 20px 0; }\n"
            "h2 { text-align: center; font-size: 54px !important;"
            " color: #2c3e50; margin: 30px 0 20px; }\n"
            ".chapter h2 { font-size: 54px !important;"
            " border-bottom: 2px solid #eee; padding-bottom: 10px; }\n"
            ".chapter { margin-top: 10px; }\n"
            ".description { background: #f9f9f9; padding: 15px;"
            " border-left: 4px solid #3498db; margin: 20px 0;"
            " font-size: 36px !important; }\n"
            ".toc { list-style: none; padding: 0; }\n"
            ".toc li { padding: 10px 0; border-bottom: 1px dashed #ddd;"
            " font-size: 36px !important; }\n"
            ".toc a { color: #2980b9; text-decoration: none;"
            " font-size: 36px !important; }\n"
            ".nav { text-align: center; margin: 20px 0; padding: 12px;"
            " background: #f5f5f5; border-radius: 5px;"
            " font-size: 36px !important; }\n"
            ".nav a { margin: 0 12px; color: #2980b9;"
            " text-decoration: none; font-size: 36px !important; }\n"
            ".cover { text-align: center; padding: 40px 20px;"
            " background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);"
            " color: #fff; border-radius: 10px; margin-bottom: 30px; }\n"
            ".cover h1 { color: #fff; font-size: 72px !important; }\n"
            ".cover .author { font-size: 36px !important; opacity: 0.9; }\n"
        )
        self._write(self.html_dir / "style.css", css)

        cover_parts = [
            "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
            "<meta charset=\"UTF-8\">\n"
            f"<title>{self.novel_title} - 封面</title>\n"
            '<link rel="stylesheet" href="style.css">\n'
            "</head>\n"
            '<body style="font-size:45px;font-family:Microsoft YaHei,SimSun,serif;line-height:2;">\n'
            '<div class="cover">\n'
            f"<h1>{self.novel_title}</h1>\n"
            f'<div class="author">作者: {self.novel_author or "未知"}</div>\n'
            "</div>\n"
        ]
        if self.novel_description:
            cover_parts.append(
                f'<div class="description">{self.novel_description}</div>\n'
            )
        cover_parts.append(
            '<div class="nav"><a href="index.html">目录</a></div>\n'
            '<div class="nav"><a href="chapter_001.html">开始阅读</a></div>\n'
            "</body>\n</html>"
        )
        self._write(self.html_dir / "cover.html", "".join(cover_parts))

        toc_lines = [
            "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
            "<meta charset=\"UTF-8\">\n"
            f"<title>{self.novel_title} - 目录</title>\n"
            '<link rel="stylesheet" href="style.css">\n'
            "</head>\n"
            '<body style="font-size:45px;font-family:Microsoft YaHei,SimSun,serif;line-height:2;">\n'
            f"<h1>{self.novel_title}</h1>\n"
            '<p style="text-align:center;color:#888;">'
            f"作者: {self.novel_author or '未知'}</p>\n"
            '<ul class="toc">\n'
        ]
        for i, ch in enumerate(self.chapter_data, 1):
            toc_lines.append(
                f'<li><a href="chapter_{i:03d}.html">{ch["title"]}</a></li>\n'
            )
        toc_lines.append(
            "</ul>\n<div class=\"nav\">"
            '<a href="cover.html">封面</a> | '
            '<a href="chapter_001.html">第一章</a>\n'
            "</div>\n</body>\n</html>"
        )
        self._write(self.html_dir / "index.html", "".join(toc_lines))

        total = len(self.chapter_data)
        for i, ch in enumerate(self.chapter_data, 1):
            prev_link = (
                f'<a href="chapter_{i-1:03d}.html">上一章</a>'
                if i > 1
                else '<span style="color:#ccc;">上一章</span>'
            )
            next_link = (
                f'<a href="chapter_{i+1:03d}.html">下一章</a>'
                if i < total
                else '<span style="color:#ccc;">下一章</span>'
            )
            content_html = text_to_html_paragraphs(ch["content"], ch.get("images", [])) or "<p>(空内容)</p>"
            chapter_html = (
                "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
                "<meta charset=\"UTF-8\">\n"
                f"<title>{ch['title']} - {self.novel_title}</title>\n"
                '<link rel="stylesheet" href="style.css">\n'
                "</head>\n"
                '<body style="font-size:45px;font-family:Microsoft YaHei,SimSun,serif;line-height:2;">\n'
                f'<div class="nav">{prev_link} | '
                '<a href="index.html">目录</a> | '
                f"{next_link}</div>\n"
                '<div class="chapter">\n'
                f"<h2>{ch['title']}</h2>\n"
                f'<div class="content" style="font-size:45px;line-height:2;text-indent:2em;">{content_html}</div>\n'
                "</div>\n"
                f'<div class="nav">{prev_link} | '
                '<a href="index.html">目录</a> | '
                f"{next_link}</div>\n"
                "</body>\n</html>"
            )
            self._write(self.html_dir / f"chapter_{i:03d}.html", chapter_html)

        html_count = len(list(self.html_dir.glob("*.html")))
        self.log.debug("HTML已生成: %d 个文件", html_count)

    def generate_single_html(self):
        title_safe = safe_filename(self.novel_title)
        single_path = self.output_dir / f"{title_safe}.html"

        parts = [
            "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
            "<meta charset=\"UTF-8\">\n"
            f"<title>{self.novel_title}</title>\n"
            "<style>\n"
            'body { font-family: "Microsoft YaHei", "SimSun", serif;'
            " max-width: 800px; margin: 0 auto; padding: 20px;"
            " line-height: 1.8; color: #333; }\n"
            "h1, h2 { text-align: center; color: #2c3e50; }\n"
            ".chapter { margin-top: 40px; page-break-before: always; }\n"
            ".chapter h2 { font-size: 1.3em; border-bottom: 2px solid #eee;"
            " padding-bottom: 8px; }\n"
            ".content { text-indent: 2em; white-space: pre-wrap;"
            " word-wrap: break-word; font-size: 16px; }\n"
            ".description { background: #f9f9f9; padding: 15px;"
            " border-left: 4px solid #3498db; margin: 20px 0; }\n"
            ".cover { text-align: center; padding: 40px 20px;"
            " background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);"
            " color: #fff; border-radius: 10px; margin-bottom: 30px; }\n"
            ".cover h1 { color: #fff; font-size: 2.2em; }\n"
            ".toc { list-style: none; padding: 20px;"
            " background: #f9f9f9; border-radius: 5px; }\n"
            ".toc li { padding: 5px 0; }\n"
            "</style>\n</head>\n<body>\n"
            '<div class="cover">\n'
            f"<h1>{self.novel_title}</h1>\n"
            f'<div style="font-size:1.1em;opacity:0.9;">'
            f"作者: {self.novel_author or '未知'}</div>\n"
            "</div>\n"
        ]
        if self.novel_description:
            parts.append(
                f'<div class="description">{self.novel_description}</div>\n'
            )

        parts.append('<ul class="toc">\n')
        for i, ch in enumerate(self.chapter_data, 1):
            parts.append(f'<li><a href="#ch{i}">{ch["title"]}</a></li>\n')
        parts.append("</ul>\n")

        for i, ch in enumerate(self.chapter_data, 1):
            content_html = text_to_html_paragraphs(ch["content"], ch.get("images", [])) or "<p>(空内容)</p>"
            parts.append(
                f'<a id="ch{i}"></a>\n'
                '<div class="chapter">\n'
                f"<h2>{ch['title']}</h2>\n"
                f'<div class="content">{content_html}</div>\n'
                "</div>\n"
            )

        parts.append("</body>\n</html>")
        self._write(single_path, "".join(parts))
        self.log.debug("单文件HTML已生成: %s", single_path)
        return single_path

    def generate_hhc_project(self):
        hhc_file = self.chm_dir / "novel.hhc"
        hhp_file = self.chm_dir / "novel.hhp"
        hhk_file = self.chm_dir / "novel.hhk"

        hhc_parts = [
            '<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML//EN">\n'
            "<HTML>\n<HEAD>\n<meta charset=\"UTF-8\">\n"
            f"<Title>{self.novel_title}</Title>\n"
            "</HEAD>\n<BODY>\n<UL>\n"
            '<LI><OBJECT type="text/sitemap">\n'
            f'<param name="Name" value="{self.novel_title}">\n'
            '<param name="Local" value="..\\html\\cover.html">\n'
            "</OBJECT>\n<UL>\n"
        ]
        for i, ch in enumerate(self.chapter_data, 1):
            hhc_parts.append(
                '<LI><OBJECT type="text/sitemap">\n'
                f'<param name="Name" value="{ch["title"]}">\n'
                f'<param name="Local" value="..\\html\\chapter_{i:03d}.html">\n'
                "</OBJECT>\n"
            )
        hhc_parts.append("</UL>\n</UL>\n</BODY>\n</HTML>")
        self._write(hhc_file, "".join(hhc_parts))

        hhk_lines = [
            '<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML//EN">\n'
            "<HTML>\n<HEAD>\n"
            f"<Title>{self.novel_title} - 索引</Title>\n"
            "</HEAD>\n<BODY>\n<UL>\n"
        ]
        hhk_lines.append(
            f'<LI>{self.novel_title}<OBJECT type="text/sitemap">'
            '<param name="Local" value="..\\html\\cover.html"></OBJECT></LI>\n'
        )
        hhk_lines.append(
            '<LI>封面<OBJECT type="text/sitemap">'
            '<param name="Local" value="..\\html\\cover.html"></OBJECT></LI>\n'
        )
        hhk_lines.append(
            '<LI>目录<OBJECT type="text/sitemap">'
            '<param name="Local" value="..\\html\\index.html"></OBJECT></LI>\n'
        )
        for i, ch in enumerate(self.chapter_data, 1):
            hhk_lines.append(
                f'<LI>{ch["title"]}<OBJECT type="text/sitemap">'
                f'<param name="Local" value="..\\html\\chapter_{i:03d}.html">'
                "</OBJECT></LI>\n"
            )
        hhk_lines.append("</UL>\n</BODY>\n</HTML>")
        self._write(hhk_file, "".join(hhk_lines))

        hhp_lines = [
            "[OPTIONS]\n",
            f"Title={self.novel_title}\n",
            "Default Topic=..\\html\\cover.html\n",
            "Contents file=novel.hhc\n",
            "Index file=novel.hhk\n",
            "Compiled file=novel.chm\n",
            "Language=0x804\n",
            "Full-text search=Yes\n",
            "\n[FILES]\n",
            "..\\html\\cover.html\n",
            "..\\html\\index.html\n",
            "..\\html\\style.css\n",
        ]
        for i in range(1, len(self.chapter_data) + 1):
            hhp_lines.append(f"..\\html\\chapter_{i:03d}.html\n")
        self._write(hhp_file, "".join(hhp_lines))

        self.log.debug("HHC工程已生成: %s", hhp_file)
        return hhp_file

    def find_hhc(self):
        if self.hhc_path and os.path.isfile(self.hhc_path):
            return self.hhc_path

        candidates = [
            r"C:\Program Files (x86)\HTML Help Workshop\hhc.exe",
            r"C:\Program Files\HTML Help Workshop\hhc.exe",
            r"D:\HTML Help Workshop\hhc.exe",
            r"D:\Program Files\HTML Help Workshop\hhc.exe",
            r"E:\HTML Help Workshop\hhc.exe",
        ]
        try:
            programdata_hhk = subprocess.run(
                ["where.exe", "hhc.exe"],
                capture_output=True, text=True, timeout=5, encoding="gbk"
            )
            if programdata_hhk.returncode == 0:
                for line in programdata_hhk.stdout.strip().splitlines():
                    line = line.strip()
                    if line and os.path.isfile(line):
                        candidates.insert(0, line)
        except Exception:
            pass

        for c in candidates:
            if os.path.isfile(c):
                self.log.debug("找到 hhc.exe: %s", c)
                return c

        tools_dir = Path(__file__).parent / "tools"
        local_hhc = tools_dir / "hhc.exe"
        if local_hhc.is_file():
            return str(local_hhc)

        return None

    def compile_chm(self, hhp_path):
        hhc_exe = self.find_hhc()
        if not hhc_exe:
            self.log.warning("跳过 CHM 编译: 未找到 hhc.exe (请安装 HTML Help Workshop 或用 --hhc 指定)")
            return False

        import shutil as _shutil

        self.log.debug("开始 CHM 编译: hhc.exe = %s", hhc_exe)

        out_dir = self.output_dir.resolve()
        chm_dir = self.chm_dir.resolve()
        build_dir = Path(tempfile.mkdtemp(prefix="novel_build_"))
        self.log.debug("临时构建目录: %s", build_dir)

        try:
            (build_dir / "html").mkdir()
            _shutil.copytree(str(self.html_dir.resolve()), str(build_dir / "html"),
                             dirs_exist_ok=True)

            hhc_src = (chm_dir / "novel.hhc").read_text(encoding="utf-8")
            hhk_src = (chm_dir / "novel.hhk").read_text(encoding="utf-8")
            hhp_src = (chm_dir / "novel.hhp").read_text(encoding="utf-8")

            hhc_src = hhc_src.replace("..\\html\\", "html/").replace("../html/", "html/")
            hhk_src = hhk_src.replace("..\\html\\", "html/").replace("../html/", "html/")
            hhp_src = hhp_src.replace("..\\html\\", "html/").replace("../html/", "html/")

            (build_dir / "novel.hhc").write_text(hhc_src, encoding="gbk", errors="replace")
            (build_dir / "novel.hhk").write_text(hhk_src, encoding="gbk", errors="replace")
            (build_dir / "novel.hhp").write_text(hhp_src, encoding="gbk", errors="replace")

            STARTUPINFO = subprocess.STARTUPINFO()
            STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            STARTUPINFO.wShowWindow = 0

            import threading as _threading

            def _dismiss_hhc_popup():
                import ctypes
                WM_CLOSE = 0x0010
                IsWindowVisible = ctypes.windll.user32.IsWindowVisible
                GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
                GetWindowText = ctypes.windll.user32.GetWindowTextW
                EnumWindows = ctypes.windll.user32.EnumWindows
                PostMessageW = ctypes.windll.user32.PostMessageW
                IsWindow = ctypes.windll.user32.IsWindow
                time.sleep(1.0)
                buf = ctypes.create_unicode_buffer(256)
                def _enum_cb(hwnd, _):
                    if IsWindowVisible(hwnd):
                        length = GetWindowTextLength(hwnd)
                        if length > 0 and length < 200:
                            GetWindowText(hwnd, buf, 256)
                            title = buf.value.lower()
                            if "compilation" in title or "hhc" in title:
                                PostMessageW(hwnd, WM_CLOSE, 0, 0)
                    return True
                CMPFUNC = ctypes.WINFUNCTYPE(
                    ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
                )
                EnumWindows(CMPFUNC(_enum_cb), 0)

            popup_killer = _threading.Thread(target=_dismiss_hhc_popup, daemon=True)
            popup_killer.start()

            result = subprocess.run(
                [hhc_exe, str(build_dir / "novel.hhp")],
                capture_output=True,
                cwd=str(build_dir),
                timeout=180,
                startupinfo=STARTUPINFO
            )

            popup_killer.join(timeout=2.0)

            stdout = result.stdout.decode("gbk", errors="replace") if result.stdout else ""
            stderr = result.stderr.decode("gbk", errors="replace") if result.stderr else ""
            for line in (stdout + stderr).strip().splitlines():
                line = line.strip()
                if line and "Compile time" not in line and "Compression" not in line:
                    self.log.debug("hhc: %s", line)

            time.sleep(0.3)

            built_chm = build_dir / "novel.chm"
            if not built_chm.is_file():
                for logf in build_dir.glob("*.log"):
                    self.log.debug("hhc log: %s", logf.read_text(encoding="gbk", errors="replace")[:500])
                self.log.warning("CHM 编译失败: 未生成 CHM 文件")
                return False

            title_safe = safe_filename(self.novel_title)
            final_chm = out_dir / f"{title_safe}.chm"

            for attempt in range(5):
                try:
                    if final_chm.exists():
                        final_chm.unlink()
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                try:
                    subprocess.run(["taskkill", "/F", "/IM", "hh.exe"],
                                   capture_output=True, timeout=5)
                    time.sleep(0.8)
                    if final_chm.exists():
                        final_chm.unlink()
                except Exception:
                    pass

            for attempt in range(10):
                try:
                    if final_chm.exists():
                        final_chm.unlink()
                    _shutil.copy2(str(built_chm), str(final_chm))
                    break
                except OSError:
                    time.sleep(0.8)
            else:
                self.log.warning("CHM 编译成功但无法写入 (可能被 HH.EXE 占用), 临时路径: %s", built_chm)
                return False

            size_kb = final_chm.stat().st_size / 1024
            self.log.info("CHM 编译成功: %s (%.1f KB)", final_chm, size_kb)
            return True

        finally:
            try:
                _shutil.rmtree(str(build_dir), ignore_errors=True)
            except Exception:
                pass

    def run(self):
        if not self.crawl():
            return False

        self.generate_html()
        self.generate_single_html()
        hhp_path = self.generate_hhc_project()
        chm_ok = self.compile_chm(hhp_path)

        final_chm = list(self.output_dir.glob("*.chm"))

        if chm_ok and final_chm:
            self._cleanup()

        if final_chm:
            self.log.info("完成 《%s》 → %s", self.novel_title, final_chm[0])
        else:
            self.log.info("完成 《%s》 (CHM 未生成)", self.novel_title)
        return bool(final_chm)

    def _cleanup(self):
        import shutil as _shutil
        html_dir = self.html_dir
        chm_dir = self.chm_dir
        output_dir = self.output_dir
        try:
            if html_dir and html_dir.is_dir():
                _shutil.rmtree(str(html_dir), ignore_errors=True)
                self.log.debug("清理: 删除 html/ 目录")
            if chm_dir and chm_dir.is_dir():
                _shutil.rmtree(str(chm_dir), ignore_errors=True)
                self.log.debug("清理: 删除 chm/ 目录")
            if output_dir and output_dir.is_dir():
                for f in output_dir.glob("*.html"):
                    try:
                        f.unlink()
                        self.log.debug("清理: 删除 %s", f.name)
                    except OSError:
                        pass
                for f in output_dir.glob("*.css"):
                    try:
                        f.unlink()
                    except OSError:
                        pass
        except Exception as e:
            self.log.debug("清理警告: %s", e)


def main():
    parser = argparse.ArgumentParser(
        description="小说爬虫 - 自动爬取小说并保存为CHM格式\n"
                    "无参数启动 = 全自动遍历所有备选站点\n"
                    "有 URL    = 单本或批量爬取指定页面"
    )
    parser.add_argument("url", nargs="?", default=None,
                        help="小说目录页URL (单本) 或 分类列表页URL (批量)\n"
                             "留空则自动遍历所有备选小说站点")
    parser.add_argument("-o", "--output", default="output",
                        help="输出目录 (默认: output, 全自动模式下默认: auto_output)")
    parser.add_argument(
        "-s", "--site", default=None,
        choices=list(SITE_PROFILES.keys()),
        help="网站模板 (默认根据URL自动检测)"
    )
    parser.add_argument("-w", "--workers", type=int, default=8,
                        help="并发线程数 (默认: 8)")
    parser.add_argument("-d", "--delay", type=float, default=0.5,
                        help="每章请求间隔秒数 (默认: 0.5)")
    parser.add_argument("--hhc", default=None, help="hhc.exe 的完整路径")
    parser.add_argument("--bulk", action="store_true",
                        help="批量模式: 从起始URL自动发现所有小说并逐个爬取")
    parser.add_argument("--max-novels", type=int, default=0,
                        help="批量模式下最多爬取的小说数 (默认: 0 = 全站不限)")
    parser.add_argument("--no-chm", action="store_true",
                        help="批量模式下只生成HTML, 不编译CHM")
    parser.add_argument("--auto", action="store_true",
                        help="强制启用全自动模式 (遍历所有备选站点)")
    parser.add_argument("--max-chapters", type=int, default=0,
                        help="每本小说最多爬取章节数 (默认: 0 = 不限制)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="详细模式 (DEBUG): 打印章节下载细节 (404/重试/分页)")

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    setup_logging(level)

    if args.auto or args.url is None:
        auto_out = args.output if args.output != "output" else "auto_output"
        print("检测到无参数启动 → 全自动遍历所有备选小说站点")
        print(f"可用 --auto 参数显式指定, 或 python crawler.py <url> 爬取单个站点")
        try:
            success = auto_run(
                output_dir=auto_out,
                max_novels_per_site=args.max_novels if args.max_novels > 0 else None,
                max_workers=args.workers,
                delay=args.delay,
                hhc_path=args.hhc,
                compile_chm=not args.no_chm,
                max_chapters=args.max_chapters,
            )
            sys.exit(0 if success else 1)
        except KeyboardInterrupt:
            print("\n用户中断")
            sys.exit(130)

    if args.bulk:
        try:
            success = batch_crawl(
                start_urls=args.url,
                output_dir=args.output,
                site_key=args.site,
                max_novels=args.max_novels if args.max_novels > 0 else None,
                max_workers=args.workers,
                delay=args.delay,
                hhc_path=args.hhc,
                compile_chm=not args.no_chm,
                max_chapters=args.max_chapters,
            )
            sys.exit(0 if success else 1)
        except KeyboardInterrupt:
            print("\n用户中断")
            sys.exit(130)

    print("=" * 60)
    print("  小说爬虫 - Novel Crawler v2")
    print("=" * 60)
    print(f"目标URL: {args.url}")
    print(f"输出目录: {args.output}")
    print(f"并发数:   {args.workers}")
    print(f"章节限制: {args.max_chapters if args.max_chapters > 0 else '不限制'}")
    print("=" * 60)

    site_note = detect_site_by_url(args.url)
    if site_note in ("qidian", "zongheng"):
        print(f"\n[提示] 检测到 {site_note} 站点, 该网站有严格反爬保护, 可能无法直接抓取")
        print("       建议换用 biqukan (笔趣看) 等反爬较松的网站")
        print("       如仍需尝试, 请确保已处理好反爬\n")

    crawler = NovelCrawler(
        catalog_url=args.url,
        output_dir=args.output,
        site_key=args.site,
        max_workers=args.workers,
        delay=args.delay,
        hhc_path=args.hhc,
        max_chapters=args.max_chapters,
    )

    try:
        success = crawler.run()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n用户中断")
        sys.exit(130)


if __name__ == "__main__":
    main()