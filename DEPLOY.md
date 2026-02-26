# Zeabur 部署说明

## 1. 连接 GitHub 仓库

在 Zeabur 创建项目 → 添加服务 → 选择「从 GitHub 部署」→ 选择本仓库。

## 2. 挂载持久化卷（必须）

应用会将 SQLite 数据库和 TTS 音频缓存写入 `/data`。请在 Zeabur 服务页：

- 打开 **Volumes** 标签
- 点击 **Mount Volumes**
- **Mount Directory** 填写：`/data`
- **Volume ID** 填写：`data`（或任意标识符）

未挂载时，重启后排行榜与音频缓存会清空，但应用仍可运行。

## 3. 环境变量（可选）

| 变量 | 说明 | 默认 |
|------|------|------|
| `DATA_DIR` | 数据目录（排行榜、音频缓存） | `/data` |
| `DATA_DB` | SQLite 数据库路径 | `{DATA_DIR}/data.db` |
| `GPTWORDS_PATH` | 词库文件路径 | `{WORKDIR}/gptwords.json` |
| `PORT` | 监听端口 | `8000` |

Dockerfile 已设置 `DATA_DIR=/data`，一般无需修改。

## 4. 构建说明

- **gptwords.json**：构建时自动从 GitHub/CDN 下载，无需随代码提交
- **static/**：前端 HTML 需随代码提交
- **端口**：Zeabur 会注入 `PORT`，应用会自动使用
