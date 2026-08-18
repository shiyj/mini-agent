# model.py — ResponsesModel (模型通信层)

## 概述

使用 `httpx` 直接调用兼容 OpenAI Responses API 的 `/v1/responses` 端点（Ollama、自建代理等）。

单次 POST 请求，无流式模式。空白输出自动重试（温度 +0.25，最多 1 次）。

---

## ResponsesModel

```python
class ResponsesModel:
    def __init__(
        self,
        model_name: str,
        api_base: str = "http://localhost:11434",
        temperature: float = 0.0,
        max_output_tokens: int = 512,
        context_window: int = 65536,
        log_file: str | None = None,
        reasoning: dict | None = None,      # 如 {"effort": "low"}
        timeout: tuple = (10.0, 180.0),     # (connect, total)
    ):
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `api_base` | `http://localhost:11434` | 自动去除末尾 `/v1` |
| `temperature` | `0.0` | 采样温度 |
| `max_output_tokens` | `512` | 最大生成长度 |
| `context_window` | `65536` | 模型上下文窗口（供 compressor 估算用） |
| `reasoning` | `None` | 可选 `{"effort": "low|medium|high"}` 控制推理深度 |
| `log_file` | `None` | 可选 JSONL 请求/响应日志路径 |

---

## generate(messages, stop_sequences) → ChatMessage

```python
def generate(
    self,
    messages: list[dict],
    stop_sequences: list[str] | None = None,
) -> ChatMessage:
```

**流程：**
```
1. get_clean_message_list(messages) → 合并同角色、展平 content
2. 分离 system 消息 → instructions 字段
3. 构建请求体：
   {
     "model": "ornith:9b",
     "input": [...],           # 非 system 消息
     "instructions": "...",    # system 消息
     "temperature": 0.4,
     "max_output_tokens": 4096,
     "reasoning": {"effort": "low"}
   }
4. 空白重试：响应文本为空 → 温度 +0.25 重试 1 次
5. _send_bulk() → httpx.post("/v1/responses", json=body)
6. _extract_output_text() → 遍历 output[].content[].output_text
7. 应用 stop_sequences 截断
8. 返回 ChatMessage(content, token_usage, raw=response)
```

---

## _extract_output_text(data) 实现要点

```python
def _extract_output_text(self, data: dict) -> str:
    parts = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    parts.append(content.get("text", ""))
    return "".join(parts)
```

---

## 空白重试机制

```python
for attempt in range(2):
    response = self._send_bulk(body)
    text = self._extract_output_text(response.json())
    if text.strip():
        return text
    if attempt == 0:
        body["temperature"] = min(body.get("temperature", 0) + 0.25, 1.0)
return ""
```

容纳模型偶发空白输出。

---

## URL 自动归一化

```python
api_base = api_base.rstrip("/")
if api_base.endswith("/v1"):
    api_base = api_base[:-3]
```

兼容带 `/v1` 和不带的输入格式。

---

## 用法示例

```python
from mini_agent import ResponsesModel, run_deep_agent

model = ResponsesModel(
    model_name="ornith:9b",
    api_base="http://localhost:11434",
    temperature=0.4,
    max_output_tokens=4096,
    reasoning={"effort": "low"},
)

result = run_deep_agent("Fix the bug in foo.py", model)
```