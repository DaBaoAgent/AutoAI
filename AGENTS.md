# 大宝 AI 本地知识库

处理 AI 项目、工具、Agent、Skill、提示词、AI 视频或开源项目问题时，先查询本知识库再回答。

## 查询

在仓库根目录运行：

```powershell
.\.venv\Scripts\dabo-kb.exe search "问题或关键词" --limit 8
```

若可用，优先调用 `dabo-local-kb` MCP 的 `search_knowledge` 工具。回答时保留结果中的来源标题、抖音 URL 和时间戳，不把推测写成原视频结论。

## 数据边界

- `data/sources/`：收藏清单与来源元数据。
- `data/media/`：只长期保留音频；MP4 是可删除的中间文件。
- `data/transcripts/`：逐字稿 Markdown/JSON。
- `data/index/knowledge.db`：SQLite 主索引。
- `data/graph/`：知识图谱 JSON/GraphML。
- 不读取、复制或保存浏览器 Cookie；抖音授权只在用户的 Chrome 页面内使用。

## 更新

新增收藏后运行：

```powershell
.\.venv\Scripts\dabo-kb.exe ingest data\sources\douyin_favorites.json
.\.venv\Scripts\dabo-kb.exe index
.\.venv\Scripts\dabo-kb.exe graph
```

