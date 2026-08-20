# 设计文档：ChatSession 集成 run_deep_agent 作为子 Agent

## 概述

将 `run_deep_agent` 作为 **子 Agent** 集成到 `ChatSession` 中。`ChatSession` 负责日常对话，复杂任务（需工具、多步推理、代码执行）委托给 `run_deep_agent` 处理。

---

## 核心思想

| 组件 | 职责 |
|------|------|
| **ChatSession** | 日常对话、简单问答、上下文管理、历史记录 |
| **run_deep_agent (子 Agent)** | 复杂任务：需工具、多步推理、代码执行、Bug 修复 |

**核心原则**：不重复造轮子，复用现有成熟的 `run_deep_agent` 逻辑。

---

## 架构对比

### 当前架构
```
ChatSession (仅对话)
  └── model.generate() → 回复
```

### 目标架构
```
ChatSession
├── model: ResponsesModel
├── history: list[ChatMessage]
├── deep_agent: Optional[DeepAgent]   # 懒加载
└── _delegate_to_deep_agent(task) -> str
```

**流程**：
```
用户消息
    ↓
ChatSession 判断：是否需要深度推理/工具？
    ├─ 否 → 直接 model.generate() → 回复
    └─ 是 → 调用 run_deep_agent(task) → 获取结果 → 回复
```

---

## 判断是否需要子 Agent

### 启发式规则（可配置）

| 触发条件 | 示例 |
|----------|------|
| 显式请求执行代码 | "运行 test.py"、"运行脚本" |
| 需要读写文件 | "读取 config.json"、"写入文件" |
| 复杂 Bug 修复 | "修复这个 Bug"、"代码报错" |
| 多步推理任务 | "实现一个排序算法并测试" |
| 显式要求深度思考 | "/deep 修复这个 Bug" |

### 实现方式

```python
def _needs_deep_agent(self, user_text: str) -> bool:
    """判断是否需要子 Agent"""
    triggers = [
        "运行", "执行", "运行脚本", "运行代码",
        "读取文件", "写入文件", "修改文件",
        "修复", "Bug", "报错", "报错",
        "实现", "编写", "编写脚本",
        "/deep",  # 显式触发
    ]
    text = user_text.lower()
    return any(trigger in text for trigger in triggers)
```

也可让模型自己判断（在系统提示词中说明）。

---

## 子 Agent 调用接口

### run_deep_agent 签名（现有）

```python
def run_deep_agent(
    prompt: str,
    model: ResponsesModel,
    max_steps: int = 8,
    max_observation_length: int = 2000,
    compressor: Optional[ContextCompressor] = None,
    verbose: bool = True,
) -> str:
    ...
```

### ChatSession 集成方式

```python
class ChatSession:
    def __init__(self, ..., enable_deep_agent: bool = True):
        self._deep_agent_enabled = enable_deep_agent
        self._deep_agent = None  # 懒加载

    def _get_deep_agent(self):
        if self._deep_agent is None:
            from mini_agent import run_deep_agent
            self._deep_agent = run_deep_agent
        return self._deep_agent

    def _delegate_to_deep_agent(self, task: str) -> str:
        """委托给子 Agent 执行"""
        deep_agent = self._get_deep_agent()
        return deep_agent(
            prompt=task,
            model=self._model,
            max_steps=15,  # 可配置
            verbose=False,
        )
```

---

## 完整流程设计

### chat() 方法增强

```python
def chat(self, user_text: str, stream: bool = False) -> str | Iterator[str]:
    # 1. 判断是否需要子 Agent
    if self._deep_agent_enabled and self._needs_deep_agent(user_text):
        return self._delegate_to_deep_agent(user_text)
    
    # 2. 普通对话流程
    return self._chat_blocking(user_text)  # 或 _chat_stream
```

### 上下文传递

为保证子 Agent 有上下文，需传递必要的历史：

```python
def _delegate_to_deep_agent(self, task: str) -> str:
    # 构建包含上下文的任务提示词
    context = self._build_context_for_deep_agent()
    full_task = f"{context}\n\n用户任务: {task}"
    
    return self._get_deep_agent()(full_task, model=self._model, ...)
```

---

## 系统提示词设计

### ChatSession 系统提示词（简化版）

```
你是一个编程助手。对于简单对话直接回答。
对于复杂任务（需运行代码、读写文件、修复Bug、多步推理），
请说明你需要使用深度思考模式，我会为你启动深度思考模式。
```

### run_deep_agent 内部系统提示词（现有）

已包含完整的工具使用指引、ReAct 循环规则、错误处理等。

---

## API 设计

### ChatSession 增强

```python
class ChatSession:
    def __init__(
        self,
        model,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        session_id: Optional[str] = None,
        persist_path: Optional[str | Path] = None,
        enable_deep_agent: bool = True,  # 新增
        deep_agent_config: Optional[dict] = None,  # 子 Agent 配置
    ):
        self._deep_agent_enabled = enable_deep_agent
        self._deep_agent_config = deep_agent_config or {}
        # ...

    def chat(self, user_text: str, stream: bool = False) -> str | Iterator[str]:
        # 自动判断是否需要子 Agent
        ...

    def deep_chat(self, task: str, stream: bool = False) -> str | Iterator[str]:
        """显式调用深度思考模式"""
        return self._delegate_to_deep_agent(task, stream)
```

### 使用示例

```python
# 1. 普通对话（自动判断）
chat = ChatSession(model, system_prompt="...")
chat.chat("你好")  # 普通对话
chat.chat("运行 test.py")  # 自动触发深度模式

# 2. 显式触发深度模式
chat.deep_chat("修复 test.py 的 Bug")

# 3. 禁用深度模式
chat = ChatSession(model, enable_deep_agent=False)
```

---

## 配置选项

```python
deep_agent_config = {
    "max_steps": 15,           # 最大步数
    "max_observation_length": 2000,
    "auto_trigger": True,      # 是否自动触发
    "trigger_keywords": [...], # 自定义触发词
    "verbose": False,          # 子 Agent 是否输出详细日志
}

chat = ChatSession(model, deep_agent_config={
    "max_steps": 20,
    "trigger_keywords": ["修复", "调试", "运行"],
})
```

---

## 与 run_deep_agent 复用点

| 组件 | 复用方式 |
|------|----------|
| 工具定义 | 直接复用 `read_file`、`run_python_file` |
| ReAct 循环 | 完全复用 `react_loop` |
| 错误恢复 | 复用 E001-E004 错误恢复机制 |
| 压缩器 | 复用 `SummaryCompressor` |
| 提示词 | 复用 `build_deep_system_prompt` |

---

## 文件变更

| 文件 | 变更 |
|------|------|
| `src/mini_agent/chat.py` | 新增 `_needs_deep_agent`、`_delegate_to_deep_agent`、`deep_chat` |
| `src/mini_agent/agent.py` | 导出增强后的 `ChatSession` |
| `examples/chat_demo.py` | 新增深度模式演示 |
| `examples/cli_chat.py` | 支持 `/deep` 命令显式触发 |

---

## 测试用例

| 测试 | 描述 |
|------|------|
| `test_auto_trigger` | 包含触发词时自动调用子 Agent |
| `test_explicit_deep_chat` | `deep_chat()` 显式调用 |
| `test_deep_agent_context` | 子 Agent 能访问对话历史 |
| `test_deep_agent_tools` | 子 Agent 正确使用工具 |
| `test_deep_agent_streaming` | 流式输出支持深度模式 |
| `test_disable_deep_agent` | `enable_deep_agent=False` 时不触发 |

---

## 实施优先级

| 优先级 | 任务 |
|------|------|
| P0 | `_needs_deep_agent` + `_delegate_to_deep_agent` 基础集成 |
| P0 | `chat()` 自动触发逻辑 |
| P1 | `deep_chat()` 显式调用 |
| P1 | 上下文传递（历史传给子 Agent） |
| P1 | `/deep` 命令支持 |
| P2 | 流式输出支持深度模式 |
| P2 | 配置选项 (`deep_agent_config`) |
| P3 | 测试用例、文档 |

---

## 总结

**核心思想**：ChatSession 做路由，run_deep_agent 做执行。不重复造轮子，最大化复用现有成熟代码。

**关键点**：
1. 自动/显式双模式触发
2. 上下文透传（历史 → 子 Agent）
3. 复用 run_deep_agent 完整工具链
4. 保持 ChatSession 接口简洁