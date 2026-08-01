# 大宝 AI 知识库同步状态

更新时间：2026-07-31（Asia/Shanghai）

## 已完成

- 已从“徐艾伦 Alan”（抖音号：`tielanhai`）收藏页完整发现 1,479 条真实收藏。
- 已隔离 8 条页面推荐，未把它们误当收藏。
- 已判定 501 条为 AI 候选。
- 全量续跑启动前已保存并转写 49 条 AI 收藏音频。
- `process-all` 首个正式断点已完成 10/10 下载、10/10 转写、0 失败，
  新增 64,484 字；任务仍在后台继续下一批。
- 首个断点时累计 59/501 条、16,084 个可检索分段；语义向量将在全量
  任务结束后自动增量补齐。
- 已生成知识图谱：501 个文档、1,482 个节点、2,675 条边；纯数字噪声标签
  已移除，相关实体优先返回仓库、项目与核心概念。
- 已生成全文索引与多语言语义向量索引。
- 已通过 MCP 内存调用验证 4 个工具：`search_knowledge`、`get_source`、`related_entities`、`knowledge_status`。

## 英文项目名质量门

- `7435930193543449897` 已按 1 秒间隔做画面 OCR。
- 语音误识别的项目名已由画面确认：
  - `FaceFusion`
  - `FunClip`
  - `Pyramid Flow`
  - `PaintsUndo`
- `7648700670300451441` 已按 10 秒间隔识别 40 分钟无有效旁白视频，保留 184 张英文证据帧；没有把 API 路径、文件名、日期或单位误记为 GitHub 仓库。
- 当前共有 58 条已验证名称记录。
- `7664123134932536616` 已用 41 张定向证据帧确认 19 个 GitHub 仓库和
  `Desktop Commander MCP`，并清除了页面目录路径等假阳性。
- `7663547890190634278` 已用 12 张定向证据帧确认 5 个仓库：
  `OpenMOSS/MOSS-Transcribe-Diarize`、`stablyai/orca`、
  `VoltAgent/awesome-design-md`、`HKUDS/Vibe-Trading`、
  `hasaneyldrm/exercises-dataset`。

## 待继续

- AI 候选总数：501
- 全量任务起点：已转写 49，剩余 452
- 首个断点：已转写 59，剩余 442
- 当前任务仍在运行，请以 `dabo-kb status` 和
  `data/sources/douyin_process_all_checkpoint.json` 为准。
- 2026-07-31 浏览器复测时登录态正常、未显示验证码。

## 阶段一性能结论

- OpenVINO `whisper-base-int8` / CPU：13.96× 实时，作为普通内容快通道。
- OpenVINO `whisper-small-int8` / CPU：5.14× 实时，作为 GitHub/开源项目专名通道。
- Intel Iris Xe GPU、whisper.cpp 和原 faster-whisper small 均未达到最优速度。
- 逐条打开抖音详情页获取媒体地址未达到每小时 100 条，已淘汰。
- 剩余 452 条按可断点批次继续，不需要再依赖收藏列表接口监听。
- 完整报告：`benchmarks/phase1/REPORT.md`

## 阶段二开发进度

- 已生成剩余 452 条的断点清单：
  `data/sources/douyin_processing_queue.json`。
- 已自动分流：356 条进入 OpenVINO base 快通道，96 条进入
  OpenVINO small 专名通道。
- 已实现收藏列表 JSON 响应解析、临时地址隔离、音频优先下载、
  时长校验和 MP4 回退抽音轨。
- 已接入 OpenVINO 双通道正式转写入口 `transcribe-smart`，并验证隔离
  环境及两个模型可用。
- 收藏页当前一次可读取 64 条真实作品卡片；真实列表接口响应的自动捕获
  仍受 Chrome 页面接管超时影响，因此已改用无需 Cookie 的公开分享页。
- 公开分享页真实验证 23/23 成功、0 失败；首批 4 条已完成下载和双通道
  转写，其中 base 实测 10.21–12.47×，small 实测 4.98×。
- 首条 GitHub 榜单已用 41 张定向证据帧核验，保留 19 个明确仓库地址和
  1 个明确项目名，已清除目录路径等假阳性；临时原视频已删除。
- 下一批 10 条一键流水线已完成：10/10 下载、10/10 转写、0 失败，
  共新增 33,514 字；其中 5 条已标记为画面核验候选。
- 向量索引已改为增量模式，本轮复用 8,018 个旧向量，仅补算 3,144 个；
  后续每批不再重算全部历史内容。
- 详细说明：`docs/PHASE2_PIPELINE.md`

继续时先运行：

```powershell
.\.venv\Scripts\dabo-kb.exe status
.\.venv\Scripts\dabo-kb.exe pending --limit 20
.\.venv\Scripts\dabo-kb.exe ocr-needed --limit 20
```

## 隐私与文件策略

- 未读取、复制或保存 Chrome Cookie、Local Storage、密码或会话文件。
- 优先长期保存 M4A 音频；仅在画面核验需要时临时保存 MP4。
- 画面核验使用的临时 MP4 均已删除；公开媒体候选地址只留在可随时重建的
  `data/tmp/`，不会进入长期知识档案。
- 所有知识库数据保持在 `D:\@kaifa\shoucang`，没有上传或发布。
