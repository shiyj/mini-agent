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

### `run_deep_agent(prompt, model, max_steps=8, **kwargs) -> str`

**完整 ReAct 循环**的便利入口。文件路径由模型在代码块（`file:path` / `edit:path`）中自行写出，任务描述里写明要操作的文件即可。

```python
result = run_deep_agent(
    prompt="examples/data/modify_python.py cannot run, find the problem and fix it.",
    model=model,
    max_steps=10,
)
print(result)
```

**内部：**
```python
def run_deep_agent(prompt, model, max_steps=8, **kwargs):
    model.max_output_tokens = max(model.max_output_tokens, 4096)
    tools = [run_python_file, read_file]

    task = prompt + "\n\nIMPORTANT: Follow these steps exactly:\n" \
        "1. Write the corrected file using a ```file:...``` block\n" \
        "2. Run it with run_python_file() in a ```python``` block\n" \
        "3. If errors, fix and write again, then run again\n" \
        "4. When it works, call final_answer(result)"

    return react_loop(
        system_prompt=build_deep_system_prompt(tools),
        task=task,
        model=model,
        tools=tools,
        max_steps=max_steps,
        **kwargs,
    )
```

---

### `run_deep_agent_with_config(...)` — 配置驱动

```python
result = run_deep_agent_with_config(
    prompt="examples/data/out.py cannot run, find the problem and fix it.",
    model_name="ornith:9b",
    provider_name="ollama-local",  # 可选，从 agents.json 推断
    max_steps=10,
    temperature=0.5,
)
```

**内部：**
```python
def run_deep_agent_with_config(
    prompt,
    model_name,
    provider_name=None,
    max_steps=8,
    **model_overrides,
):
    model = load_agent_config(model_name, provider_name, **model_overrides)
    return run_deep_agent(prompt, model, max_steps)
```

---

## 四个内置工具

| 工具 | 签名 | 说明 |
|------|------|------|
| `read_file` | `read_file(filename, raw=False, offset=0, limit=0) -> str` | 默认每行带 `N#HHHH` 哈希标签；`raw=True` 返回纯文本；`offset`/`limit` 读取范围 |
| `run_python_file` | `run_python_file(filename) -> str` | `subprocess.run(["python3", filename], timeout=30)` 捕获 stdout/stderr |

**精准编辑**通过 ````edit:path``` Markdown 块实现（非 Python 工具），见 `doc/tools.md`。

**安全边界：** `read_file` 本地 I/O 无需隔离；运行脚本、Shell 命令走子进程，30s 超时。

---

## 扩展示例

### 自定义工具

```python
from mini_agent import tool, run_deep_agent, get_local_model

@tool
def fetch_url(url: str) -> str:
    """Fetch a URL and return its text content."""
    import requests
    return requests.get(url, timeout=10).text

model = get_local_model("ornith:9b")
result = run_deep_agent(
    "Download https://example.com and save to page.html",
    model,
    tools=[run_python_file, fetch_url],
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