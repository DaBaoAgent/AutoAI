# 采用与评估的开源项目

## 已采用

- `yt-dlp/yt-dlp`：公开视频元数据与常规下载；抖音登录收藏只作后备，因为当前站点要求新鲜 Cookie。
- `SYSTRAN/faster-whisper`：CPU int8 本地语音识别。
- `qdrant/fastembed`：CPU 友好的本地多语言嵌入；向量直接存入 SQLite，避免本机策略拦截 gRPC DLL。
- `jlowin/fastmcp`：把检索、来源与知识图谱暴露给 Codex/其他 MCP 智能体。
- `networkx/networkx`：生成可迁移的 JSON/GraphML 知识图谱。
- `RapidAI/RapidOCR`：本地中英文画面识别，用于核对语音转写中不确定的 GitHub 仓库名和英文项目名。
- `kangarooking/cangjie-skill`：将高价值逐字稿进一步蒸馏为可执行 Skill。

## 评估后暂不作为主栈

- `HKUDS/LightRAG`：知识图谱与 WebUI 很完整，但官方建议抽取模型至少 32B、上下文至少 32K；本机 16GB 内存和 Intel 核显不适合作为首发主栈。保留为将来接入云模型或升级硬件后的迁移选项。
- `AI-Builder-Club/skills`：`new-loop` 的共享文件知识库思想值得借鉴，但当前技能明确以 Claude Code 和 `CLAUDE.md` 为前提，所以不直接注入 Codex。
- `mattpocock/skills`：工程技能质量高；当前任务只需要官方 `cli-creator`、`transcribe` 与自建 `dabo-local-kb`，避免一次安装过多技能造成触发冲突。
- `Johnserf-Seed/f2`：在隔离环境中测试了抖音解析能力；不读取或导出 Chrome 登录凭据时，当前详情接口没有返回可用数据，因此不接入主环境。主流程继续使用用户选定的 Chrome 页面状态与临时签名媒体地址。
