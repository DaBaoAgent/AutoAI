# Hermes 集成

AutoAI 项目自带 Hermes Agent 技能文件。

## 安装技能

```bash
# 安装到 Hermes
hermes skills install DaBaoAgent/AutoAI --name autoai
```

或手动复制：

```bash
cp SKILL.md ~/.hermes/skills/data-science/autoai/SKILL.md
```

## 技能功能

加载 `autoai` 技能后，Hermes Agent 可以：

- 自动运行 `process-all` 处理抖音收藏
- 诊断故障并自适应重试
- 执行增量同步
- 检索知识图谱回答问题
- 生成AI趋势日报

## 定时任务

项目建议搭配以下 Hermes cron 任务：

| 任务 | 频率 | 说明 |
|------|------|------|
| `autoai-增量同步` | 每6h | Playwright自动同步新收藏 |
| `AI趋势日报` | 每天9:00 | 多平台AI趋势聚合+邮件推送 |
