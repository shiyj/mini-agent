# agent.py — 组合层与公共 API

## 概述

组合层：组装工具、模型、ReAct 循环，暴露两种模式入口。

---

## 公共 API

```python
from mini_agent import (
    # 模型工厂
    get_local_model,
    load_agent_config,
    ResponsesModel,

    # 两种模式
    run_fast_agent,
    run_deep_agent,
    run_deep_agent_with_config,

    # 工具（如需自定义）
    read_file,
    run_python_file,

    # 配置
    load_agent_config,
)
```

---

## 模型工厂

### `get_local_model(model_name, api_base, temperature, **kwargs) -> ResponsesModel`

便利函数，直接构造 `ResponsesModel`。

```python
model = get_local_model(
    model_name="ornith:9b",
    api_base="http://localhost:11434",
    temperature=0.4,
)
```

---

## 极速流

### `run_fast_agent(prompt, model, prefill=None) -> str`

**单次模型调用**，无工具、无循环、无执行。

```python
result = run_fast_agent(
    prompt="Write a function to compute Fibonacci numbers",
    model=model,
    prefill=True,  # 默认 True，追加 "```python\n" 跳过 Qwen thinking
)
print(result)  # 原始模型输出
```

**内部：**
1. 使用 `FAST_AGENT_SYSTEM`（12 词）作为系统提示词
2. 从 `prompts.py` 读取 `FAST_AGENT_PREFILL` / `FAST_AGENT_PREFILL_TEXT` 常量
3. 构建 messages：`[system, user, assistant(prefill)]`
4. `model.generate(messages)` → 返回内容

**Prefill 机制：**
- Qwen 系模型有 Ollama 原生 thinking 模式
- 追加 `{"role": "assistant", "content": "```python\n"}` → 模型认为已开始写代码，跳过 thinking
- 关闭：`prefill=False`

---

## 深思流

### `run_deep_agent(prompt, model, max_steps=8, verbose=True, **kwargs) -> str`

**完整 ReAct 循环**的便利入口。文件路径由模型在 `````file:path``` 代码块中自行写出，任务描述里写明要操作的文件即可。

```python
result = run_deep_agent(
    prompt="examples/data/modify_python.py cannot run, find the problem and fix it.",
    model=model,
    max_steps=10,
    verbose=True,
)
print(result)
```

**内部：**
```python
def run_deep_agent(prompt, model, max_steps=8, max_observation_length=2000,
                   compressor=None, verbose=True):
    tools = [run_python_file, read_file]
    system_prompt = build_deep_system_prompt(tools)
    return react_loop(
        system_prompt=system_prompt,
        task=prompt,          # 直接使用 prompt，不再追加固定步骤说明
        model=model,
        tools=tools,
        max_steps=max_steps,
        verbose=verbose,
    )
```

---

### `run_deep_agent_with_config(...)` — 配置驱动

```python
result = run_deep_agent_with_config(
    prompt="examples/data/out.py cannot run, find the problem and fix it.",
    model_name="ornith:9b",
    provider_name="ollama-local",  # 可选，从 agents.json 推断
    log_file="logs/run.jsonl",     # 可选，JSONL 请求/响应日志
)
```

**内部：**
```python
def run_deep_agent_with_config(prompt, model_name, provider_name=None,
                               log_file=None, reasoning=None):
    config = load_agent_config(model_name, provider_name)  # 读 config/agents.json
    model = get_local_model(model_name, api_base=config["baseURL"], ...)
    compressor = SummaryCompressor(**config.get("compressor", {}))
    return run_deep_agent(
        prompt, model,
        max_steps=config.get("max_steps", 8),
        max_observation_length=config.get("max_observation_length", 2000),
        compressor=compressor,
    )
```

---

## 内置工具

| 工具 | 签名 | 说明 |
|------|------|------|
| `read_file` | `read_file(filename, offset=0, limit=0) -> str` | 返回文件**纯文本**（无哈希标签）；`offset`/`limit` 读取范围 |
| `run_python_file` | `run_python_file(filename) -> str` | `subprocess.run(["python3", filename], timeout=30)` 捕获 stdout/stderr |

**编辑通过 ````file:path``` Markdown 块**整文件重写实现（非 Python 工具），见 `doc/tools.md`。

**安全边界：** `read_file` 本地 I/O 无需隔离；运行脚本走子进程，30s 超时。

---

## 扩展示例

### 自定义工具

`run_deep_agent` 使用固定的内置工具集；要加自定义工具，用 `react_loop` 直接组装（传入你的工具列表）：

```python
from mini_agent import tool, get_local_model, read_file, run_python_file
from mini_agent.react_agent import react_loop
from mini_agent.prompts import build_deep_system_prompt

@tool
def fetch_url(url: str) -> str:
    """Fetch a URL and return its text content."""
    import requests
    return requests.get(url, timeout=10).text

tools = [read_file, run_python_file, fetch_url]
model = get_local_model("ornith:9b")
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