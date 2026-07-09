# config.py — 配置加载

## 概述

加载两类 JSON 配置：
- `config/prompts.json` — 提示词模板（极速流 prefill 等）
- `config/agents.json` — 模型/Provider 定义（供 `run_deep_agent_with_config` 使用）

---

## load_prompts()

```python
def load_prompts(config_path: str = "config/prompts.json") -> dict:
```

**prompts.json 结构：**
```json
{
  "fast_agent": {
    "system_instruction": "You are a fast code generator...",
    "prefill": true,
    "prefill_text": "```python\n"
  }
}
```

返回解析后的 dict。`run_fast_agent` 读取其中的 `system_instruction`、`prefill`、`prefill_text`。

---

## load_agent_config()

```python
def load_agent_config(
    model_name: str,
    provider_name: str | None = None,
    config_path: str = "config/agents.json",
) -> ResponsesModel:
```

**agents.json 结构：**
```json
{
  "providers": {
    "ollama-local": {"api_base": "http://localhost:11434"},
    "openai": {"api_base": "https://api.openai.com/v1"}
  },
  "models": {
    "ornith:9b": {"provider": "ollama-local", "temperature": 0.4, "max_output_tokens": 4096},
    "qwen2.5-coder:7b": {"provider": "ollama-local", "temperature": 0.3, "max_output_tokens": 8192}
  }
}
```

**解析逻辑：**
1. 找到 `model_name` 对应的模型配置
2. 若未指定 `provider_name`，用模型配置中的 `provider`
3. 从 `providers` 获取 `api_base`
4. 合并模型配置 + `**overrides` → 构造 `ResponsesModel`

**错误处理：**
- 模型名在多个 provider 存在且未指定 provider → `ValueError`
- 模型名不存在 → `KeyError`
- Provider 不存在 → `KeyError`

---

## 用法

```python
from mini_agent import load_prompts, load_agent_config

# 加载提示词配置
prompts = load_prompts()
print(prompts["fast_agent"]["prefill_text"])

# 配置驱动构建模型
model = load_agent_config("ornith:9b", "ollama-local", temperature=0.5)
```