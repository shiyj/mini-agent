# config.py — 配置加载

## 概述

加载 `config/agents.json` — 按 Provider 组织的模型配置（供 `load_model()` 使用）。

---

## agents.json 结构

```json
{
  "defaults": {
    "context_window": 65536,
    "max_output_tokens": 4096,
    "max_observation_length": 2000,
    "max_steps": 8,
    "temperature": 0.0
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
          "temperature": 0.0
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
          "temperature": 0.0
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
| model | `temperature` | 采样温度 |

---

## load_model()

```python
def load_model(
    model_name: str,
    provider_name: str | None = None,
    *,
    log_file: str | None = None,
    reasoning: dict | None = None,
    agents_path: str | Path | None = None,
) -> tuple[ResponsesModel, SummaryCompressor]:
```

**行为：**
1. 从 `agents.json` 读取配置（解析逻辑同旧 `load_agent_config`）
2. 自动构造 `ResponsesModel`（temperature/context_window/max_output_tokens 等全从 JSON 读取）
3. 返回 `(ResponsesModel, SummaryCompressor)` —— compressor 为新实例，每次调用独立

**返回值：** `(model, compressor)` —— 直接可传给 `run_fast_agent(model)` / `run_deep_agent(model)`。

---

## 内部函数（不建议外部使用）

```python
def _load_agent_config(
    model_name: str,
    provider_name: str | None = None,
    agents_path: str | Path | None = None,
) -> dict:
```

仅供内部/测试使用，返回扁平化配置 dict（包含 baseURL、apiKey、temperature 等）。

---

## 使用示例

```python
from mini_agent import load_model, run_fast_agent, run_deep_agent

# 最简单用法：provider + model 名
model, compressor = load_model("ornith:9b", "ollama-local")
result = run_fast_agent("Hello world", model)

# 带日志
model, _ = load_model("ornith:9b", "ollama-local", log_file="run.jsonl")

# 自定义 reasoning（如 o1 系列）
model, _ = load_model("o1-mini", "openai", reasoning={"effort": "high"})
```