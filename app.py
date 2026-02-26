import json
import os
import random
import urllib.request
import urllib.parse
from datetime import datetime, timezone
import re
import edge_tts
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

app = FastAPI()

LEADERBOARD_FILE = os.environ.get("LEADERBOARD_FILE", "leaderboard.json")


class LeaderboardIn(BaseModel):
    count: int = 0
    device_id: str | None = None  # 设备唯一标识，仅后台记录，不返回前端

words_data = {}
words_keys = []

def load_data():
    global words_data, words_keys
    try:
        with open("gptwords.json", "r", encoding="utf-8-sig") as f:
            content = f.read().strip()
            if not content: raise ValueError("文件内容为空")
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
                                if keys: w, item = keys[0], item[keys[0]]
                            if w: words_data[str(w).lower()] = item
            except Exception as e:
                print(f"⚠️ 整体解析失败，启用逐行容错模式")
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line in ("[", "]"): continue
                    if line.endswith(","): line = line[:-1]
                    try:
                        item = json.loads(line, strict=False)
                        if isinstance(item, dict):
                            w = item.get("word") or item.get("Word") or item.get("name") or item.get("headWord")
                            if not w:
                                keys = list(item.keys())
                                if keys: w, item = keys[0], item[keys[0]]
                            if w: words_data[str(w).lower()] = item
                    except Exception: pass
                        
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

# 新增：返回所有合法单词 key 给前端，用于智能过滤熟词
@app.get("/api/keys")
def api_keys():
    return [k for k in words_keys if k != "error"]

@app.get("/api/search")
def api_search(q: str = ""):
    q = q.lower().strip()
    if not q: return []
    res = []
    if q in words_data: res.append({"word": q})
    for k in words_keys:
        if k != q and k.startswith(q):
            res.append({"word": k})
            if len(res) >= 12: break
    return res

def _fetch_phonetic(w: str) -> str:
    """从 Free Dictionary API 获取 IPA 音标"""
    try:
        req = urllib.request.Request(
            f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(w)}",
            headers={"User-Agent": "GPT8000/1.0"}
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
    """从词条数据中提取音标"""
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
    # 音标：优先用数据内字段，否则请求 Free Dictionary API
    phonetic = _extract_phonetic_from_data(data)
    if not phonetic:
        phonetic = _fetch_phonetic(word)
    if phonetic and "phonetic" not in data:
        data["phonetic"] = phonetic
    return {"word": word, "data": data}

@app.get("/api/random")
def api_random():
    if not words_keys: return {"word": "Error", "data": {"消息": "词库加载异常"}}
    w = random.choice(words_keys)
    return {"word": w, "data": words_data[w]}


# 排行榜：持久化到文件，用日期字符串 day/month 做周期筛选（不依赖时间戳）
# 存储格式: [{count, day?, month?, device_id?, ip?, user_agent?}, ...]
def _today_str() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")


def _load_leaderboard() -> list[dict]:
    try:
        with open(LEADERBOARD_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list):
                return []
            out = []
            for x in data:
                if isinstance(x, dict) and "count" in x:
                    day = x.get("day") or ""
                    month = x.get("month") or ""
                    if not day and x.get("ts"):
                        try:
                            dt = datetime.fromtimestamp(x["ts"] / 1000, tz=timezone.utc)
                            day, month = dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m")
                        except Exception:
                            pass
                    out.append({
                        **x,
                        "day": day,
                        "month": month,
                        "device_id": x.get("device_id") or "",
                        "ip": x.get("ip") or "",
                        "user_agent": x.get("user_agent") or "",
                    })
                elif isinstance(x, (int, float)):
                    out.append({"count": int(x), "day": "", "month": "", "device_id": "", "ip": "", "user_agent": ""})
            return out
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_leaderboard(lst: list[dict]) -> None:
    try:
        with open(LEADERBOARD_FILE, "w", encoding="utf-8") as f:
            json.dump(lst, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ 排行榜保存失败: {e}")


def _device_key(x: dict) -> str:
    """设备主键：device_id 优先，否则 ip|user_agent"""
    did = (x.get("device_id") or "").strip()
    if did:
        return f"d:{did}"
    return f"i:{x.get('ip','')}|{x.get('user_agent','')}"


def _dedupe_by_device(records: list[dict]) -> list[int]:
    """按设备去重，每设备只保留该周期内最高分，再取排名"""
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
        "total": total_counts[:2],       # 第1、2名，每设备仅占一名
        "monthly": _dedupe_by_device(monthly_records)[:1],
        "daily": _dedupe_by_device(daily_records)[:2],
    }


_leaderboard = _load_leaderboard()


@app.get("/api/leaderboard")
def api_leaderboard():
    return _build_leaderboard_response(_leaderboard)


@app.post("/api/leaderboard")
def api_leaderboard_submit(body: LeaderboardIn, request: Request):
    """提交已接触数量，后台记录 device_id + ip + user_agent 辅助，不返回前端"""
    if 0 <= body.count <= 100000:
        did = (body.device_id or "").strip()[:64] if body.device_id else ""
        ip = request.headers.get("x-forwarded-for", request.client.host or "").split(",")[0].strip()[:45] if request.client else ""
        ua = (request.headers.get("user-agent") or "")[:200]
        day_str, month_str = _today_str()
        _leaderboard.append({
            "count": body.count,
            "day": day_str,
            "month": month_str,
            "device_id": did,
            "ip": ip,
            "user_agent": ua,
        })
        _leaderboard.sort(key=lambda x: x["count"], reverse=True)
        del _leaderboard[100:]
        _save_leaderboard(_leaderboard)
    return {"ok": True, **_build_leaderboard_response(_leaderboard)}


@app.get("/api/audio/{word}")
async def api_audio(word: str):
    """后端 TTS 发音，微信等不支持 Web Speech API 的浏览器可用此接口播放"""
    w = "".join(c for c in word.strip() if c.isalpha() or c.isspace())[:50].strip()
    if not w:
        return Response(status_code=400)
    try:
        communicate = edge_tts.Communicate(w, "en-US-GuyNeural")
        chunks = []
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                chunks.append(chunk.get("data", b""))
        body = b"".join(chunks)
        if not body:
            return Response(status_code=500)
        return Response(content=body, media_type="audio/mpeg")
    except Exception as e:
        return Response(status_code=500, content=str(e))


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_CONTENT

HTML_CONTENT = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GPT4 智能单词本 | 艾宾浩斯考场版</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/vue@3/dist/vue.global.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        [v-cloak] { display: none; }
        .markdown-body p { margin-bottom: 0.15rem; line-height: 1.5; }
        .markdown-body strong { color: #4f46e5; }
        .shake { animation: shake 0.5s cubic-bezier(.36,.07,.19,.97) both; }
        @keyframes shake {
            10%, 90% { transform: translate3d(-1px, 0, 0); }
            20%, 80% { transform: translate3d(2px, 0, 0); }
            30%, 50%, 70% { transform: translate3d(-4px, 0, 0); }
            40%, 60% { transform: translate3d(4px, 0, 0); }
        }
    </style>
</head>
<body class="bg-gray-50 text-gray-800 font-sans min-h-screen">
    <div id="app" v-cloak class="max-w-3xl mx-auto p-4 sm:p-6 lg:p-8 relative pb-20">
        <header class="text-center mb-8 mt-4 relative">
            <h1 class="text-4xl font-black text-indigo-600 mb-2 tracking-tight">GPT4 智能单词本</h1>
            <p class="text-gray-500 font-medium">内置 30 天熟词休眠与动态考核引擎</p>
            <div class="mt-4 flex justify-center gap-3 text-sm font-bold flex-wrap">
                <button @click="openModal('encountered')" class="bg-gray-200 hover:bg-gray-300 px-3 py-1 rounded-full text-gray-600 cursor-pointer transition-colors">
                    🧠 已接触: {{ encounteredWords.length }} 词
                </button>
                <button @click="openModal('mastered')" class="bg-green-100 hover:bg-green-200 px-3 py-1 rounded-full text-green-700 cursor-pointer transition-colors">
                    🏆 熟词本: {{ masteredCount }} 词
                </button>
            </div>
            <div class="mt-4 bg-white rounded-xl shadow border border-gray-100 p-4 max-w-xs mx-auto">
                <h3 class="text-xs font-black text-gray-500 uppercase tracking-wider mb-3 text-center">📊 排行榜（已接触数）</h3>
                <div class="space-y-3 text-sm">
                    <div class="border-b border-gray-100 pb-2">
                        <p class="text-gray-400 text-xs mb-1">周期：全部</p>
                        <div class="flex justify-between py-0.5"><span class="text-gray-500">1</span><span class="font-bold" :class="leaderboard.total?.[0] ? 'text-indigo-600' : 'text-gray-300'">{{ leaderboard.total?.[0] ? leaderboard.total[0] + ' 词' : '—' }}</span></div>
                        <div class="flex justify-between py-0.5"><span class="text-gray-500">2</span><span class="font-bold" :class="leaderboard.total?.[1] ? 'text-indigo-600' : 'text-gray-300'">{{ leaderboard.total?.[1] ? leaderboard.total[1] + ' 词' : '—' }}</span></div>
                    </div>
                    <div class="border-b border-gray-100 pb-2">
                        <p class="text-gray-400 text-xs mb-1">周期：本月</p>
                        <div class="flex justify-between py-0.5"><span class="text-gray-500">1</span><span class="font-bold" :class="leaderboard.monthly?.[0] ? 'text-indigo-600' : 'text-gray-300'">{{ leaderboard.monthly?.[0] ? leaderboard.monthly[0] + ' 词' : '—' }}</span></div>
                    </div>
                    <div>
                        <p class="text-gray-400 text-xs mb-1">周期：今日</p>
                        <div class="flex justify-between py-0.5"><span class="text-gray-500">1</span><span class="font-bold" :class="leaderboard.daily?.[0] ? 'text-indigo-600' : 'text-gray-300'">{{ leaderboard.daily?.[0] ? leaderboard.daily[0] + ' 词' : '—' }}</span></div>
                        <div class="flex justify-between py-0.5"><span class="text-gray-500">2</span><span class="font-bold" :class="leaderboard.daily?.[1] ? 'text-indigo-600' : 'text-gray-300'">{{ leaderboard.daily?.[1] ? leaderboard.daily[1] + ' 词' : '—' }}</span></div>
                    </div>
                </div>
            </div>
        </header>

        <div v-if="modalType" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50" @click.self="modalType = null">
            <div class="bg-white rounded-2xl shadow-2xl max-w-md w-full max-h-[70vh] overflow-hidden flex flex-col">
                <div class="px-6 py-4 border-b flex justify-between items-center">
                    <span class="font-bold text-lg">{{ modalType === 'encountered' ? '🧠 已接触' : '🏆 熟词本' }}</span>
                    <button @click="modalType = null" class="text-gray-400 hover:text-gray-600 text-2xl leading-none">&times;</button>
                </div>
                <ul class="flex-1 overflow-y-auto p-4 space-y-1">
                    <li v-for="w in modalWords" :key="w" @click="closeModalAndSelect(w)" class="px-4 py-3 rounded-xl hover:bg-indigo-50 cursor-pointer font-medium text-gray-700">
                        {{ w }}
                    </li>
                    <li v-if="modalWords.length === 0" class="text-gray-400 text-center py-8">暂无</li>
                </ul>
            </div>
        </div>

        <main>
            <div class="relative mb-6 z-20" v-show="!examState.isExam">
                <input v-model="searchQuery" @input="handleSearch" type="text" 
                       class="w-full px-5 py-4 rounded-2xl border border-gray-200 shadow-sm focus:border-indigo-500 focus:ring-4 focus:ring-indigo-100 focus:outline-none text-lg transition-all"
                       placeholder="查词 (搜索不计入抽卡次数)..." autocomplete="off">
                <button @click="fetchRandom" class="absolute right-3 top-3 bg-indigo-50 text-indigo-600 font-bold px-4 py-2 rounded-xl hover:bg-indigo-100 transition-colors shadow-sm flex items-center gap-1 active:scale-95">
                    🎲 盲盒抽词
                </button>
            </div>

            <div v-if="searchResults.length > 0 && searchQuery && !examState.isExam" class="bg-white rounded-2xl shadow-xl border border-gray-100 mb-6 overflow-hidden absolute w-[calc(100%-2rem)] sm:w-[calc(100%-3rem)] lg:w-[calc(100%-4rem)] max-w-3xl z-30">
                <ul class="divide-y divide-gray-50 max-h-80 overflow-y-auto">
                    <li v-for="item in searchResults" :key="item.word" @click="selectWord(item.word)" 
                        class="px-6 py-4 hover:bg-indigo-50 cursor-pointer transition-colors flex justify-between items-center group">
                        <span class="font-bold text-gray-700 group-hover:text-indigo-600 text-lg">{{ item.word }}</span>
                        <span class="text-gray-400 text-sm">查看解析 ➔</span>
                    </li>
                </ul>
            </div>

            <div v-if="examState.isExam" class="bg-white rounded-3xl shadow-2xl border-2 border-orange-300 overflow-hidden mt-6 text-center py-10 px-6 sm:px-12 relative z-10">
                <div class="inline-block bg-gradient-to-r from-orange-400 to-red-500 text-white px-6 py-2 rounded-full text-sm font-black tracking-widest mb-6 shadow-md">
                    {{ examTitle }}
                </div>
                
                <h3 class="text-xl sm:text-2xl text-gray-700 mb-8 leading-relaxed font-medium bg-orange-50/50 p-6 rounded-2xl border border-orange-100 shadow-inner text-left">
                    <span class="text-orange-500 font-bold text-sm block mb-2 uppercase tracking-widest">💡 根据释义拼写单词：</span>
                    <span v-html="renderMarkdown(examState.hint)"></span>
                </h3>
                
                <div class="max-w-sm mx-auto relative">
                    <input v-model="examInput" type="text" ref="examInputRef" @keyup.enter="submitExam"
                           class="w-full text-center px-4 py-4 rounded-xl border-2 shadow-sm focus:outline-none text-3xl font-black tracking-widest transition-colors mb-4 uppercase"
                           :class="examError ? 'border-red-400 bg-red-50 text-red-600 shake focus:border-red-500' : 'border-gray-200 focus:border-orange-400'"
                           placeholder="输入英文" autocomplete="off" spellcheck="false">
                    <p v-if="examError" class="text-red-500 mt-2 text-sm font-bold absolute w-full -bottom-6">❌ 拼写错误，再仔细想想！</p>
                </div>

                <div class="flex justify-center gap-4 mt-12">
                    <button @click="giveUp" class="px-6 py-3 rounded-xl font-bold text-gray-500 bg-gray-100 hover:bg-gray-200 transition-colors">
                        想不起来 (降级惩罚)
                    </button>
                    <button @click="submitExam" class="px-8 py-3 rounded-xl font-bold text-white bg-orange-500 hover:bg-orange-600 transition-colors shadow-md shadow-orange-200 active:scale-95 text-lg flex-1 max-w-[200px]">
                        提交答案
                    </button>
                </div>
            </div>

            <div v-else-if="currentWord && !searchQuery" class="bg-white rounded-3xl shadow-sm border border-gray-100 overflow-hidden mt-6 relative z-10">
                <div class="bg-gradient-to-r from-indigo-600 to-blue-500 px-8 py-8 sm:py-10 text-white relative">
                    <div class="relative z-10">
                        <h2 class="text-5xl font-black tracking-wide">{{ currentWord.word }}</h2>
                        <p v-if="phoneticText" class="text-white/90 text-lg mt-1 font-medium">{{ phoneticText }}</p>
                    </div>
                    <div class="absolute top-6 right-6 flex flex-col items-end space-y-2 z-10">
                        <span class="bg-white/20 backdrop-blur px-3 py-1 rounded-full text-xs font-bold shadow-sm border border-white/30">
                             🎯 抽中: {{ stats[currentWord.word]?.count || 0 }} 次
                        </span>
                        <span v-if="stats[currentWord.word]?.nextReview" class="bg-green-400 text-green-900 px-3 py-1 rounded-full text-xs font-bold shadow-sm shadow-green-500/30">
                             🌟 熟词 (休眠30天)
                        </span>
                    </div>
                </div>
                <div class="p-8 space-y-4">
                    <div v-for="(value, key) in currentWord.data" :key="key">
                        <template v-if="!['word', 'headword', 'phonetic'].includes(key.toLowerCase()) && value">
                            <div class="flex items-center justify-between gap-3 mb-2">
                                <h3 class="text-sm text-indigo-500 font-extrabold uppercase tracking-widest flex items-center">
                                    {{ formatKey(key) }}
                                </h3>
                                <button v-if="key.toLowerCase() === 'content'" @click="speakWord(currentWord.word)" type="button" class="flex-shrink-0 px-3 py-1.5 rounded-lg bg-indigo-100 hover:bg-indigo-200 text-indigo-700 text-xs font-bold transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-300" title="语迟福到">语迟福到</button>
                            </div>
                            <div v-if="typeof value === 'object'" class="space-y-1 bg-indigo-50/30 p-4 rounded-2xl markdown-body">
                                <p v-for="(v, k) in value" :key="k" class="text-gray-700">
                                    <span v-if="isNaN(k)" class="font-bold text-gray-900 mr-1">{{ k }}: </span>
                                    <span v-html="renderMarkdown(String(v))"></span>
                                </p>
                            </div>
                            <div v-else class="text-gray-700 bg-indigo-50/30 p-4 rounded-2xl markdown-body" v-html="renderMarkdown(String(value))"></div>
                        </template>
                    </div>
                </div>
            </div>

            <div v-else-if="!searchQuery && !isLoading" class="text-center text-gray-400 mt-24">
                <div class="text-6xl mb-4 opacity-50">✨</div>
                <p class="text-lg text-gray-500 font-bold mb-2">点击右上角「盲盒抽词」，开启艾宾浩斯记忆之旅</p>
                <p class="text-sm opacity-70">当单词在第4次、第6次及满30天出现时，将自动拦截进行强制默写</p>
            </div>
            
            <div v-if="isLoading" class="text-center text-indigo-400 mt-20">
                <p class="text-lg animate-pulse font-bold">云端检索引擎运转中...</p>
            </div>
            
            <div v-if="searchResults.length > 0 && searchQuery" @click="searchQuery = ''" class="fixed inset-0 z-10"></div>
        </main>

        <div v-if="!examState.isExam" class="fixed bottom-0 left-0 right-0 z-40 p-4 pb-6 bg-gradient-to-t from-gray-50 to-transparent">
            <button @click="fetchRandom" class="w-full max-w-sm mx-auto flex items-center justify-center gap-2 py-4 px-6 rounded-2xl bg-indigo-500 hover:bg-indigo-600 text-white font-bold text-lg shadow-lg shadow-indigo-200 active:scale-[0.98] transition-all">
                🎲 盲盒抽词
            </button>
            <p class="text-center text-xs text-gray-400 mt-2">摇一摇手机也可抽词</p>
        </div>
    </div>

    <script>
        const { createApp, ref, computed, watch, onMounted, nextTick } = Vue;
        createApp({
            setup() {
                const searchQuery = ref('');
                const searchResults = ref([]);
                const currentWord = ref(null);
                const isLoading = ref(false);
                let timeoutId = null;
                
                // 本地学习大脑 (LocalStorage)
                const allKeys = ref([]);
                const stats = ref(JSON.parse(localStorage.getItem('gpt_dict_stats') || '{}'));
                
                const saveStats = () => {
                    localStorage.setItem('gpt_dict_stats', JSON.stringify(stats.value));
                };

                const masteredCount = computed(() => {
                    return Object.values(stats.value).filter(s => s.nextReview > 0).length;
                });

                const phoneticText = computed(() => {
                    const d = currentWord.value?.data;
                    if (!d || typeof d !== 'object') return '';
                    for (const k of ['phonetic', 'phonetics', '音标', 'pronunciation', 'usPhonetic', 'ukPhonetic']) {
                        const v = d[k];
                        if (v && typeof v === 'string') return v;
                    }
                    return '';
                });

                const encounteredWords = computed(() => Object.keys(stats.value).sort());
                const masteredWords = computed(() => Object.keys(stats.value).filter(w => stats.value[w]?.nextReview > 0).sort());
                const modalType = ref(null);
                const modalWords = computed(() => modalType.value === 'encountered' ? encounteredWords.value : masteredWords.value);
                const leaderboard = ref({ total: [], monthly: [], daily: [] });

                const openModal = (t) => { modalType.value = t; };
                const closeModalAndSelect = async (word) => {
                    modalType.value = null;
                    await selectWord(word);
                };

                const getWordStat = (word) => {
                    if (!stats.value[word]) stats.value[word] = { count: 0, nextReview: 0 };
                    return stats.value[word];
                };

                // 考试系统状态
                const examState = ref({ isExam: false, word: '', hint: '', type: '', data: null });
                const examInput = ref('');
                const examError = ref(false);
                const examInputRef = ref(null);

                const examTitle = computed(() => {
                    if (examState.value.type === 'draw4') return '⚠️ 阶段考核：这是你第 4 次遇到它';
                    if (examState.value.type === 'draw6') return '🔥 终极考核：答对将入驻熟词本封印 30 天';
                    if (examState.value.type === 'review') return '⏰ 记忆唤醒：31 天期满复测';
                    return '随堂测验';
                });

                // 单词展示时自动发音一遍
                watch(currentWord, (val) => {
                    if (val?.word && !examState.value.isExam) nextTick(() => speakWord(val.word));
                });

                // 初始化：仅拉取 keys（阻塞），排行榜延后加载不阻塞主流程
                onMounted(async () => {
                    try {
                        const keysRes = await fetch('/api/keys');
                        allKeys.value = await keysRes.json();
                    } catch(e) {}
                    // 延后加载排行榜，不阻塞首屏
                    const loadLeaderboard = async () => {
                        try {
                            const lbRes = await fetch('/api/leaderboard');
                            leaderboard.value = await lbRes.json();
                            const count = Object.keys(stats.value).length;
                            if (count > 0) {
                                let deviceId = localStorage.getItem('gpt_dict_device_id');
                                if (!deviceId) {
                                    deviceId = (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : 'd-' + Date.now() + '-' + Math.random().toString(36).slice(2);
                                    localStorage.setItem('gpt_dict_device_id', deviceId);
                                }
                                const sub = await fetch('/api/leaderboard', {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({ count, device_id: deviceId })
                                });
                                const data = await sub.json();
                                if (data.total) leaderboard.value = { total: data.total || [], monthly: data.monthly || [], daily: data.daily || [] };
                            }
                        } catch(e) {}
                    };
                    const runWhenIdle = (fn) => {
                        if (typeof requestIdleCallback !== 'undefined') {
                            requestIdleCallback(fn, { timeout: 3000 });
                        } else {
                            setTimeout(fn, 300);
                        }
                    };
                    runWhenIdle(loadLeaderboard);
                    // 摇一摇抽词：延后初始化，不阻塞主流程
                    runWhenIdle(() => {
                        if (typeof DeviceMotionEvent === 'undefined') return;
                        let lastShake = 0, lastCheck = 0;
                        const SHAKE_THRESHOLD = 15;
                        const SHAKE_COOLDOWN = 1500;
                        const CHECK_INTERVAL = 150;
                        const handleMotion = (e) => {
                            const now = Date.now();
                            if (now - lastCheck < CHECK_INTERVAL) return;
                            lastCheck = now;
                            const a = e.accelerationIncludingGravity || e.acceleration;
                            if (!a) return;
                            const mag = Math.sqrt((a.x||0)**2 + (a.y||0)**2 + (a.z||0)**2);
                            if (mag > SHAKE_THRESHOLD && now - lastShake >= SHAKE_COOLDOWN) {
                                lastShake = now;
                                fetchRandom();
                            }
                        };
                        if (typeof DeviceMotionEvent.requestPermission === 'function') {
                            document.addEventListener('click', function req() {
                                DeviceMotionEvent.requestPermission().then(() => {
                                    window.addEventListener('devicemotion', handleMotion, { passive: true });
                                    document.removeEventListener('click', req);
                                }).catch(() => {});
                            }, { once: true });
                        } else {
                            window.addEventListener('devicemotion', handleMotion, { passive: true });
                        }
                    });
                });

                const handleSearch = () => {
                    if (!searchQuery.value) { searchResults.value = []; return; }
                    clearTimeout(timeoutId);
                    timeoutId = setTimeout(async () => {
                        try {
                            const res = await fetch(`/api/search?q=${searchQuery.value}`);
                            searchResults.value = await res.json();
                        } catch(e) {}
                    }, 150);
                };

                // 搜索点词（只展示，不计入盲盒次数）
                const selectWord = async (word) => {
                    if (examState.value.isExam) { alert("请先完成答卷！"); return; }
                    isLoading.value = true;
                    searchQuery.value = '';
                    searchResults.value = [];
                    try {
                        const res = await fetch(`/api/word/${word}`);
                        currentWord.value = await res.json();
                    } catch(e) {}
                    isLoading.value = false;
                };

                // 自动提取中文释义并「打码原词」防作弊
                const extractHint = (data, wordStr) => {
                    let hint = "无可用中文释义，请凭直觉盲拼！";
                    for (const key in data) {
                        const kl = key.toLowerCase();
                        if (kl.includes('translation') || kl.includes('meaning') || kl.includes('词义') || kl.includes('翻译') || kl.includes('解析')) {
                            const val = data[key];
                            hint = typeof val === 'object' ? Object.values(val).join('; ') : String(val);
                            break; 
                        }
                    }
                    // 把提示里的原单词替换为马赛克
                    const regex = new RegExp(wordStr, "gi");
                    return hint.replace(regex, " <span class='bg-gray-800 text-gray-800 px-2 rounded tracking-[0] select-none'>***</span> ");
                };

                // 🎲 盲盒抽词（核心记忆大脑引擎）
                const fetchRandom = async () => {
                    if (examState.value.isExam) { alert("请先完成随堂测验才能继续抽词！"); return; }
                    if (allKeys.value.length === 0) return;

                    isLoading.value = true;
                    searchQuery.value = '';
                    searchResults.value = [];

                    const now = Date.now();
                    
                    // 【核心算法】智能跳过 30 天冷却期内的熟词
                    const eligibleKeys = allKeys.value.filter(k => {
                        if (k === 'error') return false;
                        const s = stats.value[k];
                        if (!s) return true;
                        return s.nextReview <= now; // 大于 now 说明还在 30 天休眠期内，剔除
                    });
                    
                    if (eligibleKeys.length === 0) {
                        alert("🎉 太强了！你的所有单词都已经背完，且都在 30 天的熟词休眠保护期内，去休息吧！");
                        isLoading.value = false; return;
                    }

                    const randomWord = eligibleKeys[Math.floor(Math.random() * eligibleKeys.length)];

                    try {
                        const res = await fetch(`/api/word/${randomWord}`);
                        const wordObj = await res.json();
                        
                        const s = getWordStat(randomWord);
                        s.count += 1;
                        saveStats();

                        const isReview = s.nextReview > 0 && now >= s.nextReview;

                        // 拦截考试条件：第4次、第6次、或31天复测
                        if (s.count === 4 || (!s.nextReview && s.count >= 6) || isReview) {
                            examState.value = {
                                isExam: true, word: randomWord, 
                                hint: extractHint(wordObj.data, randomWord),
                                type: isReview ? 'review' : (s.count === 4 ? 'draw4' : 'draw6'),
                                data: wordObj
                            };
                            examInput.value = '';
                            examError.value = false;
                            currentWord.value = null;
                            nextTick(() => { if (examInputRef.value) examInputRef.value.focus(); });
                        } else {
                            currentWord.value = wordObj;
                        }
                    } catch(e) {}
                    isLoading.value = false;
                };

                // 提交试卷
                const submitExam = () => {
                    if (!examInput.value.trim()) return;
                    const ans = examInput.value.trim().toLowerCase();
                    const correctAns = examState.value.word.toLowerCase();
                    
                    if (ans === correctAns) {
                        examError.value = false;
                        examState.value.isExam = false;
                        
                        const s = getWordStat(correctAns);
                        
                        // 第 6 次，或满月复习通过，打入30天冷宫
                        if (examState.value.type === 'draw6' || examState.value.type === 'review') {
                            const THIRTY_DAYS = 30 * 24 * 60 * 60 * 1000;
                            s.nextReview = Date.now() + THIRTY_DAYS;
                            saveStats();
                            setTimeout(() => alert("🏆 完美！该词已加入熟词本，为你开启 30 天的免考保护！"), 100);
                        }
                        currentWord.value = examState.value.data;
                    } else {
                        examError.value = false;
                        nextTick(() => { examError.value = true; }); // 触发抖动
                        setTimeout(() => { examError.value = false; }, 800);
                        nextTick(() => { if (examInputRef.value) examInputRef.value.focus(); });
                    }
                };

                // 放弃测验
                const giveUp = () => {
                    const w = examState.value.word;
                    const s = getWordStat(w);
                    // 严厉的降级惩罚
                    if (examState.value.type === 'review') {
                        s.nextReview = 0; // 剥夺熟词保护
                        s.count = 5;      // 退回第5次，下次抽到直接终极考核
                    } else {
                        s.count = Math.max(0, s.count - 1); // 次数倒退
                    }
                    saveStats();
                    
                    examState.value.isExam = false;
                    currentWord.value = examState.value.data;
                };

                const renderMarkdown = (text) => {
                    return marked.parseInline(String(text).replace(/\\n/g, '<br>'));
                };

                const formatKey = (key) => {
                    const map = {
                        content: "word is world",
                        meaning: "💡 词义与考点", etymology: "🏛️ 词源探秘", root: "🧩 词根词缀", roots: "🧩 词根词缀",
                        variations: "🔄 单词变形", background: "🌍 文化背景", memory: "🧠 记忆技巧", trick: "🧠 记忆窍门",
                        story: "📖 场景小故事", sentences: "📝 实用例句", example: "📝 实用例句", examples: "📝 实用例句",
                        translation: "🇨🇳 中文翻译"
                    };
                    for (let k in map) { if (key.toLowerCase().includes(k)) return map[k]; }
                    return "🔹 " + key.toUpperCase();
                };

                const speakWord = (word) => {
                    if (!word || typeof word !== 'string') return;
                    const w = word.trim();
                    if (!w) return;
                    const url = '/api/audio/' + encodeURIComponent(w);
                    const audio = new Audio(url);
                    audio.play().catch(() => {});
                };

                return { 
                    searchQuery, searchResults, currentWord, isLoading, stats, masteredCount, phoneticText,
                    encounteredWords, modalType, modalWords, leaderboard, openModal, closeModalAndSelect,
                    examState, examInput, examError, examInputRef, examTitle,
                    handleSearch, selectWord, fetchRandom, submitExam, giveUp, renderMarkdown, formatKey, speakWord 
                };
            }
        }).mount('#app');
    </script>
</body>
</html>
"""
