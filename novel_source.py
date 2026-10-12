# -*- coding: utf-8 -*-
"""小说简介查询 · 数据源适配层

支持站点：
  * 晋江文学城  https://www.jjwxc.net
  * 长佩文学    https://www.gongzicp.com

仅依赖 Python 标准库。
"""

from __future__ import annotations

import difflib
import gzip
import html as _html
import json
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = 20
HOST_INTERVAL = 0.7          # 同一站点两次请求的最小间隔（秒），对站点友好
CACHE_TTL = 600              # 结果缓存时间（秒）

_lock = threading.Lock()
_last_hit: dict[str, float] = {}
_cache: dict[str, tuple[float, object]] = {}

SOURCES = {
    "jjwxc": "晋江文学城",
    "changpei": "长佩文学",
}


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _ssl_context(verify: bool = True) -> ssl.SSLContext:
    if verify:
        return ssl.create_default_context()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _throttle(host: str) -> None:
    with _lock:
        wait = HOST_INTERVAL - (time.time() - _last_hit.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.time()


def _fetch(url: str, *, referer: str | None = None) -> bytes:
    """带节流、重试、证书回退的 GET。"""
    host = urllib.parse.urlsplit(url).netloc
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/json,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    }
    if referer:
        headers["Referer"] = referer

    last_error: Exception | None = None
    for attempt in range(3):
        _throttle(host)
        req = urllib.request.Request(url, headers=headers)
        for verify in (True, False):
            try:
                with urllib.request.urlopen(
                    req, timeout=TIMEOUT, context=_ssl_context(verify)
                ) as resp:
                    return _decompress(resp.read(), resp.headers.get("Content-Encoding"))
            except ssl.SSLError as exc:            # 证书问题 -> 关闭校验重试一次
                last_error = exc
                continue
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
                last_error = exc
                break
        time.sleep(0.6 * (attempt + 1))
    raise ConnectionError(f"请求失败：{url}（{last_error}）")


def _decompress(raw: bytes, encoding: str | None) -> bytes:
    """按 Content-Encoding 解压；部分站点不声明却发 gzip，故再做一次魔数兜底。"""
    scheme = (encoding or "").strip().lower()
    try:
        if scheme == "gzip":
            return gzip.decompress(raw)
        if scheme == "deflate":
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
        if not scheme and raw[:2] == b"\x1f\x8b":
            return gzip.decompress(raw)
    except (OSError, zlib.error):
        return raw
    return raw


def _decode(raw: bytes) -> str:
    """按声明编码 / UTF-8 / GB18030 择优选解码（晋江不同页面编码不一致）。"""
    candidates: list[str] = []
    m = re.search(rb'charset=["\']?([\w\-]+)', raw[:5000], re.I)
    if m:
        candidates.append(m.group(1).decode("ascii", "replace"))
    candidates += ["utf-8", "gb18030"]

    best, best_score = None, None
    for enc in candidates:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            try:
                text = raw.decode(enc, "replace")
            except LookupError:
                continue
        score = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff") - 50 * text.count("\ufffd")
        if best_score is None or score > best_score:
            best, best_score = text, score
    return best if best is not None else raw.decode("utf-8", "replace")


def _strip_tags(fragment: str, *, block_tags: str = "br|/p|/div|/li") -> str:
    text = re.sub(r"(?i)<(%s)\s*/?>|<(%s)>" % (block_tags, block_tags), "\n", fragment)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = _html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _extract_element(source: str, start: int) -> str:
    """从 start（某元素 '<' 处）取出该元素完整外层 HTML，含同名嵌套标签。"""
    m = re.match(r"<([a-zA-Z0-9]+)", source[start:])
    if not m:
        return ""
    tag = m.group(1)
    pattern = re.compile(r"<%s\b[^>]*>|</%s>" % (tag, tag), re.I)
    depth = 0
    for t in pattern.finditer(source, start):
        token = t.group(0)
        if token.startswith("</"):
            depth -= 1
        elif token.endswith("/>"):
            pass
        else:
            depth += 1
        if depth == 0:
            return source[start:t.end()]
    return source[start:]


def _find_element(source: str, pattern: str) -> str:
    m = re.search(pattern, source, re.I)
    return _extract_element(source, m.start()) if m else ""


def _attr(fragment: str, name: str) -> str:
    m = re.search(r'\b%s\s*=\s*"([^"]*)"' % name, fragment, re.I)
    if not m:
        m = re.search(r"\b%s\s*=\s*'([^']*)'" % name, fragment, re.I)
    return _html.unescape(m.group(1)).strip() if m else ""


def _inner(fragment: str) -> str:
    m = re.match(r"(?s)<[a-zA-Z0-9][^>]*>(.*)</[a-zA-Z0-9]+>\s*$", fragment.strip())
    return m.group(1) if m else fragment


def _prop(source: str, itemprop: str) -> str:
    m = re.search(
        r'<[^>]*itemprop\s*=\s*["\']%s["\'][^>]*>(.*?)</' % re.escape(itemprop),
        source,
        re.S | re.I,
    )
    return _strip_tags(m.group(1)).strip() if m else ""


# --------------------------------------------------------------------------- #
# 书名归一化与匹配度
# --------------------------------------------------------------------------- #
_NOISE = set("《》〈〉【】〔〕[]()（）「」『』\"'“”‘’`·．.,。、!！?？:：;；~～ \t\u3000-_—+/\\|")


def normalize_title(text: str) -> str:
    text = (text or "").casefold()
    return "".join(ch for ch in text if ch not in _NOISE)


def clean_query(text: str) -> str:
    """去掉用户输入两端可能带的书名号、引号、空格。"""
    text = (text or "").strip()
    stripped = re.sub(r'^[《〈【〔\[（(「『"\'\s]+', "", text)
    stripped = re.sub(r'[》〉】〕\]）)」』"\'\s]+$', "", stripped)
    return stripped.strip() or text


def match_level(query: str, title: str) -> dict:
    """比较用户输入与候选题名，给出匹配等级。"""
    q, t = normalize_title(query), normalize_title(title)
    if not q or not t:
        return {"key": "unknown", "label": "—", "score": 0.0}
    if q == t:
        return {"key": "exact", "label": "精确匹配", "score": 1.0}
    if q in t or t in q:
        ratio = min(len(q), len(t)) / max(len(q), len(t))
        if ratio >= 0.6:
            return {"key": "strong", "label": "高度匹配", "score": round(0.80 + 0.15 * ratio, 3)}
        # 包含关系但长度相差悬殊（例如输入一长串而站内只有短标题），只算相关结果
        return {"key": "loose", "label": "相关结果", "score": round(0.30 + 0.40 * ratio, 3)}
    ratio = difflib.SequenceMatcher(None, q, t).ratio()
    if ratio >= 0.62:
        return {"key": "likely", "label": "疑似匹配", "score": round(ratio, 3)}
    return {"key": "loose", "label": "相关结果", "score": round(ratio, 3)}


def match_level_author(query: str, author: str) -> dict:
    """按作者名比较，给出匹配等级（作者搜索专用）。"""
    q, a = normalize_title(query), normalize_title(author)
    if not q or not a:
        return {"key": "unknown", "label": "—", "score": 0.0}
    if q == a:
        return {"key": "exact", "label": "作者精确匹配", "score": 1.0}
    if q in a or a in q:
        ratio = min(len(q), len(a)) / max(len(q), len(a))
        if ratio >= 0.6:
            return {"key": "strong", "label": "作者高度匹配", "score": round(0.80 + 0.15 * ratio, 3)}
        return {"key": "loose", "label": "该作者的相关作品", "score": round(0.30 + 0.40 * ratio, 3)}
    ratio = difflib.SequenceMatcher(None, q, a).ratio()
    if ratio >= 0.62:
        return {"key": "likely", "label": "疑似作者", "score": round(ratio, 3)}
    return {"key": "loose", "label": "该作者的相关作品", "score": round(ratio, 3)}


_LEVEL_ORDER = {"exact": 0, "strong": 1, "likely": 2, "loose": 3, "unknown": 4}


def _sort_key(item: dict):
    return (_LEVEL_ORDER[item["match"]["key"]], -item["match"]["score"])


# --------------------------------------------------------------------------- #
# 晋江文学城
# --------------------------------------------------------------------------- #
JJ_SEARCH = "https://www.jjwxc.net/search.php?%s"


def search_jjwxc(keyword: str, limit: int = 15, mode: str = "title") -> list[dict]:
    # 注意：晋江的搜索参数必须按 GB18030 编码，用 UTF-8 会搜出完全无关的结果
    # t=1 按书名搜索，t=2 按作者搜索；两种结果的 HTML 结构完全一致，解析代码通用
    url = JJ_SEARCH % urllib.parse.urlencode(
        {"kw": keyword, "t": 2 if mode == "author" else 1, "ord": "novelbefavoritedcount"},
        encoding="gb18030",
    )
    page = _decode(_fetch(url, referer="https://www.jjwxc.net/"))

    blocks = re.split(r'(?i)<h3\s+class="title">', page)[1:]
    results: list[dict] = []
    for block in blocks:
        head = re.search(
            r'href="[^"]*onebook\.php\?novelid=(\d+)"[^>]*>(.*?)</a>',
            block,
            re.S,
        )
        if not head:
            continue
        novel_id = head.group(1)
        title = _strip_tags(head.group(2))
        if not title:
            continue

        intro_m = re.search(r'(?is)<div class="intro">(.*?)</div>', block)
        intro = _strip_tags(intro_m.group(1)) if intro_m else ""

        info_m = re.search(r'(?is)<div class="info">(.*?)</div>', block)
        info = info_m.group(1) if info_m else ""

        author = ""
        author_m = re.search(r'oneauthor\.php\?authorid=\d+"[^>]*>(.*?)</a>', info, re.S)
        if author_m:
            author = _strip_tags(author_m.group(1))

        update = ""
        date_m = re.search(r"<font[^>]*>\s*\(([^)]+)\)", block, re.S)
        if date_m:
            update = date_m.group(1).strip()

        extra = {}
        plain = _strip_tags(re.sub(r"(?is)<a\b.*?</a>", "", info))
        plain = plain.replace("作者：", "").strip()
        for part in re.split(r"[┃|]", plain):
            part = part.strip()
            if "：" in part:
                k, v = part.split("：", 1)
                if v.strip():
                    extra.setdefault(k.strip(), v.strip())

        results.append({
            "source": "jjwxc",
            "source_name": SOURCES["jjwxc"],
            "id": novel_id,
            "title": title,
            "author": author,
            "intro": intro,
            "url": f"https://www.jjwxc.net/onebook.php?novelid={novel_id}",
            "cover": "",
            "updated": update,
            "extra": extra,
            "match": (match_level_author(keyword, author) if mode == "author"
                      else match_level(keyword, title)),
        })
        if len(results) >= limit:
            break

    return sorted(results, key=_sort_key)


def detail_jjwxc(novel_id: str) -> dict:
    url = f"https://www.jjwxc.net/onebook.php?novelid={novel_id}"
    page = _decode(_fetch(url, referer="https://www.jjwxc.net/"))

    title = _prop(page, "name") or _strip_tags(_find_element(page, r'<h1[^>]*itemprop="name"'))
    intro_el = _find_element(page, r'<div[^>]*id="novelintro"')
    intro = _strip_tags(_inner(intro_el)) if intro_el else ""

    author = _prop(page, "author")
    if not author:
        m = re.search(r"<meta\s+name=\"Author\"\s+content=\"([^\"]*)\"", page, re.I)
        author = _html.unescape(m.group(1)) if m else ""

    cover = ""
    img = _find_element(page, r'<img[^>]*class="[^"]*noveldefaultimage[^"]*"')
    if img:
        cover = _attr(img, "src") or _attr(img, "_src")
    if not cover:
        m = re.search(r'(https?://[^"\']*novelimage\.php\?novelid=%s[^"\']*)' % re.escape(novel_id), page)
        cover = _html.unescape(m.group(1)) if m else ""
    if cover.startswith("//"):
        cover = "https:" + cover

    # 内容标签：定位承载“内容标签”的那个 smallreadbody 区块后再取 bq 链接
    tags: list[str] = []
    label = re.search(r"内容标签[：:]", page)
    if label:
        div_start = page.rfind("<div", 0, label.start())
        region = _extract_element(page, div_start) if div_start != -1 else ""
        if "bq=" not in region:
            region = page[label.start():label.start() + 3000]
        for t in re.findall(r"bookbase\.php\?bq=\d+[^>]*>([^<]{1,20})</a>", region):
            t = _html.unescape(t).strip()
            if t and t not in tags:
                tags.append(t)

    stats = {
        "类型": _prop(page, "genre"),
        "进度": _prop(page, "updataStatus"),
        "字数": _prop(page, "wordCount"),
        "所属系列": _prop(page, "series"),
        "收藏": _prop(page, "collectedCount"),
        "积分": _prop(page, "scoreCount"),
        "书评": _prop(page, "reviewCount"),
    }
    stats = {k: v for k, v in stats.items() if v}

    # 主角 / 配角 / 其它：页面可见区域带链接，改从 meta keywords 的纯文本里取
    meta = re.search(r'<meta\s+name="[Kk]eywords"\s+content="([^"]*)"', page)
    blob = _html.unescape(meta.group(1)) if meta else page
    roles = {}
    for name in ("主角", "配角", "其它"):
        mm = re.search(r"%s[：:]\s*([^┃|]*)" % name, blob)
        value = mm.group(1).strip().rstrip("　 ") if mm else ""
        if value:
            roles[name] = value

    return {
        "source": "jjwxc",
        "source_name": SOURCES["jjwxc"],
        "id": str(novel_id),
        "title": title,
        "author": author,
        "intro": intro,
        "url": url,
        "cover": cover,
        "tags": tags,
        "stats": stats,
        "roles": roles,
    }


# --------------------------------------------------------------------------- #
# 长佩文学
# --------------------------------------------------------------------------- #
CP_API = "https://www.gongzicp.com/webapi"


def _cp_json(url: str):
    raw = _fetch(url, referer="https://www.gongzicp.com/")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _search_changpei_author(keyword: str, limit: int = 15) -> list[dict]:
    """长佩作者搜索：/search/novelAndAuthor 会同时给出作者信息和 TA 的作品列表。"""
    url = CP_API + "/search/novelAndAuthor?" + urllib.parse.urlencode({"k": keyword})
    data = (_cp_json(url) or {}).get("data") or {}
    if not isinstance(data, dict):
        data = {}

    author = ((data.get("author") or {}) or {}).get("author_nickname", "") or ""
    novels = data.get("author_novels") or []
    if isinstance(novels, dict):
        novels = novels.get("list") or []

    results: list[dict] = []
    for item in novels[:limit]:
        novel_id = str(item.get("novel_id", ""))
        title = item.get("novel_name", "") or ""
        if not novel_id or not title:
            continue
        results.append({
            "source": "changpei",
            "source_name": SOURCES["changpei"],
            "id": novel_id,
            "title": title,
            "author": author,
            "intro": item.get("novel_desc", "") or "",
            "url": f"https://www.gongzicp.com/novel-{novel_id}.html",
            "cover": (item.get("novel_cover", "") or "").split("?")[0],
            "updated": "",
            "extra": {"字数（万）": item.get("novel_wordnumber", "") or ""},
            "match": match_level_author(keyword, author),
        })

    if not results and author:
        # 该接口偶尔不返回作品列表，退回书名搜索再按作者过滤
        q = normalize_title(keyword)
        for item in search_changpei(keyword, limit=60, mode="title"):
            if q and q in normalize_title(item["author"]):
                item["match"] = match_level_author(keyword, item["author"])
                results.append(item)
    return sorted(results, key=_sort_key)


def search_changpei(keyword: str, limit: int = 15, mode: str = "title") -> list[dict]:
    if mode == "author":
        return _search_changpei_author(keyword, limit)
    url = CP_API + "/search/novels?" + urllib.parse.urlencode(
        {"k": keyword, "page": 1, "search_point": 0}
    )
    payload = _cp_json(url)
    items = ((payload.get("data") or {}).get("list")) or []

    results: list[dict] = []
    for item in items[:limit]:
        novel_id = str(item.get("novel_id", ""))
        title = item.get("novel_name", "") or ""
        if not novel_id or not title:
            continue
        tags = item.get("novel_all_tag") or []
        if isinstance(tags, str):
            tags = [t for t in re.split(r"[,，]", tags) if t]
        results.append({
            "source": "changpei",
            "source_name": SOURCES["changpei"],
            "id": novel_id,
            "title": title,
            "author": item.get("novel_author", "") or "",
            "intro": item.get("novel_desc", "") or "",
            "url": f"https://www.gongzicp.com/novel-{novel_id}.html",
            "cover": (item.get("novel_cover", "") or "").split("?")[0],
            "updated": item.get("novel_uptime", "") or "",
            "extra": {
                "类型": item.get("novel_type", "") or "",
                "标签": "、".join(tags),
            },
            "tags": tags,
            "match": match_level(keyword, title),
        })
    return sorted(results, key=_sort_key)


def detail_changpei(novel_id: str) -> dict:
    url = CP_API + "/novel/novelInfo?" + urllib.parse.urlencode({"id": novel_id})
    data = (_cp_json(url) or {}).get("data") or {}
    if not isinstance(data, dict):
        data = {}

    intro = _strip_tags(data.get("novel_info") or "")
    if not intro:
        intro = (data.get("novel_desc") or "").strip()

    cover = (data.get("novel_cover") or "").split("?")[0]
    words = data.get("novel_wordnumber") or ""
    tags = [t for t in (data.get("novel_tag_txt") or "").split(",") if t]

    stats = {
        "类型": data.get("type_names") or "",
        "进度": data.get("novel_process") or "",
        "字数": f"{words}字" if str(words).isdigit() else str(words or ""),
        "人气": data.get("novel_allpopu") or "",
        "最新章": data.get("novel_newcname") or "",
        "完结时间": data.get("novel_uptime") or "",
    }
    stats = {k: v for k, v in stats.items() if v}

    return {
        "source": "changpei",
        "source_name": SOURCES["changpei"],
        "id": str(novel_id),
        "title": data.get("novel_name", "") or "",
        "author": data.get("author_nickname") or "",
        "intro": intro,
        "url": f"https://www.gongzicp.com/novel-{novel_id}.html",
        "cover": cover,
        "tags": tags,
        "stats": stats,
        "roles": {},
    }


# --------------------------------------------------------------------------- #
# 统一入口
# --------------------------------------------------------------------------- #
def _cache_get(key: str):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    return None


def _cache_put(key: str, value):
    _cache[key] = (time.time(), value)


def search_all(keyword: str, source: str = "all", mode: str = "title") -> dict:
    keyword = clean_query(keyword)
    mode = "author" if mode == "author" else "title"
    if not keyword:
        return {"keyword": keyword, "mode": mode, "results": [], "errors": []}

    key = f"search::{source}::{mode}::{keyword}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    results: list[dict] = []
    errors: list[str] = []
    targets = SOURCES if source == "all" else {source: SOURCES[source]}
    for code, name in targets.items():
        try:
            results += (search_jjwxc if code == "jjwxc" else search_changpei)(keyword, mode=mode)
        except Exception as exc:                                      # noqa: BLE001
            errors.append(f"{name} 查询失败：{exc}")

    results.sort(key=_sort_key)
    payload = {"keyword": keyword, "mode": mode, "results": results, "errors": errors}
    _cache_put(key, payload)
    return payload


def get_detail(source: str, novel_id: str) -> dict:
    key = f"detail::{source}::{novel_id}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    detail = (detail_jjwxc if source == "jjwxc" else detail_changpei)(novel_id)
    _cache_put(key, detail)
    return detail


# --------------------------------------------------------------------------- #
# 命令行自测：  python novel_source.py 魔道祖师
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    mode = "author" if "--author" in args else "title"
    kw = " ".join(a for a in args if a != "--author") or "魔道祖师"
    data = search_all(kw, mode=mode)
    for err in data["errors"]:
        print("[!]", err)
    for item in data["results"][:10]:
        print(f'[{item["match"]["label"]}] {item["source_name"]} 《{item["title"]}》 / {item["author"]}')
        print(f'    {item["intro"][:60]}')
    if data["results"]:
        top = data["results"][0]
        print("\n--- 详情 ---")
        print(json.dumps(get_detail(top["source"], top["id"]), ensure_ascii=False, indent=1)[:1500])
