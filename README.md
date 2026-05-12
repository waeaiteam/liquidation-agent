# Liquidation Contrarian Agent

清算反向短线交易策略 Agent — 桌面端 + Web 界面

## 简介

基于大额清算事件驱动的反向短线交易系统。当市场发生大额清算时，系统自动检测清算密集区，结合清算热力图、OI、资金费率等多维数据，生成反向交易信号，支持 Paper 模拟交易和 LLM 二次审查。

## 功能

- **清算信号引擎** — 实时检测大额清算事件，生成 LONG/SHORT 反向信号
- **清算地图** — 定时采集 CoinAnk 清算热力图，可视化价格 × 清算密度
- **多 LLM 支持** — 18 个 Provider（Anthropic、OpenAI、xAI、DeepSeek、OpenRouter、302.ai 等）
- **Grok 分析师** — 直接调用 xAI API，实时搜索 X 情绪数据，结构化输出情绪面板
- **策略对话** — 基于完整 Agent 包（技能/记忆/规则）的策略问答
- **推文流水线** — 综合市场数据 + X 情绪 + 策略信号，一键生成候选推文并发布
- **Paper 模拟交易** — 完整的模拟下单、止盈止损、时间止损、连续亏损暂停
- **自我进化** — 基于 Paper 订单历史统计，自动建议参数优化
- **桌面端** — Electron 打包，Windows 原生应用，系统托盘、开机自启

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12 + Flask + Flask-CORS |
| 前端 | 原生 HTML/CSS/JS + ECharts |
| 桌面端 | Electron 31 |
| 数据源 | CoinAnk (Claw402 协议，按量付费) |
| AI | Anthropic SDK + OpenAI 兼容协议 + xAI httpx |
| 发推 | Tweepy (OAuth 1.0a) |

## 快速开始

### 开发模式

```bash
cd liquidation_agent
pip install -r requirements.txt
python app.py
# 访问 http://localhost:47891
```

### 桌面端开发

```bash
cd liquidation_agent_electron
npm install
npm run dev
```

### 打包

```bash
# 1. 打包 Python 后端
cd liquidation_agent
pyinstaller liquidation_agent.spec --noconfirm --clean

# 2. 打包 Electron 安装程序
cd liquidation_agent_electron
npm run dist
```

## 配置

首次使用在设置页面配置：

| 配置项 | 说明 | 费用 |
|--------|------|------|
| 钱包私钥 | Base 链钱包，用于 Claw402 协议支付 | 0.001 USDC/次清算数据 |
| LLM API Key | 主模型（数据解读/策略对话） | 按 token 计费 |
| xAI API Key | Grok 分析师 + X 情绪分析 | ~$0.02/次 |
| X OAuth 1.0a | 发推功能（可选） | 需 Basic 订阅 |

支持中转服务：OpenRouter、302.ai、SiliconFlow 等 OpenAI 兼容 API。

## 自动化配置

在设置 → 自动化配置中可调整：

- **市场数据轮询**：交易所 API 免费，默认 60s
- **清算地图采集**：0.001 USDC/次，默认 600s
- **X 情绪刷新**：~$0.02/次，默认 900s（可选）
- **LLM 交易审查**：信号触发时，按 token 计费（可选）

## Agent 包

### 策略 Agent (`AGENTS.md`)

完整的清算反向交易策略 Agent，包含：
- 技能包：市场解读、信号审查、风控计算、参数优化、状态解读
- 记忆规则：会话内记忆用户偏好
- 行为规则：先结论后依据，禁止承诺收益
- 硬风控约束：止损、杠杆、仓位、日亏损限制

### X 分析师 Agent (`agents/x_analyst.md`)

X 情报分析师，包含：
- 技能包：实时搜索、情绪量化、热度排名、KOL 追踪、叙事识别、推文起草
- 结构化输出：每次回答自动附加 JSON 数据块（情绪分数、热度前 10、KOL 观点、可行动信号）
- 前端解析后显示「查看数据面板」按钮

## 目录结构

```
liquidation_agent/
├── app.py                    # Flask 主应用，所有 API 路由
├── providers.py              # LLM Provider 配置（18 个）
├── state.py                  # Agent 状态管理（持久化到 JSONL）
├── AGENTS.md                 # 策略 Agent 系统提示
├── agents/
│   └── x_analyst.md          # X 分析师 Agent 系统提示
├── services/
│   ├── llm.py                # LLM 调用（Anthropic + OpenAI 兼容）
│   ├── agent_chat.py         # 策略对话服务
│   ├── xai_chat.py           # Grok 对话服务（直接调用 xAI API）
│   ├── x_sentiment.py        # X 情绪分析（Grok Live Search）
│   ├── x_poster.py           # 发推服务（Tweepy OAuth 1.0a）
│   ├── x_pipeline.py         # 推文流水线（综合数据生成候选推文）
│   ├── coinank.py            # CoinAnk 数据获取
│   ├── heatmap_manager.py    # 清算地图快照管理
│   ├── strategy_agent.py     # LLM 交易审查
│   └── evolution.py          # 自我进化引擎
├── strategy/
│   ├── models.py             # 数据模型（StrategyConfig、Signal、Order 等）
│   └── signals.py            # 信号生成引擎
├── trading/
│   ├── execution.py          # 执行适配器（Paper + Binance）
│   └── risk.py               # 风控管理器
└── templates/
    ├── index.html            # 单页应用
    └── app.js                # 前端逻辑

liquidation_agent_electron/
├── src/
│   ├── main.js               # Electron 主进程
│   ├── python-manager.js     # Python 后端进程管理
│   ├── preload-inject.js     # 桌面 API 注入
│   └── tray.js               # 系统托盘
└── electron-builder.yml      # 打包配置
```

## 风险提示

- 本工具仅供研究和学习使用，不构成投资建议
- 加密货币交易具有高风险，请充分了解风险后使用
- V0.0.1 仅支持 Paper 模拟交易，Live 实盘功能未开放
- 所有交易决策最终由用户负责

## License

UNLICENSED — 私有项目
