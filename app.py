# -*- coding: utf-8 -*-
"""小说简介查询 · 本地小程序

用法：
    python app.py                    # 启动并自动用 Microsoft Edge 打开页面
    python app.py --port 9000        # 指定端口
    python app.py --no-browser       # 不自动打开浏览器
    python app.py --browser default  # 改用系统默认浏览器
    python app.py --mode window      # 显示控制小窗口（tray / window / background）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import novel_source as ns  # noqa: E402

IDLE_LIMIT = 180.0        # 秒：页面关闭后多久自动退出
_last_seen = time.time()
_last_seen_lock = threading.Lock()
_QUIT_HOOKS: list = []


def _register_quit_hook(hook) -> None:
    _QUIT_HOOKS.append(hook)


def _request_quit(server) -> None:
    """统一退出入口：停掉 HTTP 服务，并让托盘 / 控制窗口也一起收摊。"""
    threading.Thread(target=server.shutdown, daemon=True).start()
    for hook in list(_QUIT_HOOKS):
        try:
            hook()
        except Exception:                                         # noqa: BLE001
            pass


def _touch() -> None:
    """刷新“还活着”的时间戳（页面心跳或任意请求都会调用）。"""
    global _last_seen
    with _last_seen_lock:
        _last_seen = time.time()


def _idle_seconds() -> float:
    with _last_seen_lock:
        return time.time() - _last_seen


def _resource_path(name: str) -> str:
    """兼容脚本运行与 PyInstaller 打包后的资源定位。"""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


def _say(message: str) -> None:
    """有控制台就打印；打包成无控制台 exe 时静默跳过。"""
    try:
        if sys.stdout is not None:
            print(message, flush=True)
    except Exception:                                             # noqa: BLE001
        pass


def _alert(title: str, message: str) -> None:
    """无控制台时用系统弹窗告知（仅 Windows 生效）。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x40)
    except Exception:                                             # noqa: BLE001
        pass


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>小说简介查询 · 晋江 / 长佩</title>
<style>
  :root {
    --bg: #0f1418;
    --panel: #171e24;
    --panel-2: #1e272e;
    --line: #2b3740;
    --text: #e8eef3;
    --muted: #93a4b1;
    --accent: #2ec4c4;
    --jj: #4caf6d;
    --cp: #2ec4c4;
    --warn: #e6b455;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; color: var(--text);
    background: radial-gradient(1200px 600px at 15% -10%, #1b2a33 0%, var(--bg) 55%) no-repeat, var(--bg);
    font: 15px/1.7 "PingFang SC", "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
  }
  .wrap { max-width: 900px; margin: 0 auto; padding: 48px 20px 80px; }
  header h1 { font-size: 26px; margin: 0 0 6px; letter-spacing: .5px; }
  header p { margin: 0 0 26px; color: var(--muted); font-size: 13.5px; }
  .searchbar { display: flex; gap: 10px; }
  input[type=text] {
    flex: 1; padding: 14px 16px; font-size: 16px; color: var(--text);
    background: var(--panel); border: 1px solid var(--line); border-radius: 12px; outline: none;
    transition: border-color .15s, box-shadow .15s;
  }
  input[type=text]:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(46,196,196,.12); }
  button.go {
    padding: 0 26px; font-size: 15px; font-weight: 600; color: #06232a; cursor: pointer;
    background: linear-gradient(180deg, #43dcdc, #23b3b3); border: 0; border-radius: 12px;
    transition: transform .1s, filter .15s;
  }
  button.go:hover { filter: brightness(1.06); }
  button.go:active { transform: translateY(1px); }
  button.go[disabled] { filter: grayscale(.6) brightness(.8); cursor: default; }
  .chips { display: flex; gap: 8px; margin: 14px 0 4px; flex-wrap: wrap; }
  .chip {
    padding: 6px 14px; font-size: 13px; color: var(--muted); cursor: pointer;
    background: var(--panel); border: 1px solid var(--line); border-radius: 999px;
  }
  .chip.active { color: #06232a; background: var(--accent); border-color: var(--accent); font-weight: 600; }
  .status { margin: 18px 2px 0; color: var(--muted); font-size: 13.5px; min-height: 22px; }
  .banner {
    margin-top: 18px; padding: 16px 18px; border-radius: 14px;
    background: linear-gradient(180deg, rgba(46,196,196,.10), rgba(46,196,196,.03));
    border: 1px solid rgba(46,196,196,.28);
  }
  .banner b { color: var(--accent); }
  .card {
    margin-top: 14px; padding: 16px 18px; border-radius: 14px;
    background: var(--panel); border: 1px solid var(--line);
    transition: border-color .15s, background .15s;
  }
  .card:hover { border-color: #3c4c58; }
  .card.open { background: var(--panel-2); border-color: rgba(46,196,196,.35); }
  .card .top { display: flex; gap: 14px; cursor: pointer; }
  .cover {
    width: 76px; height: 106px; flex: 0 0 76px; object-fit: cover; border-radius: 8px;
    background: #223039; border: 1px solid var(--line);
  }
  .cover.placeholder { display: flex; align-items: center; justify-content: center; color: #40525e; font-size: 11px; }
  .head { flex: 1; min-width: 0; }
  .titleline { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .title { font-size: 17px; font-weight: 700; }
  .badge { font-size: 11.5px; padding: 2px 8px; border-radius: 6px; border: 1px solid var(--line); color: var(--muted); }
  .badge.jj { color: var(--jj); border-color: rgba(76,175,109,.45); }
  .badge.cp { color: var(--cp); border-color: rgba(46,196,196,.45); }
  .badge.exact { color: #06232a; background: #6ee7b7; border-color: transparent; font-weight: 700; }
  .badge.strong { color: #2a1c00; background: var(--warn); border-color: transparent; font-weight: 700; }
  .badge.likely, .badge.loose, .badge.unknown { color: var(--muted); }
  .meta { color: var(--muted); font-size: 13px; margin-top: 3px; }
  .snippet { margin-top: 9px; color: #c7d5df; font-size: 13.5px; white-space: pre-wrap; }
  .detail { margin-top: 14px; border-top: 1px dashed var(--line); padding-top: 14px; }
  .intro { white-space: pre-wrap; font-size: 14.5px; color: #dbe6ee; }
  .kv { display: flex; flex-wrap: wrap; gap: 8px 16px; margin-top: 14px; font-size: 13px; color: var(--muted); }
  .kv i { font-style: normal; color: #7e929f; }
  .tags { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 12px; }
  .tag { font-size: 12px; padding: 3px 10px; border-radius: 999px; background: #22303a; color: #9fd8d8; }
  .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .act {
    padding: 6px 14px; font-size: 12.5px; color: var(--muted); cursor: pointer;
    background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px;
    transition: color .15s, border-color .15s;
  }
  .act:hover { color: var(--accent); border-color: rgba(46,196,196,.5); }
  .act.done { color: #06232a; background: var(--accent); border-color: var(--accent); font-weight: 600; }
  .act[disabled] { cursor: default; }
  .src-btn {
    display: inline-block; margin-top: 16px; padding: 8px 16px; font: inherit; font-size: 13.5px;
    color: #06232a; font-weight: 600; cursor: pointer; border: 0; border-radius: 8px;
    background: linear-gradient(180deg, #43dcdc, #23b3b3);
  }
  .src-btn:hover { filter: brightness(1.06); }
  .src-btn.done { background: var(--jj); }
  .chip-div { width: 1px; align-self: stretch; background: var(--line); margin: 2px 4px; }
  .err { margin-top: 14px; color: var(--warn); font-size: 13px; }
  .hint { margin-top: 26px; color: #6b7c88; font-size: 12.5px; }
  .footer {
    margin-top: 30px; padding-top: 16px; border-top: 1px solid var(--line);
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    color: #6b7c88; font-size: 12.5px;
  }
  .quit {
    padding: 7px 16px; font-size: 12.5px; color: var(--muted); cursor: pointer;
    background: var(--panel); border: 1px solid var(--line); border-radius: 999px;
  }
  .quit:hover { color: #ff9f9f; border-color: rgba(255,120,120,.5); }
  .spinner {
    width: 16px; height: 16px; border: 2px solid rgba(255,255,255,.25); border-top-color: var(--accent);
    border-radius: 50%; display: inline-block; vertical-align: -3px; margin-right: 8px; animation: spin .7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>小说简介查询</h1>
    <p>输入书名即可自动识别并给出简介，数据来源：晋江文学城 · 长佩文学</p>
  </header>

  <div class="searchbar">
    <input id="q" type="text" placeholder="例如：魔道祖师 / 撒野 / 某某" autocomplete="off" />
    <button class="go" id="go">查询</button>
  </div>
  <div class="chips" id="chips">
    <span class="chip active" data-mode="title">搜书名</span>
    <span class="chip" data-mode="author">搜作者</span>
    <span class="chip-div"></span>
    <span class="chip active" data-src="all">全部站点</span>
    <span class="chip" data-src="jjwxc">晋江文学城</span>
    <span class="chip" data-src="changpei">长佩文学</span>
  </div>

  <div class="status" id="status"></div>
  <div id="out"></div>

  <div class="hint">
    结果按匹配度排序（精确匹配 &gt; 高度匹配 &gt; 疑似匹配 &gt; 相关结果）。点击卡片展开完整文案，
    「复制简介」可直接粘贴分享，「前往原站」用 Microsoft Edge 打开原作页面。
  </div>

  <div class="footer">
    <span id="foot">程序正在后台运行，关闭本页面后会自动退出。</span>
    <button class="quit" id="quit">立即退出程序</button>
  </div>
</div>

<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => (s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let source = "all";
let mode = "title";
let seq = 0;
let items = {};     // "source:id" -> 搜索结果对象
let details = {};   // "source:id" -> 详情对象（用于取完整简介 / 原始链接）

$("chips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  if (chip.dataset.mode) {
    document.querySelectorAll(".chip[data-mode]").forEach((c) => c.classList.toggle("active", c === chip));
    mode = chip.dataset.mode;
  } else {
    document.querySelectorAll(".chip[data-src]").forEach((c) => c.classList.toggle("active", c === chip));
    source = chip.dataset.src;
  }
  $("q").placeholder = mode === "author"
    ? "例如：priest / 巫哲 / 卡比丘（按作者名搜索）"
    : "例如：魔道祖师 / 撒野 / 某某";
  if ($("q").value.trim()) run();
});
$("go").addEventListener("click", run);
$("q").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });

async function run() {
  const q = $("q").value.trim();
  if (!q) { $("q").focus(); return; }
  const me = ++seq;
  $("go").disabled = true;
  $("status").innerHTML = '<span class="spinner"></span>正在查询…';
  $("out").innerHTML = "";
  try {
    const data = await (await fetch(`/api/search?q=${encodeURIComponent(q)}&source=${source}&mode=${mode}`)).json();
    if (me !== seq) return;
    render(data);
  } catch (err) {
    if (me !== seq) return;
    $("status").textContent = "查询失败：" + err;
  } finally {
    if (me === seq) $("go").disabled = false;
  }
}

function render(data) {
  const list = data.results || [];
  const errs = data.errors || [];
  items = {};
  details = {};
  list.forEach((it) => { items[it.source + ":" + it.id] = it; });
  if (!list.length) {
    $("status").innerHTML = "没有找到相关作品。";
    $("out").innerHTML = `<div class="hint">可以试试：${mode === "author" ? "换用作者的常用笔名、或切换到「搜书名」" : "使用完整书名、去掉《》等符号、或切换到「全部站点」"}。</div>`
      + errs.map((e) => `<div class="err">${esc(e)}</div>`).join("");
    return;
  }
  const best = list[0];
  const bestText = mode === "author"
    ? `${esc(best.author || "佚名")} 的作品`
    : `《${esc(best.title)}》`;
  $("status").innerHTML = `共 ${list.length} 条结果 · 最佳匹配：<b style="color:var(--text)">${bestText}</b>`;
  $("out").innerHTML = list.map((item, i) => card(item, i)).join("")
    + errs.map((e) => `<div class="err">${esc(e)}</div>`).join("");

  document.querySelectorAll(".card .top").forEach((el) => {
    el.addEventListener("click", () => toggle(el.parentElement));
  });
  document.querySelectorAll(".card .act.copy").forEach((b) => {
    b.addEventListener("click", () => doCopy(b));
  });
  document.querySelectorAll(".card .act.open").forEach((b) => {
    b.addEventListener("click", () => doOpen(b, urlOf(b)));
  });
  toggle(document.querySelector(".card"), true);   // 自动展开最佳匹配
}

function card(item, index) {
  const srcCls = item.source === "jjwxc" ? "jj" : "cp";
  const cover = item.cover
    ? `<img class="cover" src="${esc(item.cover)}" referrerpolicy="no-referrer" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'cover placeholder',textContent:'无封面'}))" />`
    : `<div class="cover placeholder">无封面</div>`;
  return `
  <div class="card" data-source="${item.source}" data-id="${esc(item.id)}" data-index="${index}">
    <div class="top">
      ${cover}
      <div class="head">
        <div class="titleline">
          <span class="title">《${esc(item.title)}》</span>
          <span class="badge ${srcCls}">${esc(item.source_name)}</span>
          <span class="badge ${item.match.key}">${esc(item.match.label)}</span>
        </div>
        <div class="meta">${esc(item.author || "佚名")}${item.updated ? " · 更新 " + esc(item.updated) : ""}</div>
        <div class="snippet">${esc((item.intro || "（暂无简介）").slice(0, 120))}</div>
      </div>
    </div>
    <div class="actions">
      <button class="act copy" type="button">复制简介</button>
      <button class="act open" type="button">前往原站</button>
    </div>
    <div class="detail" hidden></div>
  </div>`;
}

async function toggle(cardEl, forceOpen) {
  if (!cardEl) return;
  const detail = cardEl.querySelector(".detail");
  const willOpen = forceOpen === true ? true : detail.hidden;
  if (!willOpen) { detail.hidden = true; cardEl.classList.remove("open"); return; }
  cardEl.classList.add("open");
  detail.hidden = false;
  if (detail.dataset.loaded) return;
  detail.innerHTML = '<span class="spinner"></span><span style="color:var(--muted)">正在加载完整简介…</span>';
  try {
    const url = `/api/detail?source=${cardEl.dataset.source}&id=${encodeURIComponent(cardEl.dataset.id)}`;
    const d = await (await fetch(url)).json();
    if (d.error) throw new Error(d.error);
    detail.dataset.loaded = "1";
    fillCover(cardEl, d);
    detail.innerHTML = detailHTML(d);
    const key = cardEl.dataset.source + ":" + cardEl.dataset.id;
    details[key] = d;
    if (items[key] && d.intro) items[key].intro = d.intro;   // 分享时用完整简介
    const srcBtn = detail.querySelector(".src-btn");
    if (srcBtn) srcBtn.addEventListener("click", () => doOpen(srcBtn, d.url || ""));
  } catch (err) {
    detail.innerHTML = `<div class="err">加载失败：${esc(String(err.message || err))}</div>`;
  }
}

// 晋江搜索结果不带封面，详情加载完成后回填
function fillCover(cardEl, d) {
  const current = cardEl.querySelector(".cover");
  if (!d.cover || !current || current.tagName === "IMG") return;
  const img = document.createElement("img");
  img.className = "cover";
  img.src = d.cover;
  img.referrerPolicy = "no-referrer";
  img.loading = "lazy";
  img.onerror = () => img.replaceWith(current);
  current.replaceWith(img);
}

function detailHTML(d) {
  const stats = Object.entries(d.stats || {}).filter(([, v]) => v !== "" && v !== null)
    .map(([k, v]) => `<span><i>${esc(k)}：</i>${esc(String(v))}</span>`).join("");
  const roles = Object.entries(d.roles || {}).filter(([, v]) => v)
    .map(([k, v]) => `<span><i>${esc(k)}：</i>${esc(v)}</span>`).join("");
  const tags = (d.tags || []).length
    ? `<div class="tags">${d.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : "";
  return `
    <div class="intro">${esc(d.intro || "（该作品暂无简介）")}</div>
    ${tags}
    <div class="kv">${stats}${roles}</div>
    <button class="src-btn" type="button">前往 ${esc(d.source_name)} 阅读原文 →</button>`;
}

// ---- 复制简介 / 用 Edge 打开原站 ----
function keyOf(el) {
  const c = el.closest(".card");
  return c ? c.dataset.source + ":" + c.dataset.id : "";
}
function urlOf(el) {
  const k = keyOf(el);
  return (details[k] && details[k].url) || (items[k] || {}).url || "";
}
function shareText(el) {
  const k = keyOf(el);
  const it = items[k] || {};
  const d = details[k] || {};
  const intro = (d.intro || it.intro || "（暂无简介）").trim();
  return `《${it.title || ""}》 作者：${it.author || "佚名"} 【${it.source_name || ""}】\n`
    + `简介：${intro}\n链接：${d.url || it.url || ""}`;
}
function flash(btn, text) {
  if (!btn) return;
  if (!btn.dataset.raw) btn.dataset.raw = btn.textContent;
  btn.textContent = text;
  btn.classList.add("done");
  btn.disabled = true;
  setTimeout(() => {
    btn.textContent = btn.dataset.raw;
    btn.classList.remove("done");
    btn.disabled = false;
  }, 1600);
}
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) { /* 走下面的兜底方案 */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch (e) {
    return false;
  }
}
async function doCopy(btn) {
  const ok = await copyText(shareText(btn));
  flash(btn, ok ? "已复制，可直接粘贴" : "复制失败，请手动选择");
}
async function doOpen(btn, url) {
  if (!url) { flash(btn, "没有可用链接"); return; }
  btn.disabled = true;
  try {
    const r = await (await fetch(`/api/open?url=${encodeURIComponent(url)}`)).json();
    if (r && r.ok) { flash(btn, "已用 Edge 打开"); return; }
    await copyText(url);
    flash(btn, "打开失败，链接已复制");
  } catch (err) {
    await copyText(url);
    flash(btn, "打开失败，链接已复制");
  }
}

// 心跳：页面还开着就持续告诉本地程序，关掉页面后程序会自动退出
setInterval(() => { fetch("/api/ping", { cache: "no-store" }).catch(() => {}); }, 5000);

$("quit").addEventListener("click", () => {
  $("foot").textContent = "程序已退出，可以关闭本页面了。";
  $("quit").disabled = true;
  fetch("/api/quit", { cache: "no-store" }).catch(() => {});
});
</script>
</body>
</html>
"""


# 只允许用浏览器打开这两个站点的页面（避免被当成任意 URL 跳板）
_SITE_HOSTS = ("jjwxc.net", "jjwxc.com", "gongzicp.com")


def _site_allowed(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in _SITE_HOSTS)


def _open_site(url: str) -> dict:
    """用后端浏览器（默认 Edge）打开原站页面，绕开本机没有默认浏览器的问题。"""
    if not _site_allowed(url):
        return {"ok": False, "error": "只允许打开晋江文学城 / 长佩文学的链接"}
    if _open_with_browser(url, _BROWSER):
        return {"ok": True, "url": url, "browser": _BROWSER}
    try:
        opened = webbrowser.open(url)
    except Exception:                                             # noqa: BLE001
        opened = False
    if opened:
        return {"ok": True, "url": url, "browser": "default"}
    return {"ok": False, "error": "没能打开浏览器"}


class Handler(BaseHTTPRequestHandler):
    server_version = "NovelFinder/1.0"

    def log_message(self, fmt, *args):        # 保持控制台整洁
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text, status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                             # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        _touch()

        if parsed.path in ("/", "/index.html"):
            return self._html(INDEX_HTML)
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if parsed.path == "/api/ping":
            self.send_response(204)
            self.end_headers()
            return
        if parsed.path == "/api/quit":
            self._json({"ok": True})
            _request_quit(self.server)
            return
        if parsed.path == "/api/search":
            q = (query.get("q") or [""])[0]
            source = (query.get("source") or ["all"])[0]
            if source not in ("all", "jjwxc", "changpei"):
                source = "all"
            mode = (query.get("mode") or ["title"])[0]
            if mode not in ("title", "author"):
                mode = "title"
            try:
                return self._json(ns.search_all(q, source, mode))
            except Exception as exc:                              # noqa: BLE001
                return self._json({"error": f"查询失败：{exc}"}, 500)
        if parsed.path == "/api/open":
            target = (query.get("url") or [""])[0]
            result = _open_site(target)
            return self._json(result, 200 if result.get("ok") else 400)
        if parsed.path == "/api/detail":
            source = (query.get("source") or [""])[0]
            novel_id = (query.get("id") or [""])[0]
            if source not in ns.SOURCES or not novel_id.isdigit():
                return self._json({"error": "参数不合法"}, 400)
            try:
                return self._json(ns.get_detail(source, novel_id))
            except Exception as exc:                              # noqa: BLE001
                return self._json({"error": f"加载失败：{exc}"}, 500)
        self._html("<h1>404</h1>", 404)


def free_port(start: int = 8765) -> int:
    for port in range(start, start + 40):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def main() -> None:
    parser = argparse.ArgumentParser(description="小说简介查询小程序（晋江 / 长佩）")
    parser.add_argument("--port", type=int, default=0, help="监听端口，默认自动选择")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--browser", default="edge", metavar="NAME",
                        help="用哪个浏览器打开页面：edge（默认）、default（系统默认），"
                             "或任意浏览器可执行文件路径 / 命令名")
    parser.add_argument("--mode", choices=("tray", "window", "background"), default=None,
                        help="运行方式：tray=系统托盘（打包成 exe 后默认）、"
                             "window=控制小窗口、background=纯后台")
    parser.add_argument("--window", dest="mode", action="store_const", const="window",
                        help="等同 --mode window")
    parser.add_argument("--no-window", dest="mode", action="store_const",
                        const="background", help="等同 --mode background")
    parser.add_argument("--no-tray", dest="mode", action="store_const",
                        const="background", help="等同 --mode background")
    args = parser.parse_args()

    global _BROWSER
    _BROWSER = args.browser or "edge"

    if args.mode is None:
        args.mode = "tray" if getattr(sys, "frozen", False) else "background"

    try:
        port = args.port or free_port()
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        _say(f"启动失败：{exc}")
        _alert("小说简介查询", f"启动失败：{exc}")
        return

    url = f"http://127.0.0.1:{port}/"
    _say("小说简介查询小程序已启动")
    _say(f"  地址：{url}")
    _say("  数据来源：晋江文学城 (jjwxc.net)、长佩文学 (gongzicp.com)")
    _say("  按 Ctrl+C 退出，或点页面上的「立即退出程序」/ 托盘、窗口里的「退出程序」\n")

    _touch()

    tray = None
    if args.mode == "tray":
        tray = _build_tray(url, server)
        if tray is None:
            _say("托盘模式不可用（缺少 pystray / Pillow），改为纯后台运行。")
            args.mode = "background"
    if args.mode == "background":
        # 托盘 / 控制窗口会自己决定何时退出，纯后台才靠心跳超时自动退出
        threading.Thread(target=_watchdog, args=(server,), daemon=True).start()

    _say("  运行方式：" + {"tray": "系统托盘", "window": "控制小窗口",
                          "background": "纯后台"}[args.mode])

    if args.no_browser:
        _say(f"请手动在浏览器打开：{url}")
    else:
        threading.Timer(0.5, lambda: _open_browser(url)).start()

    try:
        if args.mode == "background":
            server.serve_forever()
        else:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            if args.mode == "tray":
                tray.run()
            else:
                _run_window(url, port)
            server.shutdown()
    except KeyboardInterrupt:
        _say("\n已退出。")
    finally:
        server.server_close()


def _watchdog(server: ThreadingHTTPServer) -> None:
    """页面关掉之后（心跳停止）自动退出，避免后台残留进程。"""
    while True:
        time.sleep(5)
        if _idle_seconds() > IDLE_LIMIT:
            _say("长时间没有页面访问，自动退出。")
            server.shutdown()
            return


def _edge_path() -> str:
    """本机 Microsoft Edge 的可执行文件路径（找不到返回空串）。"""
    candidates = [
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Microsoft\Edge\Application\msedge.exe"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    # 兜底：注册表里登记的 Edge 路径，其次是 PATH 里的 msedge
    try:
        import winreg

        subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, subkey) as handle:
                    value = winreg.QueryValue(handle, None)
            except OSError:
                continue
            if value and os.path.exists(value):
                return value
    except ImportError:
        pass
    return shutil.which("msedge") or ""


_BROWSER = "edge"          # 由 --browser 覆盖


def _open_with_browser(url: str, browser: str) -> bool:
    """用指定浏览器打开：edge（默认）/ default（系统默认）/ 可执行文件路径或命令名。"""
    choice = (browser or "edge").strip()
    if choice.lower() == "default":
        return False
    if choice.lower() == "edge":
        exe = _edge_path()
    else:
        exe = choice if os.path.isfile(choice) else (shutil.which(choice) or "")
    if not exe:
        return False
    try:
        subprocess.Popen([exe, url])                              # noqa: S603
        return True
    except OSError:
        return False


def _open_browser(url: str) -> None:
    # 优先交给 Microsoft Edge：有些机器没设置默认浏览器，走系统默认会静默失败
    if _open_with_browser(url, _BROWSER):
        return
    try:
        opened = webbrowser.open(url)
    except Exception:                                             # noqa: BLE001
        opened = False
    if not opened:
        _say(f"请手动在浏览器打开：{url}")
        _alert("小说简介查询", f"没能自动打开浏览器，请手动访问：\n\n{url}")


def _copy_text(text: str) -> bool:
    """把文本写进 Windows 剪贴板（只用 ctypes，不依赖额外库）。"""
    try:
        import ctypes

        cf_unicodetext, gmem_moveable = 13, 0x0002
        kernel32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
        # 句柄在 64 位下是 8 字节指针，必须显式声明，否则会被截断成 32 位而崩溃
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalAlloc.argtypes = (ctypes.c_uint, ctypes.c_size_t)
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = (ctypes.c_void_p,)
        kernel32.GlobalUnlock.argtypes = (ctypes.c_void_p,)
        kernel32.GlobalFree.argtypes = (ctypes.c_void_p,)
        user32.OpenClipboard.argtypes = (ctypes.c_void_p,)
        user32.SetClipboardData.restype = ctypes.c_void_p
        user32.SetClipboardData.argtypes = (ctypes.c_uint, ctypes.c_void_p)

        payload = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(gmem_moveable, len(payload))
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(pointer, payload, len(payload))
        kernel32.GlobalUnlock(handle)
        if not user32.OpenClipboard(None):
            kernel32.GlobalFree(handle)
            return False
        try:
            user32.EmptyClipboard()
            user32.SetClipboardData(cf_unicodetext, handle)
        finally:
            user32.CloseClipboard()
        return True
    except Exception:                                             # noqa: BLE001
        return False


def _run_window(url: str, port: int) -> None:
    """控制小窗口：显示地址、打开页面、退出程序。tkinter 不可用时直接返回。"""
    try:
        import tkinter as tk
    except Exception:                                             # noqa: BLE001
        _say("缺少 tkinter，改为后台运行。")
        return

    bg, panel, line = "#0f1418", "#171e24", "#2b3740"
    text, muted, accent = "#e8eef3", "#93a4b1", "#2ec4c4"
    font = ("Microsoft YaHei UI", 9)

    root = tk.Tk()
    root.title("小说简介查询")
    root.configure(bg=bg)
    root.resizable(False, False)
    try:
        root.iconbitmap(_resource_path("icon.ico"))
    except Exception:                                             # noqa: BLE001
        pass

    wrap = tk.Frame(root, bg=bg, padx=20, pady=16)
    wrap.pack(fill="both", expand=True)

    tk.Label(wrap, text="小说简介查询", bg=bg, fg=text,
             font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
    tk.Label(wrap, text="数据来源：晋江文学城 · 长佩文学", bg=bg, fg=muted,
             font=font).pack(anchor="w", pady=(2, 12))

    row = tk.Frame(wrap, bg=bg)
    row.pack(fill="x")
    entry = tk.Entry(row, bg=panel, fg=text, readonlybackground=panel, relief="flat",
                     font=("Consolas", 10), width=28, highlightthickness=0)
    entry.insert(0, url)
    entry.configure(state="readonly")
    entry.pack(side="left", ipady=6, padx=(0, 6))

    def button(parent, label, command, fg=muted):
        return tk.Button(parent, text=label, command=command, bg=panel, fg=fg,
                         activebackground=line, activeforeground=text, relief="flat",
                         bd=0, padx=14, pady=6, cursor="hand2", font=font,
                         highlightthickness=0)

    button(row, "打开页面", lambda: _open_browser(url), accent).pack(side="left")

    status = tk.Label(wrap, text=f"● 运行中（端口 {port}）", bg=bg, fg="#6ee7b7",
                      font=font)
    status.pack(anchor="w", pady=(12, 0))

    bottom = tk.Frame(wrap, bg=bg)
    bottom.pack(fill="x", pady=(12, 0))

    def copy_url():
        try:
            root.clipboard_clear()
            root.clipboard_append(url)
            status.configure(text="地址已复制到剪贴板", fg=accent)
        except Exception:                                         # noqa: BLE001
            status.configure(text="复制失败，请手动选中地址", fg="#e6b455")

    button(bottom, "复制地址", copy_url).pack(side="left")

    def quit_app():
        status.configure(text="正在退出…", fg=muted)
        root.after(60, root.destroy)

    button(bottom, "退出程序", quit_app, "#ff9f9f").pack(side="left", padx=(8, 0))
    tk.Label(wrap, text="关闭本窗口即退出程序；页面里的「立即退出程序」同样有效。",
             bg=bg, fg="#6b7c88", font=("Microsoft YaHei UI", 8)).pack(anchor="w",
                                                                      pady=(12, 0))

    # 页面上的「立即退出程序」也要能关掉本窗口（Tk 必须由主线程操作，这里轮询标志位）
    quit_event = threading.Event()
    _register_quit_hook(quit_event.set)

    def poll_quit():
        if quit_event.is_set():
            status.configure(text="正在退出…", fg=muted)
            root.after(60, root.destroy)
        else:
            root.after(200, poll_quit)

    root.after(200, poll_quit)

    root.update_idletasks()
    win_w, win_h = root.winfo_width(), root.winfo_height()
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{(screen_w - win_w) // 2}+{max(0, (screen_h - win_h) // 3)}")

    root.protocol("WM_DELETE_WINDOW", quit_app)
    root.mainloop()


def _build_tray(url: str, server: ThreadingHTTPServer):
    """准备系统托盘图标；缺少 pystray / Pillow 时返回 None（调用方降级为后台运行）。"""
    try:
        import pystray
        from PIL import Image
    except Exception:                                             # noqa: BLE001
        return None

    try:
        image = Image.open(_resource_path("icon.ico"))
    except Exception:                                             # noqa: BLE001
        image = Image.new("RGBA", (64, 64), (46, 196, 196, 255))

    def on_open(icon=None, item=None):
        _open_browser(url)

    def on_copy(icon=None, item=None):
        ok = _copy_text(url)
        try:
            icon.notify("查询地址已复制到剪贴板" if ok else "复制失败，请手动选中地址",
                        "小说简介查询")
        except Exception:                                         # noqa: BLE001
            pass

    def on_quit(icon=None, item=None):
        icon.stop()
        server.shutdown()

    menu = pystray.Menu(
        pystray.MenuItem("打开查询页面", on_open, default=True),
        pystray.MenuItem("复制查询地址", on_copy),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(url, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出程序", on_quit),
    )
    icon = pystray.Icon("novel_finder", image, "小说简介查询", menu)
    _register_quit_hook(icon.stop)      # 页面上的「立即退出程序」也能收掉托盘
    return icon


if __name__ == "__main__":
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                             # noqa: BLE001
        pass
    main()
