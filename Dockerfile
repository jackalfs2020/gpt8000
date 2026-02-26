FROM python:3.11-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码与静态资源
COPY app.py .
COPY static/ ./static/

# 词库：构建时下载（仓库中 gptwords.json 被 .gitignore，不随代码推送）
RUN curl -fsSL -o gptwords.json "https://raw.githubusercontent.com/Ceelog/DictionaryByGPT4/main/gptwords.json" || \
    curl -fsSL -o gptwords.json "https://cdn.jsdelivr.net/gh/Ceelog/DictionaryByGPT4@main/gptwords.json" || \
    (echo '{"error":{"消息":"词库加载失败，请检查网络"}}' > gptwords.json)

# Zeabur 等平台使用 /data 持久化，需事先创建
RUN mkdir -p /data

ENV DATA_DIR=/data
ENV PORT=8000
EXPOSE 8000

# 使用 shell 形式以便运行时展开 PORT 环境变量
CMD sh -c "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"
