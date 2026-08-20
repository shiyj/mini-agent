# 设计文档：ChatSession 工具集成

## 概述

为 `ChatSession` 增加工具执行能力（如 `run_python_file`、`read_file` 等），使模型能在对话中自然地使用工具 —— 类似于 `run_deep_agent` 的工作方式，但在交互式聊天场景下。

---

## 当前架构

```
ChatSession
├── model: ResponsesModel
├── system_prompt: str
├── history: list[ChatMessage]
├── persist_path: Path
└── _model.generate() / _model.generate_stream()
```

**当前流程**：用户消息 → 模型生成回复 → 存入历史

---

## 目标架构

```
ChatSession
├── model: ResponsesModel
├── system_prompt: str (包含工具描述)
├── tools: dict[str, Tool]           # 可用工具
├── tool_executor: ToolExecutor       # 执行工具调用
├── history: list[ChatMessage]        # 包含工具调用 & 结果
└── _model.generate() / _model.generate_stream()
```

**目标流程**：用户消息 → 模型生成工具调用 → 执行工具 → 追加结果 → 模型继续 → 最终回复

---

## 设计组件

### 1. 工具定义 (`tools.py`)

已存在 - `Tool` 类和 `@tool` 装饰器。可用工具：
- `read_file(filename, offset=0, limit=0) -> str`
- `run_python_file(filename) -> str`

### 2. 工具调用格式

采用 OpenAI 风格的函数调用格式：

模型输出工具调用：
```json
{
  "role": "assistant",
  "content": null,
  "tool_calls": [
    {
      "id": "call_abc123",
      "type": "function",
      "function": {
        "name": "run_python_file",
        "arguments": "{\"filename\": \"test.py\"}"
      }
    }
  ]
}
```

工具执行结果：
```json
{
  "role": "tool",
  "tool_call_id": "call_abc123",
  "content": "Output: Hello World\n"
}
```

### 3. 系统提示词增强

在系统提示词中自动注入工具描述：

```
你可以使用以下工具：

1. read_file(filename, offset=0, limit=0) -> str
   读取文件并返回纯文本内容。

2. run_python_file(filename) -> str
   运行 Python 脚本文件并捕获其输出。

使用工具时，在消息中包含 tool_calls 块。
```

---

## 实施计划

### 第一阶段：核心工具执行（第 1 周）

1. **给 `ChatSession.__init__` 添加 `tools` 参数**
   ```python
   def __init__(self, ..., tools: Optional[dict[str, Tool]] = None):
       self._tools = tools or {}
   ```

2. **添加 `_execute_tool()` 方法**
   ```python
   def _execute_tool(self, name: str, arguments: dict) -> str:
       tool = self._tools.get(name)
       if not tool:
           return f"Error: Unknown tool {name}"
       try:
           return tool(**arguments)
       except Exception as e:
           return f"Error executing {name}: {e}"
   ```

3. **扩展 `_chat_blocking()` / `_chat_stream()` 处理工具调用**
   - 解析模型输出中的 `tool_calls`
   - 执行每个工具
   - 将工具结果追加到历史
   - 重新调用模型并传入工具结果（自动继续）

### 3. **系统提示词自动注入**

```python
def _build_system_prompt(self) -> str:
    base = self._system_prompt
    if self._tools:
        tool_descs = "\n".join(f"- {t.name}: {t.description}" for t in self._tools.values())
        return base + f"\n\n可用工具:\n{tool_descs}\n\n使用 tool_calls 调用工具。"
    return base
```

### 第二阶段：流式工具支持（第 2 周）

- 实时显示工具执行状态
- 实时展示工具执行进度
- 支持异步工具执行

### 第三阶段：高级功能（第 3 周）

- 工具确认（危险操作前询问用户）
- 工具结果缓存
- 并行工具执行
- 工具使用统计分析

---

## API 设计

```python
# 使用示例
model, _ = load_model("ornith:9b", "ollama-local")
chat = ChatSession(
    model=model,
    system_prompt="You are a coding assistant.",
    tools={
        "read_file": read_file,
        "run_python_file": run_python_file,
    }
)

# 模型现在可以自动使用工具
reply = chat.chat("运行 test.py 并告诉我输出")
# 内部：模型调用 run_python_file -> 获取输出 -> 回复结果
```

---

## 配置方式

```python
# 在 agents.json 中（可选）
{
  "defaults": {
    "tools": ["read_file", "run_python_file"]  # load_model 的默认工具
  }
}
```

---

## 测试策略

| 测试用例 | 说明 |
|------|-------------|
| `test_tool_call_parsing` | 模型输出有效的 tool_calls 格式 |
| `test_tool_execution` | 工具执行并返回结果 |
| `test_multi_tool_call` | 单次响应中调用多个工具 |
| `test_tool_error_handling` | 优雅处理工具错误 |
| `test_tool_chaining` | 工具结果作为下一次工具调用的输入 |
| `test_streaming_with_tools` | 流式输出配合工具调用 |

---

## 风险与对策

| 风险 | 对策 |
|------|------------|
| 模型输出无效的 tool_calls | 在系统提示词中添加 few-shot 示例 |
| 工具执行卡死 | `_execute_tool` 添加超时 |
| 工具无限循环 | 每轮最多工具调用次数（默认 5） |
| 工具错误导致会话崩溃 | 捕获所有异常，作为工具结果返回 |

---

## 文件变更

| 文件 | 变更内容 |
|------|-------------|
| `src/mini_agent/chat.py` | 添加 `tools` 参数、`_execute_tool`、工具调用逻辑 |
| `src/mini_agent/agent.py` | 导出支持工具的 `ChatSession` |
| `examples/chat_demo.py` | 带工具演示的演示脚本 |
| `src/mini_agent/__init__.py` | 导出支持工具的 `ChatSession` |

---

## 验收标准

- [ ] `chat.chat("运行 test.py")` 执行 `run_python_file("test.py")` 并返回输出
- [ ] 模型可在单次响应中链式调用多个工具
- [ ] 工具错误被捕获并作为工具结果返回（不抛出异常）
- [ ] 流式输出支持工具调用（显示工具执行，然后继续）
- [ ] 每轮最多 5 次工具调用，防止无限循环
- [ ] 所有现有测试通过 + 新增工具测试通过

---

## 时间线

| 周 | 交付物 |
|------|-------------|
| 1 | 核心工具执行、系统提示词注入、基础工具调用 |
| 2 | 流式工具支持、错误处理、最大迭代次数限制 |
| 3 | 高级功能、测试、文档 |

---

## 待决策项

**工具定义是在 `ChatSession` 创建时传入，还是通过 `load_model()` 从 `agents.json` 读取？**

**建议**：在 `ChatSession` 创建时显式传入，灵活性更高；`load_model()` 可选择性返回配置中的默认工具。