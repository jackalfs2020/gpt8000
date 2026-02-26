import json
import os
import random
import re
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import edge_tts
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI()

# 路径配置（Zeabur 等平台可设置 DATA_DIR=/data 使用持久化目录）
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR)))
STATIC_DIR = BASE_DIR / "static"
AUDIO_CACHE_DIR = DATA_DIR / "audio_cache"
DB_PATH = os.environ.get("DATA_DB", str(DATA_DIR / "data.db"))

# 排行榜防刷：同 IP 或 device_id 10 分钟内仅可提交 1 次
RATE_LIMIT_SECONDS = 600
_rate_limit: dict[str, float] = {}


def _rate_limit_check(key: str) -> bool:
    now = datetime.now(timezone.utc).timestamp()
    last = _rate_limit.get(key, 0)
    if now - last < RATE_LIMIT_SECONDS:
        return False
    _rate_limit[key] = now
    return True


class LeaderboardIn(BaseModel):
    count: int = 0
    device_id: str | None = None

words_data = {}
words_keys = []


def _gptwords_path() -> Path:
    p = os.environ.get("GPTWORDS_PATH")
    if p:
        return Path(p)
    return BASE_DIR / "gptwords.json"


def load_data():
    global words_data, words_keys
    path = _gptwords_path()
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            content = f.read().strip()
            if not content:
                raise ValueError("文件内容为空")
            content_fixed = re.sub(r',\s*([\]}])', r'\1', content)
            try:
                data = json.loads(content_fixed, strict=False)
                if isinstance(data, dict):
                    for k, v in data.items():
                        words_data[str(k).lower()] = v if isinstance(v, dict) else {"解析": v}
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            w = item.get("word") or item.get("Word") or item.get("name") or item.get("headWord")
                            if not w:
                                keys = list(item.keys())
                                if keys:
                                    w, item = keys[0], item[keys[0]]
                            if w:
                                words_data[str(w).lower()] = item
            except Exception:
                print("⚠️ 整体解析失败，启用逐行容错模式")
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line in ("[", "]"):
                        continue
                    if line.endswith(","):
                        line = line[:-1]
                    try:
                        item = json.loads(line, strict=False)
                        if isinstance(item, dict):
                            w = item.get("word") or item.get("Word") or item.get("name") or item.get("headWord")
                            if not w:
                                keys = list(item.keys())
                                if keys:
                                    w, item = keys[0], item[keys[0]]
                            if w:
                                words_data[str(w).lower()] = item
                    except Exception:
                        pass
        words_keys = list(words_data.keys())
        if not words_keys:
            words_data["error"] = {"消息": "🚨 词库格式极度异常"}
            words_keys = ["error"]
        else:
            print(f"✅ 成功加载词库：共 {len(words_keys)} 个词汇！")
    except Exception as e:
        words_data["error"] = {"消息": f"🚨 致命错误: {e}"}
        words_keys = ["error"]


load_data()


def _init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rank (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            count INTEGER NOT NULL,
            day TEXT NOT NULL DEFAULT '',
            month TEXT NOT NULL DEFAULT '',
            device_id TEXT NOT NULL DEFAULT '',
            ip TEXT NOT NULL DEFAULT '',
            user_agent TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()


_init_db()


def _today_str() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")


def _db_get_all() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT count, day, month, device_id, ip, user_agent FROM rank").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _db_insert(count: int, day: str, month: str, device_id: str, ip: str, user_agent: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO rank (count, day, month, device_id, ip, user_agent) VALUES (?, ?, ?, ?, ?, ?)",
        (count, day, month, device_id, ip, user_agent),
    )
    conn.commit()
    conn.close()


def _device_key(x: dict) -> str:
    did = (x.get("device_id") or "").strip()
    if did:
        return f"d:{did}"
    return f"i:{x.get('ip','')}|{x.get('user_agent','')}"


def _dedupe_by_device(records: list[dict]) -> list[int]:
    by_key: dict[str, int] = {}
    for x in records:
        k = _device_key(x)
        c = x.get("count", 0)
        by_key[k] = max(by_key.get(k, 0), c)
    return sorted(by_key.values(), reverse=True)


def _build_leaderboard_response(lst: list[dict]) -> dict:
    cur_day, cur_month = _today_str()
    total_counts = _dedupe_by_device(lst)
    monthly_records = [x for x in lst if x.get("month") == cur_month]
    daily_records = [x for x in lst if x.get("day") == cur_day]
    return {
        "total": total_counts[:2],
        "monthly": _dedupe_by_device(monthly_records)[:1],
        "daily": _dedupe_by_device(daily_records)[:2],
    }


# StaticFiles 需在具体路由之后挂载，避免覆盖 /api
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/keys")
def api_keys():
    return [k for k in words_keys if k != "error"]


@app.get("/api/search")
def api_search(q: str = ""):
    q = q.lower().strip()
    if not q:
        return []
    res = []
    if q in words_data:
        res.append({"word": q})
    for k in words_keys:
        if k != q and k.startswith(q):
            res.append({"word": k})
            if len(res) >= 12:
                break
    return res


def _fetch_phonetic(w: str) -> str:
    try:
        req = urllib.request.Request(
            f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(w)}",
            headers={"User-Agent": "GPT8000/1.0"},
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode())
        for entry in data if isinstance(data, list) else []:
            for p in entry.get("phonetics", []) or []:
                if p.get("text"):
                    return p["text"]
        return ""
    except Exception:
        return ""


def _extract_phonetic_from_data(data: dict) -> str:
    for key in ("phonetic", "phonetics", "音标", "pronunciation", "usPhonetic", "ukPhonetic"):
        v = data.get(key)
        if v:
            return v if isinstance(v, str) else str(v)
    return ""


@app.get("/api/word/{word}")
def api_word(word: str):
    word = word.lower()
    data = words_data.get(word, {}) if word in words_data else {}
    data = dict(data) if isinstance(data, dict) else {}
    phonetic = _extract_phonetic_from_data(data)
    if not phonetic:
        phonetic = _fetch_phonetic(word)
    if phonetic and "phonetic" not in data:
        data["phonetic"] = phonetic
    return {"word": word, "data": data}


@app.get("/api/random")
def api_random():
    if not words_keys:
        return {"word": "Error", "data": {"消息": "词库加载异常"}}
    w = random.choice(words_keys)
    return {"word": w, "data": words_data[w]}


@app.get("/api/leaderboard")
def api_leaderboard():
    lst = _db_get_all()
    return _build_leaderboard_response(lst)


@app.post("/api/leaderboard")
def api_leaderboard_submit(body: LeaderboardIn, request: Request):
    if body.count < 0 or body.count > 100000:
        return {"ok": False, "msg": "无效数量"}
    did = (body.device_id or "").strip()[:64] if body.device_id else ""
    ip = ""
    if request.client:
        ip = (
            request.headers.get("x-forwarded-for", request.client.host or "")
            .split(",")[0]
            .strip()[:45]
        )
    ua = (request.headers.get("user-agent") or "")[:200]
    # 防刷：IP 或 device_id 限频
    if did and not _rate_limit_check(f"d:{did}"):
        return {"ok": False, "msg": "提交过于频繁，请稍后再试", **_build_leaderboard_response(_db_get_all())}
    if ip and not _rate_limit_check(f"i:{ip}"):
        return {"ok": False, "msg": "提交过于频繁，请稍后再试", **_build_leaderboard_response(_db_get_all())}
    day_str, month_str = _today_str()
    _db_insert(body.count, day_str, month_str, did, ip, ua)
    return {"ok": True, **_build_leaderboard_response(_db_get_all())}


def _safe_word_filename(w: str) -> str:
    return "".join(c if c.isalnum() or c in " -" else "_" for c in w.strip())[:50].replace(" ", "_")


@app.get("/api/audio/{word}")
async def api_audio(word: str):
    w = "".join(c for c in word.strip() if c.isalpha() or c.isspace())[:50].strip()
    if not w:
        return Response(status_code=400)
    AUDIO_CACHE_DIR.mkdir(exist_ok=True)
    cache_path = AUDIO_CACHE_DIR / f"{_safe_word_filename(w)}.mp3"
    if cache_path.exists():
        return FileResponse(cache_path, media_type="audio/mpeg")
    try:
        communicate = edge_tts.Communicate(w, "en-US-GuyNeural")
        chunks = []
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                chunks.append(chunk.get("data", b""))
        body = b"".join(chunks)
        if not body:
            return Response(status_code=500)
        with open(cache_path, "wb") as f:
            f.write(body)
        return Response(content=body, media_type="audio/mpeg")
    except Exception as e:
        return Response(status_code=500, content=str(e))
