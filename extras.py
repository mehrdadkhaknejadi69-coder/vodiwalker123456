# extras.py — v20
#  1) اسکن پراکسی از ریپوی گیت‌هاب (کشف خودکار فایل‌ها + تشخیص خودکار SOCKS5/SOCKS4/HTTP)
#  2) دانلود اپلیکیشن APK (از گیت‌هاب Release، لینک دلخواه، یا فایل محلی)
import asyncio
import os
import random
import re
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from main import DATA_DIR, logger, safe_int
import outbound_proxy as op

router = APIRouter()

DEFAULT_REPO = os.environ.get("GITHUB_PROXY_REPO", "https://github.com/Vodiwalker/vodi_shiper").strip()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()          # اختیاری (افزایش rate-limit)
APK_URL_ENV = os.environ.get("APP_APK_URL", "").strip()            # لینک مستقیم APK (اختیاری)
APK_REPO = os.environ.get("APP_APK_REPO", DEFAULT_REPO).strip()    # ریپویی که Release آن APK دارد
APK_LOCAL = Path(os.environ.get("APP_APK_PATH", str(DATA_DIR / "vodiwalker.apk")))

_REPO_RE = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/.*)?$")
_LIST_EXT = (".txt", ".list", ".csv", ".lst", ".proxy", ".proxies", ".json", ".md")
_FALLBACK_NAMES = ["proxies.txt", "proxy.txt", "proxy_list.txt", "socks5.txt", "socks4.txt", "http.txt",
                   "https.txt", "all.txt", "list.txt", "prxi.txt", "proxies/socks5.txt", "proxies/http.txt"]
_ADDR_RE = re.compile(r"(?:(socks5h?|socks4a?|https?)://)?(?:[^\s:@/\"']+:[^\s:@/\"']+@)?"
                      r"((?:\d{1,3}\.){3}\d{1,3}):(\d{2,5})")
MAX_FILES = 40
MAX_FILE_BYTES = 3_000_000
_CACHE: dict = {}      # repo -> (ts, text_by_file)
CACHE_TTL = 120


def _parse_repo(url: str):
    m = _REPO_RE.match((url or "").strip())
    if not m:
        raise HTTPException(status_code=400, detail="آدرس ریپو باید مثل https://github.com/user/repo باشد")
    return m.group(1), m.group(2)


def _hdrs(api: bool = False) -> dict:
    h = {"User-Agent": "VodiWalker-Scanner"}
    if api:
        h["Accept"] = "application/vnd.github+json"
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


def _hint_scheme(name: str) -> str:
    n = name.lower()
    if "socks5" in n or "sock5" in n:
        return "socks5"
    if "socks4" in n:
        return "socks4"
    if "http" in n:
        return "http"
    return "auto"


async def _get_text(client: httpx.AsyncClient, url: str) -> str:
    async with client.stream("GET", url, headers=_hdrs()) as r:
        if r.status_code >= 400:
            raise RuntimeError(f"{r.status_code}")
        buf, n = [], 0
        async for ch in r.aiter_bytes():
            buf.append(ch)
            n += len(ch)
            if n >= MAX_FILE_BYTES:
                break
    return b"".join(buf)[:MAX_FILE_BYTES].decode("utf-8", errors="ignore")


async def _walk(client, owner, repo, path="", depth=0, out=None):
    """لیست فایل‌های متنی ریپو با GitHub API (تا عمق ۲)."""
    out = out if out is not None else []
    r = await client.get(f"https://api.github.com/repos/{owner}/{repo}/contents/{path}", headers=_hdrs(True))
    if r.status_code == 403:
        raise RuntimeError("rate")
    if r.status_code >= 400:
        raise RuntimeError(f"api {r.status_code}")
    items = r.json()
    if isinstance(items, dict):
        items = [items]
    for it in items:
        if len(out) >= MAX_FILES:
            break
        if it.get("type") == "file" and it["name"].lower().endswith(_LIST_EXT) and it.get("size", 0) <= MAX_FILE_BYTES:
            if it.get("download_url"):
                out.append((it["path"], it["download_url"]))
        elif it.get("type") == "dir" and depth < 2 and not it["name"].startswith("."):
            await _walk(client, owner, repo, it["path"], depth + 1, out)
    return out


async def fetch_repo_texts(repo_url: str) -> dict:
    owner, repo = _parse_repo(repo_url)
    key = f"{owner}/{repo}"
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    texts: dict = {}
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        files = []
        try:
            files = await _walk(client, owner, repo)
        except Exception as exc:
            logger.warning(f"github api listing failed ({exc}); trying common file names")
        if not files:   # fallback بدون API: نام‌های رایج روی main/master
            for br in ("main", "master"):
                for nm in _FALLBACK_NAMES:
                    files.append((nm, f"https://raw.githubusercontent.com/{owner}/{repo}/{br}/{nm}"))
        sem = asyncio.Semaphore(8)

        async def one(path, url):
            async with sem:
                try:
                    texts[path] = await _get_text(client, url)
                except Exception:
                    pass
        await asyncio.gather(*(one(p, u) for p, u in files))
    texts = {k: v for k, v in texts.items() if v.strip()}
    if not texts:
        raise HTTPException(status_code=400, detail="هیچ فایل لیست پراکسی‌ای در ریپو پیدا نشد (ریپو باید public باشد و فایل‌های txt/list/csv/json داشته باشد)")
    _CACHE[key] = (time.time(), texts)
    return texts


def candidates_from_texts(texts: dict, default_scheme: str, cap: int):
    """فایل‌ها → کاندیدها. پروتکل از اسم فایل (socks5.txt/http.txt) یا scheme:// داخل خط حدس زده می‌شود."""
    cands, seen = [], set()
    for path, txt in texts.items():
        fname = path.rsplit("/", 1)[-1]
        sc = _hint_scheme(fname)
        sc = default_scheme if sc == "auto" else sc
        if fname.lower().endswith((".json", ".md")):
            lines = [f"{(m.group(1) + '://') if m.group(1) else ''}{m.group(2)}:{m.group(3)}" for m in _ADDR_RE.finditer(txt)]
        else:
            lines = txt.replace("\r", "\n").replace(",", "\n").split("\n")
        for line in lines:
            c = op.parse_candidate(line, sc)
            if not c:
                continue
            k = (c["host"], c["port"])
            if k in seen:
                continue
            seen.add(k)
            cands.append(c)
    random.shuffle(cands)           # هر اسکن بخش متفاوتی از لیست‌های بزرگ را می‌گیرد
    truncated = len(cands) > cap
    return cands[:cap], truncated


@router.post("/api/proxies/scan/github")
async def api_scan_github(request: Request, _=Depends(op.require_owner)):
    """body: {repo?: url, protocol?: auto|socks5|socks4|http, samples, tls, speed, concurrency, timeout}"""
    body = await op._json_body(request)
    op._purge_jobs()
    if op._active_jobs() >= op.SCAN_MAX_ACTIVE_JOBS:
        raise HTTPException(status_code=429, detail="یک اسکن دیگر در حال اجراست؛ صبر کن یا لغوش کن")
    repo = str(body.get("repo") or DEFAULT_REPO).strip()
    proto = str(body.get("protocol") or "auto").strip().lower()
    proto = "auto" if proto == "auto" else op._norm_scheme(proto)
    texts = await fetch_repo_texts(repo)
    cap = max(50, min(20000, safe_int(body.get("max", op.SCAN_JOB_MAX_CANDIDATES), minimum=50, maximum=20000)))
    cands, truncated = candidates_from_texts(texts, proto, cap)
    ok, blocked = [], 0
    for c in cands:
        if await op._host_is_blocked(c["host"]):
            blocked += 1
        else:
            ok.append(c)
    if not ok:
        raise HTTPException(status_code=400, detail="هیچ پروکسی معتبری در فایل‌های ریپو پیدا نشد (فرمت host:port)")
    job = op._start_job("scan", ok, op._job_opts(body),
                        {"blocked": blocked, "truncated": truncated, "source": "github"})
    op.log_activity("network", f"اسکن گیت‌هاب شروع شد: {len(ok)} پروکسی از {len(texts)} فایل", "ok")
    return {"ok": True, "job_id": job["id"], "total": job["total"], "blocked": blocked,
            "truncated": truncated, "files": len(texts), "repo": repo}


# ═════════════════════════ لینک دانلود اپلیکیشن ═════════════════════════
# دکمه‌ی «دانلود اپلیکیشن» یک صفحه‌ی دلخواه را (مثلاً Google Play یا صفحه‌ی دانلود) در تب جدید باز می‌کند.
# لینک را مالک پنل یک‌بار از خود دکمه (آیکون مداد) تنظیم می‌کند؛ یا با env: APP_DOWNLOAD_URL
import json

LINK_FILE = DATA_DIR / "app_link.json"
APP_LINK_ENV = os.environ.get("APP_DOWNLOAD_URL", "").strip()


def _read_link() -> str:
    try:
        if LINK_FILE.exists():
            return str(json.loads(LINK_FILE.read_text(encoding="utf-8")).get("url") or "").strip()
    except Exception:
        pass
    return APP_LINK_ENV


@router.get("/api/app/link")
async def app_link_get(request: Request, _=Depends(op.require_auth)):
    info = await op.get_session_info(request.cookies.get(op.SESSION_COOKIE))
    return {"ok": True, "url": _read_link(), "owner": bool(info and info.get("admin_id") == "owner")}


@router.post("/api/app/link")
async def app_link_set(request: Request, _=Depends(op.require_owner)):
    body = await op._json_body(request)
    url = str(body.get("url") or "").strip()
    if url and not re.match(r"^https?://[^\s]+$", url, re.I):
        raise HTTPException(status_code=400, detail="لینک باید با http:// یا https:// شروع شود")
    LINK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LINK_FILE.write_text(json.dumps({"url": url}, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "url": url}
