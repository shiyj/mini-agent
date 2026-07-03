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

`OllamaResponsesModel` 也可直接构造，参见 [custom_model.py](examples/custom_model.py)。

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
    target_file="output.py",
    max_steps=8,
)
print(result)
```

温度建议 `0.4~0.5`，挂载 `write_file` / `run_python_file` / `execute_bash_command` 三个工具。
完整的 ReAct 循环：Write → Run → Observe → Fix → Repeat，直到调用 `final_answer()`。

系统提示词仅 ~30 行（smolagents 默认 174 行），专为小模型优化。

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
│   └── prompts.json             # 提示词模板（prefill 配置等）
├── doc/
│   └── implementation.md        # 实现细节文档
├── examples/
│   ├── basic_usage.py           # 最简使用示例
│   ├── fast_vs_deep.py          # 极速 vs 深思 对比
│   └── custom_model.py          # 手动构造 OllamaResponsesModel
├── src/
│   └── mini_agent/
│       ├── __init__.py          # 公共 API 导出
│       ├── agent.py             # 两种 Agent 模式 + 工具函数
│       ├── code_executor.py     # exec() 代码执行引擎（替代 smolagents 沙盒）
│       ├── config.py            # config/prompts.json 加载器
│       ├── model.py             # OllamaResponsesModel（httpx + SSE）
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
- `httpx` >= 0.27.0（仅有的外部依赖）
- 本地 Ollama 服务 + 可用模型

---

## 设计哲学

- **模型由调用者创建并传入。** Agent 不接触模型配置——你决定用哪个模型、什么温度。
- **从小模型出发。** 提示词精简到极致（极速流 12 词，深思流 ~30 行），避免小模型注意力崩溃。
- **没有 AST 沙盒。** 用 `exec()` 直接执行代码，安全通过工具自身实现（`run_python_file` 走子进程）。
- **导入时间 0.71s。** 对比 smolagents 方案 2.8s，减少 75%。

详细实现说明见 [doc/implementation.md](doc/implementation.md)。
