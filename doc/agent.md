# agent.py — 组合层与公共 API

## 概述

组合层：组装工具、模型、ReAct 循环，暴露两种模式入口。

---

## 公共 API

```python
from mini_agent import (
    # 模型加载（唯一入口）
    load_model,
    ResponsesModel,

    # 两种模式
    run_fast_agent,
    run_deep_agent,

    # 内置工具
    read_file,
    run_python_file,

    # 类型/基础设施
    ResponsesModel,
    ContextCompressor,
    SummaryCompressor,
    Tool,
    tool,
    ChatMessage,
    MessageRole,
    TokenUsage,
)
```

---

## 模型加载

### `load_model(model_name, provider_name, **kwargs) -> (ResponsesModel, SummaryCompressor)`

**唯一推荐的模型加载方式**。从 `config/agents.json` 读取配置并自动构造 `ResponsesModel`。

```python
from mini_agent import load_model, run_fast_agent, run_deep_agent

# 最简单：provider + model 名
model, compressor = load_model("ornith:9b", "ollama-local")

# 带日志
model, _ = load_model("ornith:9b", "ollama-local", log_file="run.jsonl")

# 自定义 reasoning（如 o1 系列）
model, _ = load_model("o1-mini", "openai", reasoning={"effort": "high"})
```

**参数：**
- `model_name`：模型标识（如 `"ornith:9b"`, `"gpt-4o"`）
- `provider_name`：Provider 名称（如 `"ollama-local"`），可选——不传时自动查找
- `log_file`：可选 JSONL 日志路径
- `reasoning`：可选 reasoning 配置（如 o1 系列 `{"effort": "high"}`）

**返回：** `(ResponsesModel, SummaryCompressor)` —— compressor 为每次调用的新实例。

---

## 手动构造模型

如需完全自定义参数（temperature、api_base、context_window 等），直接构造 `ResponsesModel`：

```python
from mini_agent import ResponsesModel

model = ResponsesModel(
    model_name="ornith:9b",
    api_base="http://localhost:11434",
    temperature=0.8,
    max_output_tokens=8192,
    context_window=131072,
)
```

---

## 极速流

### `run_fast_agent(prompt, model, prefill=True) -> str`

**单次模型调用**，无工具、无循环、无执行。

```python
from mini_agent import load_model, run_fast_agent

model, _ = load_model("ornith:9b", "ollama-local")
result = run_fast_agent(
    prompt="Write a function to compute Fibonacci numbers",
    model=model,
    prefill=True,  # 默认 True，追加 "```python\n" 跳过 Qwen thinking
)
print(result)  # 原始模型输出
```

**Prefill 机制：**
- Qwen 系模型有 Ollama 原生 thinking 模式
- 追加 `{"role": "assistant", "content": "```python\n"}` → 模型认为已开始写代码，跳过 thinking
- 关闭：`prefill=False`

---

## 深思流

### `run_deep_agent(prompt, model, max_steps=8, max_observation_length=2000, verbose=True) -> str`

**完整 ReAct 循环**的便利入口。文件路径由模型在 ````file:path``` 代码块中自行写出，任务描述里写明要操作的文件即可。

```python
from mini_agent import load_model, run_deep_agent

model, _ = load_model("ornith:9b", "ollama-local")
result = run_deep_agent(
    prompt="examples/data/modify_python.py cannot run, find the problem and fix it.",
    model=model,
    max_steps=10,
    verbose=True,
)
print(result)
```

**内部使用默认 SummaryCompressor（每次运行新实例）：**
```python
def run_deep_agent(prompt, model, max_steps=8, max_observation_length=2000,
                   verbose=True):
    from .context import SummaryCompressor
    tools = [run_python_file, read_file]
    system_prompt = build_deep_system_prompt(tools)
    return react_loop(
        system_prompt=system_prompt,
        task=prompt,
        model=model,
        tools=tools,
        max_steps=max_steps,
        max_observation_length=max_observation_length,
        verbose=verbose,
        compressor=SummaryCompressor(),  # 每次运行新实例
    )
```

> **注意**：`run_deep_agent_with_config` 已移除。直接用 `load_model()` + `run_deep_agent()` 即可。

---

## 内置工具

| 工具 | 签名 | 说明 |
|------|------|------|
| `read_file` | `read_file(filename, offset=0, limit=0) -> str` | 返回文件**纯文本**（无哈希标签）；`offset`/`limit` 读取范围 |
| `run_python_file` | `run_python_file(filename) -> str` | `subprocess.run([sys.executable, filename], timeout=30)` 捕获 stdout/stderr |

**编辑通过 ````file:path``` Markdown 块**整文件重写实现（非 Python 工具），见 `doc/tools.md`。

**安全边界：** `read_file` 本地 I/O 无需隔离；运行脚本走子进程，30s 超时。

---

## 扩展示例

### 自定义工具

`run_deep_agent` 使用固定的内置工具集；要加自定义工具，用 `react_loop` 直接组装（传入你的工具列表）：

```python
from mini_agent import tool, read_file, run_python_file
from mini_agent.react_agent import react_loop
from mini_agent.prompts import build_deep_system_prompt
from mini_agent import ResponsesModel

@tool
def fetch_url(url: str) -> str:
    """Fetch a URL and return its text content."""
    import requests
    return requests.get(url, timeout=10).text

tools = [read_file, run_python_file, fetch_url]
model = ResponsesModel(model_name="ornith:9b", api_base="http://localhost:11434")
result = react_loop(
    system_prompt=build_deep_system_prompt(tools),
    task="Download https://example.com and save to page.html",
    model=model,
    tools=tools,
    max_steps=8,
)
```

### 切换模型提供商

```python
from mini_agent import ResponsesModel

# 兼容 OpenAI Responses API 的任何服务
model = ResponsesModel(
    model_name="gpt-4o",
    api_base="https://api.openai.com/v1",
    temperature=0.3,
)
```