# 阶段一：本地视频蒸馏性能基准

本目录只做隔离评测，不修改 `data/index/knowledge.db`，也不会覆盖正式逐字稿。

## 目标

- 用 20 条、合计约 2.14 小时的真实抖音收藏音频评测本地 ASR。
- 覆盖短视频、普通教程、访谈、长教程和 GitHub 英文项目名。
- 比较现有 `faster-whisper small/int8`、`whisper.cpp small` 与 Intel OpenVINO 路线。
- 所有结果写入 `benchmarks/phase1/results/`，支持断点续跑。

## 基准口径

- `x_realtime = 音频时长 / 推理秒数`，数值越大越快。
- 引擎首次加载时间单列，不混入单条音频推理速度。
- 英文名称召回以 `samples.json` 中已由画面或元数据确认的词为准。
- 纯速度达标但英文项目名明显丢失的引擎，不会直接进入全量阶段。

## 运行

```powershell
.\.venv\Scripts\python.exe benchmarks\phase1\benchmark_asr.py `
  --engine faster-whisper --profile smoke
```

`quick` 只跑一条短视频，用于快速淘汰明显慢的设备；`smoke` 再跑短、中、长各一条；通过后使用 `--profile full` 跑满 20 条。

OpenVINO 使用独立环境，模型也固定到下载时的仓库修订：

```powershell
.\benchmarks\phase1\.venv-openvino\Scripts\python.exe `
  benchmarks\phase1\setup_openvino.py `
  --repo-id OpenVINO/whisper-base-int8-ov

.\benchmarks\phase1\.venv-openvino\Scripts\python.exe `
  benchmarks\phase1\benchmark_asr.py `
  --engine openvino --model base --profile smoke `
  --openvino-model benchmarks\phase1\models\OpenVINO-whisper-base-int8-ov `
  --openvino-device CPU
```

`whisper.cpp` 固定使用官方 `v1.9.1` Windows x64 发布包和 `ggml-small` 模型；源码快照位于本机隔离的 `vendors/` 目录。
