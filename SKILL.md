---
name: autoai
description: 大宝AI知识库：抖音收藏转写、检索、图谱与自适应迭代.
version: 2.0.0
author: 大宝
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [knowledge-base, transcription, douyin, whisper, openvino, self-healing]
    category: data-science
    config:
      skills.config.autoai_root:
        type: string
        default: "D:\\@kaifa\\autoai"
---

# AutoAI 本地知识库（自迭代版）

处理抖音 AI 收藏视频的全流程：下载音轨 → OpenVINO Whisper 转写 → 向量索引 → 知识图谱。
**v2.0 新增：自适应失败恢复、浏览器回退提取、增量收藏同步、故障模式自诊断。**

## 项目位置

`D:\@kaifa\autoai`，CLI 入口：`.\\.venv\\Scripts\\dabo-kb.exe`

## 核心命令

```powershell
# 进度
.\.venv\Scripts\dabo-kb.exe status

# 全自动处理（推荐）
.\.venv\Scripts\dabo-kb.exe process-all --count N --batch-size 10 --workers 8 --parallel-workers 1

# 独立步骤
.\.venv\Scripts\dabo-kb.exe build-queue
.\.venv\Scripts\dabo-kb.exe fetch-public --limit N --workers 8
.\.venv\Scripts\dabo-kb.exe download-media --limit N
.\.venv\Scripts\dabo-kb.exe transcribe-smart --limit N --parallel-workers 1

# 检索
.\.venv\Scripts\dabo-kb.exe search "关键词" --limit 8

# 索引与图谱
.\.venv\Scripts\dabo-kb.exe index
.\.venv\Scripts\dabo-kb.exe index --rebuild
.\.venv\Scripts\dabo-kb.exe graph

# OCR 核验
.\.venv\Scripts\dabo-kb.exe ocr-needed --limit 50
.\.venv\Scripts\dabo-kb.exe ocr-video <ID> --targeted

# 🆕 自诊断
.\.venv\Scripts\dabo-kb.exe diagnose
.\.venv\Scripts\dabo-kb.exe heal --limit 50
```

## 数据处理流程

1. **收藏导入**：`ingest data\\sources\\douyin_favorites_full_2026-07-30.json --replace-source`
2. **公开页解析**：`fetch-public`（8线程并行抓取 `iesdouyin.com/share/video/{id}/`）
3. **音轨下载**：`download-media`（4线程并行，优先下载独立音频，失败则从视频抽音轨）
4. **双通道转写**：`transcribe-smart`（base/small 模型分流，OpenVINO int8 量化）

## 🆕 Playwright绕限流方案（2026-07-31验证）

**curl请求`iesdouyin.com/share/video/{id}/`会被抖音限流**（首批成功后IP被ban，返回空页面）。
**Playwright + 移动端UA可完美绕过**，连续98条请求无一失败。

### 使用方式

```powershell
cd D:\@kaifa\autoai
.\.venv\Scripts\python.exe playwright_fetch.py
```

脚本自动：
1. 从DB读取所有`status!='transcribed'`的AI文档ID
2. 用Playwright逐条访问分享页提取`_ROUTER_DATA`
3. 保存为`douyin_media_candidates.json`（兼容现有pipeline格式）
4. 请求间隔2-5s随机化，避免被二次限流

提取完成后运行：
```powershell
.\.venv\Scripts\python.exe -m dabo_kb.cli download-media --limit 50
.\.venv\Scripts\python.exe -m dabo_kb.cli transcribe-smart --limit 50 --parallel-workers 1
```

### 技术要点
- UA必须为移动端：`Android 13; Pixel 7 + Chrome 138`
- 必须用`new_page()`每请求新建页面（共享context否则cookie累积触发检测）
- 新SSR格式的`music`对象无`play_url`，video_urls走`video.play_addr.url_list`

## 🆕 自适应迭代系统（v2.0）

### 故障模式分类

管道可能因以下原因失败，需针对性处理：

| 故障类型 | 症状 | 根因 | 恢复策略 |
|---------|------|------|---------|
| `FETCH_TEMPORARY` | `公开分享页中未找到作品数据`（偶发） | 抖音临时限流/CDN波动 | 等待30s后重试（最多3次），指数退避 |
| `FETCH_PERMANENT` | 连续3批全部fetch_failed | 视频已删除/设为私密 | 标记为`unreachable`，30天后重试 |
| `FETCH_FORMAT_CHANGE` | `公开分享页作品数据不完整` | 抖音SSR格式变更 | 回退到浏览器提取，同时记录新格式 |
| `DOWNLOAD_EXPIRED` | `所有临时地址均失败或过期` | CDN签名地址过期 | 重新fetch获取新签名URL |
| `TRANSCRIBE_FAILED` | 转写失败 | 音频损坏/编码问题 | 重新下载音轨 |

### 自适应重试策略

```
首次失败 → 30s等待 → 重试1
二次失败 → 60s等待 → 重试2（换UA）
三次失败 → 120s等待 → 重试3（浏览器回退）
仍失败   → 标记dead，入dead-letter队列
```

### 浏览器回退提取

当curl无法从`iesdouyin.com/share/video/{id}/`提取`_ROUTER_DATA`时：
1. 使用`browser_navigate`访问 `https://www.iesdouyin.com/share/video/{id}/`
2. 通过`browser_console`注入JS提取`window._ROUTER_DATA`
3. 解析`videoInfoRes.item_list[0]`获取视频元数据
4. 注意：新格式无`music.play_url`，需从`video.play_addr.url_list`下载视频后ffmpeg提音轨

### 🆕 增量收藏自动同步

`sync_favorites.py` — 每6小时自动运行：
1. Playwright + Chrome Profile 继承登录态
2. 调用抖音收藏API / 页面提取获取全部收藏列表
3. 对比DB已有ID，仅处理新增
4. 自动走完整管线：ingest → fetch → download → transcribe
5. 无新收藏时跳过（零token消耗）

```powershell
cd D:\@kaifa\autoai
.\.venv\Scripts\python.exe sync_favorites.py
```

同步状态记录在 `data/sources/sync_state.json`。

### 自诊断流程

执行 `diagnose` 命令时检查：
1. 网络连通性（`iesdouyin.com` 可达性）
2. 未处理项重试可行性（随机抽3个测试fetch）
3. 磁盘空间（media/transcripts目录）
4. OpenVINO模型完整性
5. 死信队列大小

### 健康检查清单

处理前必检：
- [ ] `iesdouyin.com` 可达（`curl -o /dev/null -sS --connect-timeout 5 https://www.iesdouyin.com/`）
- [ ] 磁盘剩余 > 2GB（media目录在快速增长）
- [ ] 无其他transcribe进程在跑（`tasklist | grep python`）
- [ ] OpenVINO模型文件存在（`benchmarks/phase1/models/`）

## 性能优化（2026-07 已实施）

| 模块 | 优化 | 文件 |
|------|------|------|
| 下载并行化 | `download_pending` 改为 ThreadPoolExecutor（默认4线程） | `src/dabo_kb/acquire.py` |
| 转录并行化 | 新增 `_transcribe_one` + `--parallel-workers` 参数 | `src/dabo_kb/openvino_asr.py` |
| 全链路透传 | `pipeline.py` 和 `cli.py` 全部透传 `parallel_workers` | `src/dabo_kb/pipeline.py`, `cli.py` |

## 关键发现

1. **4核CPU下 `--parallel-workers 1` 是最优配置**。OpenVINO 已内部多线程使用全部核心，再加 ThreadPoolExecutor 并行会导致 CPU 争抢，x_realtime 从 10-15x 掉到 1-3x。

2. **多个转录进程不能同时跑**。两个 `transcribe-smart` 或 `process-all` 实例会严重竞争 CPU。

3. **公开页失效多为临时性**。`iesdouyin.com/share/video/` 的 `_ROUTER_DATA` SSR数据在2026-07-31经实测仍然可用，之前的大批量`fetch_failed`可能是抖音临时限流或网络波动。**关键：curl必须携带移动端UA**（`Mozilla/5.0 (Linux; Android 13; Pixel 7)...`）。

4. **base 模型比 small 模型快约3倍**。base 模型 x_realtime 10-15x，small 模型 3-5x。

5. **临时签名地址会过期**。`process-all` 的短批次（batch-size=10）策略就是为了避免地址过期。

6. **🆕 新SSR格式无独立音频URL**。`_ROUTER_DATA`中的`music`对象不再包含`play_url`，需从`video.play_addr.url_list`下载视频后用ffmpeg提取音轨。

7. **🆕 浏览器可绕过部分限流**。当curl被限流时，Hermes内置的`browser_navigate`工具可直接渲染分享页并提取数据，成功率更高。

## 项目结构

```
D:\@kaifa\autoai\
├── src/dabo_kb/           # Python 源码
│   ├── cli.py             # CLI 入口
│   ├── pipeline.py        # process-all 编排
│   ├── acquire.py         # 抓取/下载
│   ├── openvino_asr.py    # OpenVINO 转写
│   ├── ocr.py             # 画面 OCR
│   ├── search.py          # 全文检索
│   ├── vector.py          # 语义向量索引
│   ├── graph.py           # 知识图谱
│   └── db.py              # SQLite 数据层
├── benchmarks/phase1/     # OpenVINO 独立环境
│   ├── .venv-openvino/    # 隔离的 Python 环境
│   └── models/            # int8 量化模型
├── data/
│   ├── sources/           # 收藏清单 + 处理队列
│   ├── media/             # 长期保存的 m4a 音频
│   ├── transcripts/       # Markdown + JSON 逐字稿
│   ├── index/             # SQLite + 向量索引
│   └── graph/             # JSON/GraphML 知识图谱
└── logs/                  # 运行日志
```

## 注意事项

- 项目使用独立 OpenVINO 环境（`benchmarks/phase1/.venv-openvino/`），不污染主 `.venv`
- 转写子进程通过 `PYTHONPATH` 找到 `src/dabo_kb/` 模块
- `data/sources/douyin_process_all_checkpoint.json` 是 `process-all` 的断点文件
- 处理队列在 `data/sources/douyin_processing_queue.json`
- 临时签名地址在 `data/tmp/douyin_media_candidates.json`（有时效性）

## 🆕 AI趋势猎手（每日聚合）

每天从GitHub、知识图谱、Web搜索等多渠道聚合最新AI技能/开源项目，生成日报并邮件发送。

### 触发

- "今天有什么新项目" / "AI趋势" / "最新开源" / "日报"
- 每日9:00定时自动运行

### 数据源

| 平台 | 方法 | 频率 |
|------|------|------|
| GitHub | Trending API (`api.github.com/search`) | 每日 |
| 知识图谱 | 已转录热榜/AI周报视频检索 | 持续 |
| 小红书 | `web_search site:xiaohongshu.com` | 按需 |
| 知乎 | `web_search site:zhihu.com` | 按需 |
| B站 | `web_search site:bilibili.com` | 按需 |
| X/Twitter | `x_search` | 按需 |

### 执行流程

```
1. GitHub API → 拉取今日trending AI项目
2. 知识图谱 → 检索最新热榜/AI周报视频
3. Web搜索 → 小红书/知乎/B站最新AI动态
4. 过滤 → 保留适合Windows笔记本的（轻量、无GPU）
5. 聚合 → 去重、分类、排序
6. 输出 → Markdown日报 + 邮件发送
```

### 日报格式

```markdown
# 🧠 AI趋势日报 — {日期}

## 🔥 GitHub Trending Top 5
| 项目 | Stars | 描述 | 笔记本 |
|------|-------|------|--------|
| xxx/yyy | ⭐1.2k | ... | ✅/⚠️ |

## 📊 知识图谱最新
- ...

## 💡 今日推荐安装
1. **xxx** — 理由...
```

### 过滤标准（适合你的笔记本）

- ✅ CLI工具、纯Python/Node.js、浏览器插件
- ❌ 需高端GPU、仅Linux/macOS、需付费API

### 邮件发送

日报生成后，使用 `send-email` skill 自动发送到默认邮箱：
```bash
python "C:\Users\xxx13\AppData\Local\hermes\skills\email\send-email\scripts\send_email.py" \
  --subject "🧠 AI趋势日报 — {日期}" \
  --body-file "日报文件路径"
```

### 知识图谱中已追踪的定期源

- **GitHub周榜**: 7667879568950609139, 7653037339651902761, 7664123134932536616
- **AI一周大事**: 7664255108602924294, 7661647414121467151, 7656464471656795428
- **科技补全**: 7624862865501015322
- **每日开源**: 7509070918733597967

## 🆕 处理死信队列

当`process-all`因连续零进度自动停止时：
```powershell
# 诊断当前状态
.\.venv\Scripts\dabo-kb.exe status

# 等待30分钟后重试（避开限流窗口）
# 减小批次和并发
.\.venv\Scripts\dabo-kb.exe process-all --count 20 --batch-size 5 --parallel-workers 1
```

如果仍失败，说明视频本身不可达（已删除/私密），标记后跳过。
