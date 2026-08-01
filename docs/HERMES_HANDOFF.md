# Hermes 任务交接

暂停时间：2026-07-31 08:49（Asia/Shanghai）

## 当前状态

- 用户本轮要求：从当时的断点继续 50 个。
- 本轮起点：已转写 61/501，剩余 440。
- 暂停时：已转写 65/501，剩余 436。
- 本轮已完成：4 个；Hermes 还需完成：46 个。
- 验收目标：已转写 111/501，剩余 390。
- 已下载音频：78；其中 13 条尚待转写。
- 当前没有知识库批处理或 OpenVINO 转写进程运行。
- 暂停发生时正在准备下一条，遗留
  `data/tmp/7659323081614592227.openvino.wav`。它是可丢弃中间文件，
  续跑时会被覆盖。

## Hermes 续跑命令

在 `D:\@kaifa\shoucang` 执行：

```powershell
.\.venv\Scripts\dabo-kb.exe status
.\.venv\Scripts\dabo-kb.exe process-all --count 46 --batch-size 10 --workers 4
.\.venv\Scripts\dabo-kb.exe status
.\.venv\Scripts\dabo-kb.exe ocr-needed --limit 50
```

`process-all` 完成指定数量后会自动增量更新语义向量并重建知识图谱。
如果连续三批没有新增逐字稿，它会停止并在
`data/sources/douyin_process_all_checkpoint.json` 记录失败摘要。

## 验收

- `ai_transcribed` 应达到 111。
- `ai_remaining` 应降到 390。
- 检查 `logs/process-50.err.log` 和续跑时的新错误日志。
- 英文项目名仅在原视频画面明确确认后写入知识图谱。
- 长期只保留 M4A；MP4 和 OpenVINO WAV 都是临时文件。
- 不读取、复制或保存 Chrome Cookie、Local Storage 或密码。
