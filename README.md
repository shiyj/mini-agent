# mini-agent

基于 `smolagents` 和本地 Ollama 模型的轻量级编程 Agent，提供**极速流**与**深思流**两种 ReAct 模式。

## 设计原则

**模型由调用者创建并传入。** Agent 本身不接触任何模型配置——你决定用哪个模型、哪个 provider、什么温度。配置仅保留提示词模板 `config/prompts.json`。

详细实现说明见 [doc/implementation.md](doc/implementation.md)。

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

### 创建模型

```python
from mini_agent import get_local_model

model = get_local_model(
    model_name="ornith:9b",
    api_base="http://YOUR_OLLAMA_HOST:11434",
    temperature=0.0,
)
```

也可以直接使用 `LiteLLMModel` 传入任意 provider。

### 极速流 — 单次代码生成

```python
from mini_agent import run_fast_agent

result = run_fast_agent("Write a function to compute Fibonacci numbers", model=model)
print(result)
```

温度建议 `0.0`，无外部工具，纯单步生成。

#### Prefill 模式（默认开启）

Qwen 系列（如 ornith:9b）有「思考模式」：模型会在 `thinking` 字段中反复试错，
消耗数千 token 后才输出 `content`，甚至完全不输出。

`run_fast_agent` 默认启用 **assistant prefill**：在消息末尾追加一条
`{"role": "assistant", "content": "```python\n"}`，模型认为代码块已开始，
直接续写代码，跳过思考阶段。

| | 无 prefill | 有 prefill（默认） |
|---|---|---|
| 成功率 | 6/7（Fibonacci 等任务失败） | 7/7 |
| Fibonacci 耗时 | ❌ 10-80s / 500-4000 tokens（空输出） | ✅ 1-2s / 48 tokens |
| 平均 token 数 | ~315 | ~136 |

关闭 prefill：`run_fast_agent(prompt, model, prefill=False)`，
或修改 `config/prompts.json` 中 `"prefill": false`。

### 深思流 — 自动纠错循环

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

温度建议 `0.4~0.5`，挂载文件写入/执行工具。完整的 Reason → Act → Observe → Repeat 循环。

---

## 验证

```bash
uv run python3 verify/verify.py
```

验证步骤：
1. **Prompt 加载** — 确认 `prompts.json` 正确读取
2. **模型连通性** — 调用 Ollama 模型返回简单回复
3. **Deep Agent 循环优化** — 让 Agent 编写质数筛法对比脚本并自动纠错

---

## 项目结构

```
mini_agent/
├── config/
│   └── prompts.json             # 提示词模板（唯一配置文件）
├── doc/
│   └── implementation.md        # 实现细节与总结
├── examples/
│   ├── basic_usage.py           # 最简使用示例
│   ├── fast_vs_deep.py          # 极速 vs 深思 对比
│   └── custom_model.py          # 手动构造 LiteLLMModel
├── src/
│   └── mini_agent/
│       ├── __init__.py
│       ├── config.py            # 提示词加载器
│       └── agent.py             # ReAct Agent 实现
├── verify/
│   └── verify.py                # 验证脚本
├── pyproject.toml
└── README.md
```

---

## 依赖

- `smolagents[litellm]` — Hugging Face Agent 框架
- 本地 Ollama 服务
