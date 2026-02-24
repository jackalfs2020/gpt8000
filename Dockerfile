FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN curl -L -o gptwords.json https://raw.githubusercontent.com/Ceelog/DictionaryByGPT4/main/gptwords.json || \
    curl -L -o gptwords.json https://cdn.jsdelivr.net/gh/Ceelog/DictionaryByGPT4@main/gptwords.json
COPY app.py .
EXPOSE 8000
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}
