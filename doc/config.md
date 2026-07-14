# config.py — 配置加载

## 概述

加载 `config/agents.json` — 按 Provider 组织的模型配置（供 `run_deep_agent_with_config` 使用）。

---

## agents.json 结构

```json
{
  "defaults": {
    "context_window": 65536,
    "max_output_tokens": 4096,
    "max_observation_length": 2000,
    "max_steps": 8,
    "compressor": {
      "max_messages": 20,
      "compress_batch": 5,
      "keep_recent": 6,
      "max_summary_tokens": 512
    }
  },
  "providers": {
    "ollama-local": {
      "baseURL": "http://localhost:11434/v1",
      "models": {
        "ornith:9b": {
          "context_window": 65536,
          "max_output_tokens": 4096,
          "max_steps": 8,
          "max_observation_length": 8000,
          "compressor": { ... }
        }
      }
    },
    "openai": {
      "baseURL": "https://api.openai.com/v1",
      "apiKey": "${OPENAI_API_KEY}",
      "models": {
        "gpt-4o": {
          "context_window": 128000,
          "max_output_tokens": 4096,
          "max_steps": 15,
          "max_observation_length": 5000,
          "compressor": { ... }
        }
      }
    }
  }
}
```

| 层级 | 字段 | 说明 |
|------|------|------|
| 顶层 | `defaults` | 全局默认值，被 provider/model 覆盖 |
| 顶层 | `providers` | 命名 Provider 集合 |
| provider | `baseURL` | API 地址（自动去除末尾 `/v1`） |
| provider | `apiKey` | 可选，支持 `${ENV_VAR}` 环境变量引用 |
| provider | `models` | 该 provider 下的模型集合 |
| model | `context_window` | 上下文窗口大小 |
| model | `max_output_tokens` | 最大输出 token |
| model | `max_steps` | ReAct 循环步数上限 |
| model | `max_observation_length` | 观察截断长度 |
| model | `compressor` | 上下文压缩器配置 |

---

## load_agent_config()

```python
def load_agent_config(
    model_name: str,
    provider_name: str | None = None,
    agents_path: str | Path | None = None,
) -> dict:
```

**解析逻辑（resolution order）：**
1. 从 `defaults` 获取全局默认值
2. 遍历 `providers`，找到包含 `model_name` 的 provider
3. 若指定 `provider_name`，只在该 provider 内查找
4. 合并 provider 的 `baseURL` / `apiKey`
5. 用 model 配置覆盖同名 key

**歧义处理：** 若 `model_name` 在多个 provider 下都存在且未指定 `provider_name`，抛出 `ValueError`。

**返回 dict 包含：** `baseURL`, `apiKey`, `context_window`, `max_output_tokens`, `max_observation_length`, `max_steps`, `compressor`, `model_name`, `provider_name`。

---

## 使用示例

```python
from mini_agent import load_agent_config, run_deep_agent

# 指定 provider
model = load_agent_config("ornith:9b", provider_name="ollama-local")
run_deep_agent("examples/data/out.py cannot run, find the problem and fix it.", model)

# 不指定 provider（自动查找，同名则抛 ValueError）
model = load_agent_config("gpt-4o")
```