# Agents

## 项目概述

基于 OpenCode + 国产大模型的 AI 知识库助手，自动从 GitHub Trending 和 Hacker News 采集 AI/LLM/Agent 领域的技术动态，经 AI 分析后结构化存储为 JSON，依托 OpenClaw 个人 AI 助手平台的 Gateway 实现 Telegram/飞书等多渠道分发，帮助开发者持续追踪前沿技术趋势。

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.12 |
| 运行时 | OpenCode |
| AI 模型 | 国产大模型（通过 OpenAI 兼容 API 调用） |
| 编排框架 | LangGraph |
| Agent 系统 | OpenClaw |
| 存储 | JSON 文件系统 |
| 分发渠道 | Telegram Bot / 飞书 Bot（通过 OpenClaw Gateway） |

## 编码规范

- 遵循 **PEP 8**，使用 `ruff` 格式化（line-length=80）
- 命名规则：`snake_case`（函数/变量）、`PascalCase`（类）、`UPPER_SNAKE_CASE`（常量）
- 所有公开函数写 **Google 风格 docstring**
- **禁止使用 `print()` 调试**，统一使用 `logging` 模块
- 每个函数不超过 50 行，每个文件不超过 400 行
- 禁止 `import *`、禁止硬编码密钥

## 项目结构

```
.opencode/
├── agents/          # LangGraph Agent 定义
│   ├── collector/   # 采集 Agent
│   ├── analyzer/    # 分析 Agent
│   └── organizer/   # 整理 Agent
└── skills/          # OpenCode Skill 定义
knowledge/
├── raw/             # 原始采集数据
└── articles/        # AI 整理后的知识条目
```

## 知识条目 JSON 格式

```json
{
  "id": "20250507-001",
  "title": "标题",
  "source_url": "https://github.com/...",
  "source_platform": "github_trending",
  "summary": "AI 生成的摘要，100-200 字",
  "highlights": ["第一个技术亮点","第二个技术亮点..."],
  "score":  8,
  "tags": ["LLM", "Agent", "RAG"],
  "status": "draft",
  "collected_at": "2025-05-07T10:00:00Z",
  "published_at": null
}
```

`status` 枚举：`draft` → `reviewed` → `published`

## Agent 角色概览

| 角色 | 职责 | 输入 | 输出 | 关键工具 |
|------|------|------|------|----------|
| **采集 (Collector)** | 定时从 GitHub Trending / HN 抓取 AI 相关内容 | 抓取配置 | `knowledge/raw/` JSON | HTTP 客户端 + 去重 |
| **分析 (Analyzer)** | AI 分析原始内容，生成摘要与深度分析 | `knowledge/raw/` | `knowledge/articles/` 结构化条目 | 国产大模型 + Prompt 模板 |
| **整理 (Organizer)** | 标签分类、质量评分、多渠道分发 | `knowledge/articles/` | 分发消息 | LangGraph 路由 + OpenClaw Gateway |

## 红线（绝对禁止）

- **禁止**在代码中硬编码任何 API Key、Token 或密码（必须通过环境变量 `KNOWLEDGE_*` 注入）
- **禁止**在无去重逻辑的情况下写入重复的知识条目
- **禁止**采集非公开内容或违反目标网站 robots.txt 的页面
- **禁止**在 Agent 之间直接共享可变状态（必须通过文件或数据库通信）
- **禁止**分发未经过 `reviewed` 状态的条目
