# mini-agent 实现文档

## 概述

mini-agent 是一个零外部 Agent 依赖的轻量级编程 Agent，通过 `ResponsesModel`
（基于 httpx 调用 /v1/responses API）对接本地模型（如 Ollama 上的 ornith:9b），
提供**极速流**和**深思流**两种模式。

核心决策：**用 ~400 行自实现代码替代 smolagents 的 3000+ 行。**
- 导入时间从 2.8s 降至 0.71s（-75%）
- 无沙盒白名单问题（`bytearray` 不再被拦截）
- 系统提示词从 174 行精简至 ~30 行
- 仅依赖 `httpx`，无 `smolagents` 包袱

---

## 项目结构

```
src/mini_agent/
├── __init__.py          # 公共 API 导出
├── agent.py             # get_local_model + 3 个工具 + run_fast_agent + run_deep_agent
├── code_executor.py     # exec()-based 代码执行引擎（替代 1768 行 LocalPythonExecutor）
├── config.py            # config/prompts.json 加载器
├── model.py             # ResponsesModel（httpx + SSE 流式）
├── prompts.py           # 小模型优化系统提示词（极速 12 词，深思 ~30 行）
├── react_agent.py       # ReAct 循环核心（替代 800 行 CodeAgent）
├── tools.py             # Tool 抽象 + @tool 装饰器
└── types.py             # ChatMessage / MessageRole / TokenUsage 等数据类型
```

---

## 核心模块详解

### `types.py` — 数据类型

替代 smolagents 的 `models` 和 `monitoring` 模块。纯 dataclass + Enum，无外部依赖。

- `MessageRole`：`USER` / `ASSISTANT` / `SYSTEM` 枚举
- `TokenUsage`：`input_tokens` / `output_tokens` 统计
- `ChatMessage`：模型响应封装，含 `role` / `content` / `raw` / `token_usage`
- `remove_content_after_stop_sequences()`：按停序列截断输出
- `get_clean_message_list()`：消息列表归一化（合并连续同角色消息、展平 content）

### `tools.py` — 工具抽象

替代 `smolagents.tools.Tool` 和 `@tool` 装饰器。

```python
class Tool:
    name: str          # 函数名，如 "write_file"
    description: str   # 函数文档
    func: Callable     # 底层函数
    inputs: dict       # 输入参数 schema（从类型注解自动提取）

    def to_prompt(self) -> str:  # 生成提示词中的函数签名
    def __call__(self, **kwargs) -> Any:  # 调用底层函数
```

`@tool` 装饰器通过 `inspect.signature` 自动提取参数类型，取文档字符串首段作为描述。
与 smolagents 版的关键区别：**不验证参数名必须为 `argument` 或 `args`**，更宽松。

### `code_executor.py` — 代码执行引擎

替代 smolagents 的 `LocalPythonExecutor`（1768 行 AST 解释器）。

核心流程：
1. 创建单一 `exec_ns` 字典（`{"__name__": "__main__", "__builtins__": __builtins__}`）
2. 注入工具函数（自动包装为 print-return-value 版本）
3. 注入 `final_answer(x)` 函数
4. `exec(compile(code, "<agent_code>", "exec"), exec_ns)` 执行
5. 捕获 stdout，返回 `ExecutionResult`

**关键设计决策：**

| 问题 | 方案 |
|------|------|
| `exec()` 分离 globals/locals 导致 `import` 在函数内不可见 | 使用单一 `exec_ns` 字典同时作为 globals 和 locals |
| `exec()` 不捕获表达式返回值 | 工具函数自动 `print()` 返回值 |
| `final_answer` 跨作用域设置 | 用 `list[Any]` 作为可变容器，`final_answer_value[0] = x` |
| 代码执行安全性 | 不依赖 AST 白名单——`run_python_file` 工具本身通过子进程隔离 |

**没有白名单。没有 AST 解释器。** 所有 Python 内置函数都可用，
安全由工具自身保证（`run_python_file` 走子进程，`execute_bash_command` 走 shell）。

### `react_agent.py` — ReAct 循环

替代 smolagents 的 `CodeAgent`（约 800 行）。

核心循环：

```
system_prompt + task → model.generate() → parse_code() → execute_code() → Observation → model.generate() → ...
                                                                         ↑
                                                                   final_answer() → done
```

**提示词优化（针对 9B 模型）：**

1. 系统提示词约 30 行（smolagents 默认 174 行）
2. 没有规划阶段——小模型在多步规划中容易注意力崩溃
3. 明确要求先 `write_file()` 保存脚本，再用 `run_python_file()` 执行
4. 强调"代码成功 → 立即 `final_answer()`"——防止模型重复执行

**`parse_code()` 关键逻辑：**

1. 先剥离 `<think>...</think>` 块（Qwen 思考模式产物）
2. 如果只剩 `</think>`（SSE 流已消耗开始标签），取 `</think>` 之后的内容
3. 匹配 ` ```python ` 代码块，连接多个块
4. 回退：尝试 ` ``` ` 泛代码块
5. 再回退：尝试 `ast.parse()` 直接解析未折叠的代码

**`stop_sequences`**：设置为 `["Observation:"]`，防止模型在单步中生成下一轮的 "Observation:" 前缀。

### `prompts.py` — 系统提示词

**极速流：** 仅 12 个英文单词。
```
"You are a fast code generator. Output only executable Python code. No explanation, no thinking blocks."
```

**深思流提示词设计原则：**

1. **短小直接**（30 行 vs smolagents 174 行）
2. **明确 final_answer 触发条件：** "When the script works, call final_answer(result) IMMEDIATELY"
3. **无规划阶段**：小模型在多步规划中容易迷失
4. **代码块格式明确：** ```python``` 固定格式
5. **防重复指令：** "Never repeat working code"

提供标准的工具使用示例：
```python
code = "print(sum(range(1, 101)))"
result = write_file(filename="/tmp/test.py", content=code)
print(result)
output = run_python_file(filename="/tmp/test.py")
print(output)
final_answer(output)
```

### `model.py` — ResponsesModel

使用 `httpx` 直接调用 `/v1/responses` 端点。

**关键特征：**

- **SSE 流式传输**：首 Token 到达约 0.3s（默认启用）
- **非流式模式**：`stream=False` 时使用单次 POST 请求，方便调试与离线分析
- **Responses API 格式**：使用 `instructions`（系统消息）+ `input`（对话历史）+ `stream`
- **停止序列**：在输出上应用 `remove_content_after_stop_sequences`
- **无 smolagents 继承**：`generate(messages, stop_sequences, ...)` 是独立的公共方法

**SSE 事件处理：**

```
response.output_text.delta → 累加 delta 到 output_text
response.completed         → 提取 usage 数据，回退从 response.output 提取 text
```

### `agent.py` — Agent 实现

**模型工厂：** `get_local_model()` 是可选便利函数，用户可直接构造 `ResponsesModel`。

**三个工具函数：**

| 工具 | 说明 |
|------|------|
| `write_file(filename, content)` | 将字符串写入文件，返回确认 |
| `run_python_file(filename)` | 执行 Python 文件并捕获输出，30s 超时 |
| `execute_bash_command(command)` | 执行任意 shell 命令，30s 超时 |

**两个模式对比：**

| | 极速流 | 深思流 |
|---|---|---|
| 函数 | `run_fast_agent(prompt, model)` | `run_deep_agent(prompt, model, target_file, max_steps=8)` |
| 本质 | 单次推理，仅生成 | 完整 ReAct 循环 |
| 工具 | 无 | write_file + run_python_file + execute_bash_command |
| 温度建议 | 0.0（确定） | 0.4~0.5（试错多样） |
| 系统提示词 | 12 词 | ~30 行 |
| max_output_tokens | 由 model 控制 | 至少 4096 |
| 适用场景 | 简单生成、语法转换、代码模板 | 复杂脚本、调试、迭代任务 |

---

## Prefill 机制

### 问题背景

ornith:9b（Qwen 系）有 Ollama 原生的「思考模式」。
收到代码生成请求时，模型会在 `message.thinking` 字段中反复试错多个方案，
消耗 2000-4000 token 仍未输出 `content`。

### 解决方案

在消息列表末尾追加 assistant 预填消息：

```python
messages = [
    {"role": "system", "content": "Output only Python code."},
    {"role": "user", "content": "Write a Python one-liner..."},
    {"role": "assistant", "content": "```python\n"},  # ← prefill
]
```

模型看到自己「已经开始写代码」，直接续写 content，完全跳过 thinking 阶段。

**原理**：Ollama 的 Qwen 模型在收到 assistant 消息已有内容时，认为思考阶段已结束，
直接进入 content 生成。这相当于强制模型「从代码块中间开始续写」。

### 性能对比

| 任务 | 无 prefill | 有 prefill |
|------|-----------|-----------|
| Fibonacci one-liner | ❌ 10.3s / 500t | ✅ 1.3s / 48t |
| Palindrome 函数 | ✅ 2.1s / 89t | ✅ 1.7s / 69t |
| CSV 读取脚本 | ✅ 8.0s / 385t | ✅ 2.1s / 90t |
| 反转链表 | ✅ 8.6s / 414t | ✅ 7.1s / 338t |
| 下载网页提取链接 | ✅ 9.1s / 435t | ✅ 3.0s / 134t |
| 合并有序链表 | ✅ 6.3s / 297t | ✅ 5.3s / 250t |
| sum 1 to 100 | ✅ 1.9s / 81t | ✅ 0.8s / 20t |

### 配置方式

`config/prompts.json` 中 `fast_agent` 段：

```json
{
  "fast_agent": {
    "system_instruction": "...",
    "prefill": true,
    "prefill_text": "```python\n"
  }
}
```

也可在调用时覆盖：`run_fast_agent(prompt, model, prefill=False)`。

---

## 与 smolagents 方案对比

| 维度 | smolagents 方案 | 自实现方案 |
|------|----------------|-----------|
| 导入时间 | 2.8s | 0.71s（-75%） |
| 外部依赖 | smolagents + httpx | 仅 httpx |
| 代码执行 | 1768 行 AST 解释器（白名单） | ~120 行 exec()（无白名单） |
| Agent 循环 | ~800 行 CodeAgent | ~190 行 react_loop() |
| 系统提示词 | 174 行 code_agent.yaml | ~30 行 Python 字符串 |
| 沙盒限制 | bytearray 等被拦截 | 无限制 |
| Deep Agent 步数 | 8 步未调用 final_answer | 2 步完成 |
| 安全模型 | 白名单 + AST 分析 | 工具本身提供隔离（子进程） |
| 参数校验 | 工具参数名必须为 `argument`/`args` | 无此限制 |

---

## 验证流程

`verify/verify.py` 执行三步检查：

1. **Prompt 加载**：确认 `config/prompts.json` 正确读取，模板渲染正常
2. **模型连通性**：调用 Ollama 模型返回一条简短回复
3. **Deep Agent 循环优化**：让 Agent 编写埃拉托色尼筛法和轮式优化筛法对比脚本，
   验证两个筛法都输出 9592 个素数且集合一致。最多重试 10 次，温度从 0.45 逐渐上升。

运行验证：

```bash
cd mini_agent
uv run python3 verify/verify.py
```

---

## 使用示例

`examples/` 目录包含三个可直接运行的示例：

| 文件 | 说明 |
|------|------|
| `basic_usage.py` | 创建模型 → 运行 fast agent，最简流程 |
| `fast_vs_deep.py` | 同一任务分别在两种模式下执行，观察行为差异 |
| `custom_model.py` | 跳过 `get_local_model`，直接构造 `ResponsesModel` |

---

## 依赖

- Python >= 3.10
- `httpx` >= 0.27.0（**唯一的**外部依赖）
- 本地 Ollama 服务 + 可用模型
