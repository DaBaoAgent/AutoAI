# 阶段二：批量采集与双通道转写

日期：2026-07-31

## 已落地

- 生成剩余 AI 收藏的可断点处理清单；当前尚余 452 条。
- 当前清单分流结果：356 条进入 OpenVINO base 快通道，96 条进入
  OpenVINO small 专名通道。
- 支持递归解析抖音收藏列表 JSON 响应中的作品 ID、时长、独立音频候选
  和视频候选地址。
- 临时签名地址只保存在 `data/tmp/`，不会进入长期来源清单。
- 下载器优先尝试独立音频，并用作品时长校验；失败后自动回退到 MP4，
  抽取标准 M4A 后删除视频中间文件。
- OpenVINO worker 会按标题自动选模型，复用已加载模型，保存时间戳、
  引擎、模型、推理耗时和画面核验标记。
- 逐字稿仍写入原有 SQLite、FTS 和文本分块结构，不改变智能体查询方式。
- 已打通无需 Cookie 的公开分享页解析：首批 3/3、扩展批 20/20 成功，
  两批均为 0 失败。
- 已增加一键断点命令 `process-batch`，串联公开解析、下载和转写。
- 已增加全量续跑命令 `process-all`：按短批次循环，隔离单条转写失败，
  连续无进展时安全停止，完成后自动更新向量索引与知识图谱。
- 已将 GitHub 视频 OCR 从固定密集抽帧改为逐字稿名次时间点定向抽帧。
- 仓库证据只接受完整 `github.com/owner/repo` 或 GitHub 页面明确的
  `owner/repo Public` 标题，不再把页面目录路径误记成仓库。
- 首个正式批次已完成 10/10 下载和 10/10 转写，0 失败；加上前置样本，
  当前累计转写 49/501 条。
- 语义索引默认增量更新：保留相同模型的已有向量，只为新分段生成向量；
  `index --rebuild` 可在模型或策略变化后执行全量重建。

## 数据分层

```text
data/sources/douyin_processing_queue.json
  长期清单：ID、标题、来源、通道、状态；不含签名地址

data/tmp/douyin_media_candidates.json
  临时清单：当前捕获的音视频签名地址；可随时删除、重新捕获

data/media/<作品ID>.m4a
  长期媒体：统一 M4A

data/transcripts/<作品ID>.json|md
  长期知识：逐字稿、时间戳、ASR 元数据、画面核验标记
```

## 运行顺序

```powershell
.\.venv\Scripts\dabo-kb.exe build-queue
.\.venv\Scripts\dabo-kb.exe fetch-public --limit 20 --workers 4
.\.venv\Scripts\dabo-kb.exe import-capture data\tmp\favorite-response.json
.\.venv\Scripts\dabo-kb.exe download-media --limit 20
.\.venv\Scripts\dabo-kb.exe transcribe-smart --limit 20
.\.venv\Scripts\dabo-kb.exe process-batch --limit 10
.\.venv\Scripts\dabo-kb.exe process-all --batch-size 10 --workers 4
.\.venv\Scripts\dabo-kb.exe process-all --count 50 --batch-size 10 --workers 4
.\.venv\Scripts\dabo-kb.exe ocr-needed --limit 20
.\.venv\Scripts\dabo-kb.exe index
.\.venv\Scripts\dabo-kb.exe index --rebuild
.\.venv\Scripts\dabo-kb.exe graph
```

`import-capture` 默认按作品 ID 合并多个分页响应；使用 `--replace` 可清空
已经过期的临时地址后重新开始。

## 采集结论

Chrome 收藏页一次可以读取 64 条已加载作品卡片，可用于确认收藏归属。
Chrome 网络监听会使控制通道不稳定，因此不再作为主下载路径。

主路径改为：以已确认收藏清单中的作品 ID 请求公开分享页，解析页面内嵌
作品 JSON，再进入临时媒体候选队列。该路径不读取 Cookie；首轮连续
23 条真实作品解析全部成功，随后的一键正式批次也保持 10/10 成功，
速度已明显超过每小时 100 条的阶段目标。
