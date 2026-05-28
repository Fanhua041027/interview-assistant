# 笔试面试助手

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![PyQt5](https://img.shields.io/badge/GUI-PyQt5-green)](https://pypi.org/project/PyQt5/)
[![DeepSeek](https://img.shields.io/badge/LLM-DeepSeek%20Chat-red)](https://deepseek.com)

**AI笔试面试辅助助手** 是一个 Windows 透明悬浮窗辅助工具，基于本地知识库检索 + DeepSeek API 实时生成回答。适用于线上笔试/技术面试场景，提供即时、简洁的答案建议。

---

## 功能特点

- **透明悬浮窗** — 始终置顶的半透明窗口，不遮挡面试界面
- **知识库检索** — 基于 TF-IDF 的本地知识库搜索（支持 Markdown 文档）
- **流式输出** — 回答逐字出现，实时可见
- **全局热键** — Alt+Q 聚焦输入框，无需切换窗口
- **多轮对话** — 支持上下文延续
- **一键复制** — 回答自动复制到剪贴板
- **Ghost Mode 隐身防御** — 防截屏、隐藏任务栏，保护隐私

- **Ghost Mode 隐身防御** — 防截屏（SetWindowDisplayAffinity）、隐藏任务栏，保护面试隐私

## Ghost Mode 隐身防御

Interview Helper 内置 Ghost Mode（隐身防御模式），启动时自动激活：

| 特性 | 实现 |
|------|------|
| **防截屏** | `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` — 阻止截图/录屏捕获窗口内容 |
| **隐藏任务栏** | `WS_EX_TOOLWINDOW` — 窗口不出现在任务栏 |
| **无边框** | `Qt.FramelessWindowHint` — 不可见的透明悬浮窗 |

启动日志输出 `[GhostMode] 隐身防御模式已激活`。

## 效果预览

```
┌──────────────────────────────────────────┐
│  Interview Helper 已启动                  │
│  API: ✓  |  知识库: 88 篇                │
│                                          │
│  Alt+Q        切换输入框                   │
│  Ctrl+Shift+H  显示/隐藏                  │
│  Alt+Shift+C  清除对话                    │
│  Ctrl+Shift+Q  退出                       │
│                                          │
│  ──────────────────────────────────────── │
│  Q: 什么是 RAG？                          │
│  A: RAG (Retrieval-Augmented             │
│  Generation) 是一种结合检索和生成的        │
│  NLP 方法...  [kb_source]                │
│                                          │
│  ┌─────────────────────────────────┐     │
│  │ 输入问题… (Enter)  KB: 88篇    │     │
│  └─────────────────────────────────┘     │
└──────────────────────────────────────────┘
```

## 快速开始

### 前置条件

- Python 3.11+
- [DeepSeek API Key](https://platform.deepseek.com/)

### 安装

```bash
# 克隆仓库
git clone https://github.com/Fanhua041027/interview-assistant.git
cd interview-assistant

# 安装依赖
pip install -r requirements.txt
```

### 配置

编辑 `config.json`：

```json
{
  "api_key": "sk-your-deepseek-api-key",
  "kb_path": "C:\\path\\to\\knowledge-base",
  "opacity": 0.05,
  "font_size": 13
}
```

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `api_key` | DeepSeek API 密钥 | `""` |
| `api_url` | API 端点 | `https://api.deepseek.com/v1/chat/completions` |
| `kb_path` | 知识库文件夹路径（含 `.md` 文件） | `""` |
| `opacity` | 窗口背景透明度 (0.0-1.0) | `0.05` |
| `streaming_enabled` | 是否启用流式输出 | `true` |
| `temperature` | 生成温度 (0.0-1.0) | `0.4` |
| `max_tokens` | 最大生成长度 | `1000` |
| `font_family` | 字体 | `Microsoft YaHei` |
| `font_size` | 字号 | `13` |
| `auto_clear_timeout_ms` | 自动清除超时（毫秒） | `90000` |

### 运行

```bash
python main.py
```

### 构建可执行文件

```bash
build.bat
```

输出在 `dist/InterviewHelper.exe`。

## 热键

| 热键 | 功能 |
|------|------|
| **Alt+Q** | 聚焦到输入框 |
| **Ctrl+Shift+H** | 显示/隐藏窗口 |
| **Alt+Shift+C** | 清除对话历史 |
| **Ctrl+Shift+Q** | 退出程序 |

## 项目结构

```
interview-assistant/
├── main.py                 # 主程序入口（含 Ghost Mode）
├── workers.py              # 后台线程工作器
├── build.bat               # PyInstaller 构建脚本
├── config.json             # 配置文件（本地，含 API Key）
├── config.example.json     # 配置示例
├── requirements.txt        # Python 依赖
├── README.md               # 本文件
└── LICENSE                 # 许可证
```

## 技术架构

```
┌─────────────┐    ┌───────────────┐    ┌─────────────┐
│  QLineEdit   │    │  QTextBrowser  │    │  Knowledge  │
│  (输入框)    │    │  (显示区域)    │    │  Base       │
└──────┬──────┘    └───────┬───────┘    └──────┬──────┘
       │                   │                    │
       └──────────┬────────┘                    │
                  │                             │
         ┌────────▼────────┐                   │
         │  OverlayWindow  │                   │
         │  (Qt主线程)      │                   │
         └────────┬────────┘                   │
                  │ pyqtSignal                  │
         ┌────────▼────────┐                   │
         │  StreamWorker   │◄──────────────────┘
         │  (后台线程)      │
         └────────┬────────┘
                  │ HTTP/SSE
         ┌────────▼────────┐
         │  DeepSeek API   │
         └─────────────────┘
```

## 知识库

项目核心功能依赖于本地知识库。知识库应为 Markdown 文件（`.md`）集合，支持按标题（`##` / `###`）分块索引。每块内容通过 TF-IDF 向量化，搜索时返回最相关的片段作为 LLM 上下文。

示例知识库结构：
```
knowledge-base/
├── 01-ai-agent-fundamentals.md
├── 02-machine-learning.md
├── 03-deep-learning.md
├── 04-nlp.md
├── 05-python.md
└── ...
```

## 许可证

本项目仅供个人学习使用。请遵守 [DeepSeek 使用条款](https://platform.deepseek.com/terms)。
