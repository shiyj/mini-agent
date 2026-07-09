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
    write_file,
    read_file,
    run_python_file,
    execute_bash_command,
    
    # 配置
    load_prompts,
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
1. 读取 `config/prompts.json` → `fast_agent.system_instruction`
2. 构建 messages：`[system, user, assistant(prefill)]`
3. `model.generate(messages)` → 返回内容

**Prefill 机制：**
- Qwen 系模型有 Ollama 原生 thinking 模式
- 追加 `{"role": "assistant", "content": "```python\n"}` → 模型认为已开始写代码，跳过 thinking
- 关闭：`prefill=False` 或 `config/prompts.json` 中 `"prefill": false`

---

## 深思流

### `run_deep_agent(prompt, model, target_file, max_steps=8, **kwargs) -> str`

**完整 ReAct 循环**的便利入口。

```python
result = run_deep_agent(
    prompt="Write a script that downloads a webpage and extracts all links",
    model=model,
    target_file="scraper.py",
    max_steps=10,
)
print(result)
```

**内部：**
```python
def run_deep_agent(prompt, model, target_file, max_steps=8, **kwargs):
    model.max_output_tokens = max(model.max_output_tokens, 4096)
    tools = [write_file, run_python_file, execute_bash_command]
    
    task = prompt + "\n\nIMPORTANT: Follow these steps exactly:\n" \
        "1. Write the corrected file using a ```file:...``` block\n" \
        "2. Run it with run_python_file() in a ```python``` block\n" \
        "3. If errors, fix and write again, then run again\n" \
        "4. When it works, call final_answer(result)"
    
    return react_loop(
        system_prompt=build_deep_system_prompt(tools, target_file),
        task=task,
        model=model,
        tools=tools,
        max_steps=max_steps,
        target_file=target_file,
        **kwargs,
    )
```

---

### `run_deep_agent_with_config(...)` — 配置驱动

```python
result = run_deep_agent_with_config(
    prompt="Write a script...",
    model_name="ornith:9b",
    provider_name="ollama-local",  # 可选，从 agents.json 推断
    target_file="out.py",
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
    target_file="output.py",
    max_steps=8,
    **model_overrides,
):
    model = load_agent_config(model_name, provider_name, **model_overrides)
    return run_deep_agent(prompt, model, target_file, max_steps)
```

---

## 三个内置工具

| 工具 | 签名 | 说明 |
|------|------|------|
| `write_file` | `write_file(filename: str, content: str) -> str` | 进程内写文件，返回 `"Written N bytes to ..."` |
| `run_python_file` | `run_python_file(filename: str) -> str` | `subprocess.run(["python3", filename], timeout=30)` 捕获 stdout/stderr |
| `execute_bash_command` | `execute_bash_command(command: str) -> str` | `subprocess.run(command, shell=True, timeout=30)` |

**安全边界：** `write_file` 本地 I/O 无需隔离；运行脚本、Shell 命令走子进程，30s 超时。

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
    target_file="page.html",
    tools=[write_file, run_python_file, execute_bash_command, fetch_url],
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