# types.py — 数据类型与消息处理

## 概述

纯 dataclass + Enum，零外部依赖。

---

## 核心类型

### `MessageRole`
```python
class MessageRole(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
```

### `TokenUsage`
```python
@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
```

### `ChatMessage`
```python
@dataclass
class ChatMessage:
    role: MessageRole
    content: str
    raw: Any = None           # 原始响应对象
    token_usage: TokenUsage = None
```

---

## 消息处理工具

### `remove_content_after_stop_sequences(text, stop_sequences)`
在第一个停止序列处截断文本。

```python
remove_content_after_stop_sequences("hello [Result] world", ["[Result]"])
# → "hello "
```

### `get_clean_message_list(messages, flatten_messages_as_text=False)`
归一化消息列表：
- 合并连续同角色消息
- `flatten_messages_as_text=True` 时，将 `content` 列表展平为纯文本

```python
# 输入
[{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
# 输出 (flatten=True)
[{"role": "user", "content": "hi"}]
```

---

## 使用示例

```python
from mini_agent.types import (
    ChatMessage, MessageRole, TokenUsage,
    get_clean_message_list, remove_content_after_stop_sequences
)

msg = ChatMessage(role=MessageRole.USER, content="hello")
cleaned = get_clean_message_list([msg], flatten_messages_as_text=True)
```