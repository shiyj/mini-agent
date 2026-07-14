# mini-agent

基于本地 Ollama 模型的轻量级编程 Agent，提供**极速流**与**深思流**两种模式。

零外部 Agent 依赖——不依赖 smolagents、LangChain 等框架，仅需 `httpx` + Python 标准库。
针对 9B 级小模型（如 ornith:9b / Qwen2.5-Coder）的注意力特性专门优化。

---

## 快速开始

### 前置条件

- Python >= 3.10
- 本地 [Ollama](https://ollama.com) 服务运行中
- 可用模型（如 `ornith:9b`）

### 安装

```bash
cd mini_agent
uv sync
```

---

## 使用

### 0. 创建模型

```python
from mini_agent import get_local_model

model = get_local_model(
    model_name="ornith:9b",
    api_base="http://YOUR_OLLAMA_HOST:11434",
    temperature=0.0,
)
```

`ResponsesModel` 也可直接构造，参见 [custom_model.py](examples/custom_model.py)。

### 1. 🏎️ 极速流 — 单次代码生成

```python
from mini_agent import run_fast_agent

result = run_fast_agent("Write a function to compute Fibonacci numbers", model=model)
print(result)
```

温度建议 `0.0`，无外部工具，纯单步生成。模型直接输出代码，不做执行验证。

#### Prefill 模式（默认开启）

Qwen 系模型（如 ornith:9b）有 Ollama 原生的「思考模式」：
模型收到代码生成请求时，会在 `thinking` 字段中反复试错多个方案，
消耗数千 token 后才输出 `content`，甚至完全不输出。

极速流默认启用 **assistant prefill**：在消息末尾追加一条
`{"role": "assistant", "content": "```python\n"}`，模型认为代码块已开始，
直接续写代码，跳过思考阶段。

| | 无 prefill | 有 prefill（默认） |
|---|---|---|
| 成功率 | 6/7（Fibonacci 等任务失败） | 7/7 |
| Fibonacci 耗时 | ❌ 10-80s / 500-4000 tokens | ✅ 1-2s / 48 tokens |
| 平均 token 数 | ~315 | ~136 |

关闭 prefill：`run_fast_agent(prompt, model, prefill=False)`，
或修改 `config/prompts.json` 中 `"prefill": false`。

### 2. 🧠 深思流 — 自动纠错循环

```python
from mini_agent import run_deep_agent

result = run_deep_agent(
    "Write a script that downloads a webpage and extracts all links",
    model=model,
    max_steps=8,
)
print(result)
```

温度建议 `0.4~0.5`，挂载 `read_file` / `run_python_file` / `execute_bash_command` 三个工具。
完整的 ReAct 循环：Write → Run → Observe → Fix → Repeat，直到调用 `final_answer()`。

系统提示词仅 ~30 行（smolagents 默认 174 行），专为小模型优化。

**核心工作流（Markdown 块驱动）：**
```
```file:target.py
# 原始内容，零转义
def main():
    ...
```

```python
run_python_file('target.py')
final_answer("Fixed")
```
```
- 所有 ````file:``` / ````python:``` / ````bash:``` 块**先写入磁盘**
- 仅**第一个** ````python``` / ````bash``` 块在 `exec()` 中执行（注入工具、final_answer）
- 零转义写文件，彻底解决 9B 模型字符串嵌套引号/转义失败

---

## 验证

```bash
uv run python3 verify/verify.py
```

三步验证：
1. **Prompt 加载** — 确认 `config/prompts.json` 正确读取
2. **模型连通性** — 调用 Ollama 模型返回简单回复
3. **Deep Agent 循环优化** — Agent 编写质数筛法对比脚本并自动纠错（最多重试 10 次）

---

## 项目结构

```
mini_agent/
├── config/
│   ├── prompts.json             # 极速流 prefill 配置
│   └── agents.json              # Provider + Model 配置
├── doc/                         # 按模块组织的文档
│   ├── README.md                # 文档索引
│   ├── types.md                 # types.py — 数据类型、消息处理
│   ├── tools.md                 # tools.py — Tool 抽象、@tool、内置工具
│   ├── code_executor.md         # code_executor.py — exec() 执行引擎
│   ├── react_agent.md           # react_agent.py — ReAct 循环、解析、写入、错误恢复
│   ├── prompts.md               # prompts.py — 系统提示词、错误码 E001–E008
│   ├── model.md                 # model.py — ResponsesModel、SSE、消息处理
│   ├── config.md                # config.py — 配置加载
│   ├── agent.md                 # agent.py — 组合层、公共 API、两种模式
│   └── architecture.md          # 整体架构、数据流、安全模型、与 smolagents 对比
├── examples/
│   ├── basic_usage.py           # 最简使用示例
│   ├── fast_vs_deep.py          # 极速 vs 深思 对比
│   └── custom_model.py          # 手动构造 ResponsesModel
├── src/
│   └── mini_agent/
│       ├── __init__.py          # 公共 API 导出
│       ├── agent.py             # 两种 Agent 模式 + 工具函数
│       ├── code_executor.py     # exec() 代码执行引擎
│       ├── config.py            # 配置加载器
│       ├── model.py             # ResponsesModel（httpx + SSE）
│       ├── prompts.py           # 小模型优化系统提示词
│       ├── react_agent.py       # ReAct 循环核心
│       ├── tools.py             # Tool 抽象 + @tool 装饰器
│       └── types.py             # ChatMessage / TokenUsage 等数据类型
├── verify/
│   └── verify.py                # 验证脚本
├── pyproject.toml               # 依赖仅 httpx
└── README.md
```

---

## 依赖

- Python >= 3.10
- `httpx` >= 0.27.0（**唯一**外部依赖）
- 本地 Ollama 服务 + 可用模型

---

## 设计哲学

- **模型由调用者创建并传入。** Agent 不接触模型配置——你决定用哪个模型、什么温度。
- **从小模型出发。** 提示词精简到极致（极速流 12 词，深思流 ~30 行），避免小模型注意力崩溃。
- **没有 AST 沙盒。** 用 `exec()` 直接执行代码，安全通过工具自身实现（`run_python_file` 走子进程）。
- **导入时间 0.71s。** 对比 smolagents 方案 2.8s，减少 75%。

---

## 文档导航

| 模块 | 文档 | 核心内容 |
|------|------|----------|
| `types.py` | [types.md](doc/types.md) | 数据类型、消息归一化、停止序列截断 |
| `tools.py` | [tools.md](doc/tools.md) | Tool 抽象、@tool 装饰器、三个内置工具 |
| `code_executor.py` | [code_executor.md](doc/code_executor.md) | exec() 执行引擎、工具注入、final_answer |
| `react_agent.py` | [react_agent.md](doc/react_agent.md) | ReAct 循环、Markdown 块解析、文件写入、错误恢复 |
| `prompts.py` | [prompts.md](doc/prompts.md) | 系统提示词、错误码 E001–E008 |
| `model.py` | [model.md](doc/model.md) | ResponsesModel、SSE、空白重试 |
| `config.py` | [config.md](doc/config.md) | prompts.json / agents.json 加载 |
| `agent.py` | [agent.md](doc/agent.md) | 组合层、run_fast_agent / run_deep_agent |
| 整体 | [architecture.md](doc/architecture.md) | 数据流图、模块依赖、安全模型、与 smolagents 对比 |