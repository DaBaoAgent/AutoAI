# 阶段一实测报告

日期：2026-07-31  
结论：**ASR 方案通过下限，批量采集方案未通过；暂不启动剩余 466 条全量任务。**

## 1. 已完成

- 建立 20 条、总时长 128.6 分钟的分层基准集。
- 覆盖短视频、普通教程、长教程、访谈、GitHub/开源项目盘点。
- 所有引擎、模型、缓存和结果均放在 `benchmarks/phase1/`，未污染正式 Python 环境。
- 克隆 `ggml-org/whisper.cpp` 源码快照，安装官方 Windows x64 `v1.9.1` 发布包。
- 安装独立 OpenVINO GenAI 2026.2.1 环境。
- 固定下载官方 OpenVINO `whisper-small-int8-ov` 和 `whisper-base-int8-ov` 模型修订。
- 使用 Chrome 现有登录态验证抖音页面和媒体地址获取路径。
- 正式知识库仍为 1479 篇文档、501 篇 AI 内容、35 篇已转写、466 篇待处理、8018 个片段；本阶段未修改正式数据库。

## 2. 本机条件

- CPU：Intel Core i7-11390H，4 核 8 线程
- GPU：Intel Iris Xe，无独立显卡
- OpenVINO 可用设备：CPU、GPU
- D 盘阶段一开始时可用空间：约 96 GB

## 3. ASR 实测

冒烟集为短、中、长三条真实收藏音频，总时长 1702.2 秒（28.37 分钟）。  
`x realtime` 越高越快；英文专名召回只统计已由画面或元数据确认的名称。

| 引擎 | 模型/设备 | 样本 | x realtime | 首次加载 | 英文专名召回 | 判定 |
|---|---|---:|---:|---:|---:|---|
| faster-whisper | small int8 / CPU | 3 | 3.308× | 4.199 s | 76.47% | 速度淘汰 |
| whisper.cpp 1.9.1 | small / CPU | 2（早停） | 1.679× | 未单列 | 71.43% | 明显更慢，早停 |
| OpenVINO GenAI | small int8 / CPU | 3 | **5.140×** | 2.911 s | 70.59% | 通过速度下限 |
| OpenVINO GenAI | small int8 / Iris Xe | 1 | 2.157× | 35.234 s | 100%（4 个词） | GPU 路线淘汰 |
| OpenVINO GenAI | base int8 / CPU | 3 | **13.960×** | 1.200 s | 41.18% | 仅用于普通内容快通道 |

重要观察：

- OpenVINO small 在 24 分钟长视频上达到 5.32×，长音频没有明显掉速。
- OpenVINO base 在同一长视频上达到 14.26×，但 GitHub 项目名掉点明显。
- GitHub 盘点样本中，small 命中 5/10 个已确认名称，base 只命中 2/10。
- 因此不能只换成小模型全量跑；最快且稳妥的是分流。

## 4. 推荐的双通道转写

### 快通道

适用：普通中文教程、观点、访谈、提示词、工作流说明。

- OpenVINO `whisper-base-int8` / CPU
- 目标速度约 10–14×
- 生成初稿和时间戳

### 专名通道

适用：标题包含 GitHub、开源、热榜、排行榜、项目盘点，以及大量英文工具名的内容。

- OpenVINO `whisper-small-int8` / CPU
- 目标速度约 4–5.3×
- 抽取疑似项目名并与标题、描述交叉验证
- 名称仍不确定时，下载原视频、抽关键帧并 OCR
- 最后用 GitHub 仓库搜索确认规范拼写

当前 466 条待处理内容中：

- 严格按“GitHub/开源/热榜/排行榜”匹配：93 条，约 20%
- 扩大到“项目/工具/Skill/Agent/模型/插件/框架/部署”：289 条，约 62%

建议先用严格规则进入专名通道，再根据初稿中的英文密度和名称验证结果动态升级，避免 62% 全部走慢通道。

## 5. 预计耗时

现有 35 条音频的中位时长为 4.70 分钟，平均时长为 7.78 分钟。以下只估算 ASR，不含采集下载和 OCR：

| 策略 | 按中位时长估算 | 按平均时长估算 |
|---|---:|---:|
| 原 faster-whisper small | 约 11.0 小时 | 约 18.3 小时 |
| 全部 OpenVINO small | 约 7.1 小时 | 约 11.8 小时 |
| 全部 OpenVINO base | 约 2.6 小时 | 约 4.3 小时 |
| 20% small + 80% base | **约 3.5 小时** | **约 5.8 小时** |

双通道相比原方案预计节省约 65%–70% 的纯转写时间。

## 6. 抖音采集实测

- Chrome 登录态有效，测试时未出现验证码，视频可正常播放。
- 已能从加载完成的播放器读取临时媒体地址。
- 当前得到的是 `video_mp4`，未观察到独立音频地址。
- 页面资产接口因抖音播放器使用动态媒体流，没有直接列出视频资产。
- 连续导航 5 个视频的测试在 180 秒内未完成，最后停在第 4 个视频；**逐条打开详情页不满足每小时 100 条的目标**。

判定：淘汰“逐个打开视频页再取地址”的批量方案。

下一步必须改为：

1. 在收藏列表页只加载一次。
2. 捕获收藏列表接口响应。
3. 从单个或少量响应中批量提取 `aweme_id`、标题和音视频候选地址。
4. 优先下载音频；没有独立音频时才下载 MP4 并抽音轨。
5. 不读取、导出或保存 Cookie。

## 7. 关于“视频蒸馏 Skill”

蒸馏 Skill 可以加快“逐字稿 → 结构化知识/可执行 Skill”的后半段，但不能加快抖音媒体获取，也不能替代 ASR 推理。当前 `cangjie-skill` 适合在转写完成后用于高价值长内容；阶段一真正的速度提升来自 OpenVINO 和双通道路由。

## 8. 阶段判定

- ASR：**有条件通过**
  - base 负责速度
  - small 负责高风险英文专名
  - OCR/GitHub 校验负责最终拼写
- 批量采集：**未通过**
  - 必须先完成收藏列表接口的批量捕获
- 全量 466 条：**暂缓**
  - 等批量采集达到每小时 100 条以上后再开工

## 9. 固定来源

- whisper.cpp：[官方仓库](https://github.com/ggml-org/whisper.cpp)；[v1.9.1 发布包](https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.1)
- OpenVINO GenAI：[官方仓库](https://github.com/openvinotoolkit/openvino.genai)
- OpenVINO 模型：[whisper-small-int8-ov](https://huggingface.co/OpenVINO/whisper-small-int8-ov)；[whisper-base-int8-ov](https://huggingface.co/OpenVINO/whisper-base-int8-ov)

