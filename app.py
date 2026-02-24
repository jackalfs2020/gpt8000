import json
import random
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

words_data = {}
words_keys = []

def load_data():
    global words_data, words_keys
    try:
        with open("gptwords.json", "r", encoding="utf-8") as f:
            content = f.read().strip()
            try:
                data = json.loads(content)
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
            except Exception:
                for line in content.splitlines():
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                        w = item.get("word") or item.get("Word") or item.get("name") or item.get("headWord")
                        if not w and isinstance(item, dict):
                            keys = list(item.keys())
                            if keys:
                                w, item = keys[0], item[keys[0]]
                        if w:
                            words_data[str(w).lower()] = item if isinstance(item, dict) else {"解析": item}
                    except Exception:
                        continue

        words_keys = list(words_data.keys())
        print(f"✅ 成功加载词库：共 {len(words_keys)} 个词汇！")
    except FileNotFoundError:
        print("❌ 未找到 gptwords.json，请先下载词库到当前目录。")
    except Exception as e:
        print(f"❌ 数据加载错误: {e}")

load_data()

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

@app.get("/api/word/{word}")
def api_word(word: str):
    word = word.lower()
    if word in words_data: return {"word": word, "data": words_data[word]}
    return {"word": word, "data": {}}

@app.get("/api/random")
def api_random():
    if not words_keys: return {"word": "Error", "data": {"消息": "词库加载中或异常"}}
    w = random.choice(words_keys)
    return {"word": w, "data": words_data[w]}

@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_CONTENT

HTML_CONTENT = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GPT4 智能单词本</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/vue@3/dist/vue.global.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        [v-cloak] { display: none; }
        .markdown-body p { margin-bottom: 0.5rem; line-height: 1.6; }
        .markdown-body strong { color: #4f46e5; }
    </style>
</head>
<body class="bg-gray-50 text-gray-800 font-sans min-h-screen">
    <div id="app" v-cloak class="max-w-3xl mx-auto p-4 sm:p-6 lg:p-8 relative">
        <header class="text-center mb-8 mt-4">
            <h1 class="text-4xl font-black text-indigo-600 mb-2 tracking-tight">GPT4 智能单词本</h1>
            <p class="text-gray-500 font-medium">8000+ 高频词汇深度解析 · 部署于 Zeabur</p>
        </header>

        <main>
            <div class="relative mb-6 z-20">
                <input v-model="searchQuery" @input="handleSearch" type="text" 
                       class="w-full px-5 py-4 rounded-2xl border border-gray-200 shadow-sm focus:border-indigo-500 focus:ring-4 focus:ring-indigo-100 focus:outline-none text-lg transition-all"
                       placeholder="输入你想查询的单词 (例如: abandon)..." autocomplete="off">
                <button @click="fetchRandom" class="absolute right-3 top-3 bg-indigo-50 text-indigo-600 font-bold px-4 py-2 rounded-xl hover:bg-indigo-100 transition-colors shadow-sm">
                    🎲 盲盒抽词
                </button>
            </div>

            <div v-if="searchResults.length > 0 && searchQuery" class="bg-white rounded-2xl shadow-xl border border-gray-100 mb-6 overflow-hidden absolute w-[calc(100%-2rem)] sm:w-[calc(100%-3rem)] lg:w-[calc(100%-4rem)] max-w-3xl z-30">
                <ul class="divide-y divide-gray-50 max-h-80 overflow-y-auto">
                    <li v-for="item in searchResults" :key="item.word" @click="selectWord(item.word)" 
                        class="px-6 py-4 hover:bg-indigo-50 cursor-pointer transition-colors flex justify-between items-center group">
                        <span class="font-bold text-gray-700 group-hover:text-indigo-600 text-lg">{{ item.word }}</span>
                        <span class="text-gray-400 text-sm">查看解析 ➔</span>
                    </li>
                </ul>
            </div>

            <div v-if="currentWord && !searchQuery" class="bg-white rounded-3xl shadow-sm border border-gray-100 overflow-hidden mt-6">
                <div class="bg-gradient-to-r from-indigo-600 to-blue-500 px-8 py-8 text-white">
                    <h2 class="text-5xl font-black tracking-wide capitalize">{{ currentWord.word }}</h2>
                </div>
                <div class="p-8 space-y-6">
                    <div v-for="(value, key) in currentWord.data" :key="key">
                        <template v-if="!['word', 'headword'].includes(key.toLowerCase()) && value">
                            <h3 class="text-sm text-indigo-500 font-extrabold uppercase tracking-widest mb-3 flex items-center">
                                {{ formatKey(key) }}
                            </h3>
                            <div v-if="typeof value === 'object'" class="space-y-3 bg-indigo-50/30 p-5 rounded-2xl markdown-body">
                                <p v-for="(v, k) in value" :key="k" class="text-gray-700">
                                    <span v-if="isNaN(k)" class="font-bold text-gray-900 mr-1">{{ k }}: </span>
                                    <span v-html="renderMarkdown(String(v))"></span>
                                </p>
                            </div>
                            <div v-else class="text-gray-700 bg-indigo-50/30 p-5 rounded-2xl markdown-body" v-html="renderMarkdown(String(value))"></div>
                        </template>
                    </div>
                </div>
            </div>

            <div v-else-if="!searchQuery && !isLoading" class="text-center text-gray-400 mt-24">
                <div class="text-6xl mb-4 opacity-50">✨</div>
                <p class="text-lg">开始搜索单词，或者点击盲盒探索词库</p>
            </div>
            
            <div v-if="isLoading" class="text-center text-gray-400 mt-20">
                <p class="text-lg animate-pulse font-bold">云端检索中...</p>
            </div>
            
            <div v-if="searchResults.length > 0 && searchQuery" @click="searchQuery = ''" class="fixed inset-0 z-10"></div>
        </main>
    </div>

    <script>
        const { createApp, ref, onMounted } = Vue;
        createApp({
            setup() {
                const searchQuery = ref('');
                const searchResults = ref([]);
                const currentWord = ref(null);
                const isLoading = ref(false);
                let timeoutId = null;

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

                const selectWord = async (word) => {
                    isLoading.value = true;
                    searchQuery.value = '';
                    searchResults.value = [];
                    try {
                        const res = await fetch(`/api/word/${word}`);
                        currentWord.value = await res.json();
                    } catch(e) {}
                    isLoading.value = false;
                };

                const fetchRandom = async () => {
                    isLoading.value = true;
                    searchQuery.value = '';
                    searchResults.value = [];
                    try {
                        const res = await fetch(`/api/random`);
                        currentWord.value = await res.json();
                    } catch(e) {}
                    isLoading.value = false;
                };

                const renderMarkdown = (text) => {
                    return marked.parseInline(text.replace(/\\n/g, '<br>'));
                };

                const formatKey = (key) => {
                    const map = {
                        meaning: "💡 词义与考点", etymology: "🏛️ 词源探秘", root: "🧩 词根词缀", roots: "🧩 词根词缀",
                        variations: "🔄 单词变形", background: "🌍 文化背景", memory: "🧠 记忆技巧", trick: "🧠 记忆窍门",
                        story: "📖 场景小故事", sentences: "📝 实用例句", example: "📝 实用例句", examples: "📝 实用例句",
                        translation: "🇨🇳 中文翻译"
                    };
                    for (let k in map) { if (key.toLowerCase().includes(k)) return map[k]; }
                    return "🔹 " + key.toUpperCase();
                };

                onMounted(() => { fetchRandom(); });
                return { searchQuery, searchResults, currentWord, isLoading, handleSearch, selectWord, fetchRandom, renderMarkdown, formatKey };
            }
        }).mount('#app');
    </script>
</body>
</html>
"""
