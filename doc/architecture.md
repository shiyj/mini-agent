# mini-agent 架构与运行流程

本文以 `examples/fast_vs_deep.py` 为例，详细追踪整个框架从入口到模型调用、代码执行、ReAct 循环的完整路径。

---

## 示例入口

```python
# examples/fast_vs_deep.py（简化）
from mini_agent import get_local_model, run_fast_agent, run_deep_agent

fast_model = get_local_model("ornith:9b", temperature=0.0)
deep_model = get_local_model("ornith:9b", temperature=0.4)

TASK = "Write a Python script ... Sieve of Eratosthenes ..."

# 极速流：单次生成
fast_result = run_fast_agent(prompt=TASK, model=fast_model)

# 深思流：完整 ReAct 循环
deep_result = run_deep_agent(prompt=TASK, model=deep_model,
                              target_file="/tmp/check_primes.py", max_steps=8)
```

整个流程分为三个独立阶段：**模型创建 → 极速流 → 深思流**。它们通过 `ResponsesModel` 共享同一个模型通信层。

---

## 阶段 0：模型创建

```
get_local_model("ornith:9b", temperature=0.4)
    │
    ▼
ResponsesModel.__init__(
    model_name="ornith:9b",
    api_base="http://localhost:11434",
    temperature=0.4,
    max_output_tokens=512,
    stream=False,
)
    │
    ▼
  存储参数，创建 httpx.Client(timeout=(connect=10s, total=180s))
```

`get_local_model` 是便利函数，直接构造 `ResponsesModel`。用户也可以跳过它直接实例化：

```python
from mini_agent import ResponsesModel
model = ResponsesModel(model_name="ornith:9b", stream=False)
```

关键参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `api_base` | `http://localhost:11434` | /v1/responses 端点地址 |
| `stream` | `False` | 非流式模式，单次 POST 请求返回全部结果 |
| `temperature` | `0.0` | 采样温度 |
| `max_output_tokens` | `512` | 最大生成长度 |

---

## 阶段 1：极速流（Fast Agent）

极速流是**单次模型调用**，没有执行环境、没有循环、没有工具。本质是一个带 prefill 优化的文本生成器。

```
run_fast_agent(prompt=TASK, model=fast_model)
    │
    ├─ 1. 加载 config/prompts.json → 读取 fast_agent.system_instruction
    ├─ 2. 构建消息列表
    │      ┌─ system:  "You are a fast code generator..."
    │      ├─ user:    TASK
    │      └─ assistant: "```python\n"   ← prefill 消息
    │
    ├─ 3. model.generate(messages)
    │      │
    │      ▼  (进入模型通信层，见下文)
    │
    └─ 4. 拼接 prefill_text + content → 返回完整代码
```

### 为什么要 Prefill？

Qwen 系模型有 Ollama 原生的「思考模式」：收到代码请求时，模型在 `thinking` 字段中反复试错，消耗数千 token 后才输出 `content`。

Prefill 在消息列表末尾追加一条 assistant 消息：

```python
{"role": "assistant", "content": "```python\n"}
```

模型看到自己「已经开始写代码」，跳过 thinking 阶段直接续写 content。实测结果：

| 指标 | 无 prefill | 有 prefill |
|------|-----------|-----------|
| Fibonacci 耗时 | 10-80s | 1-2s |
| Token 消耗 | 500-4000 | ~48 |
| 成功率 | 6/7 | 7/7 |

---

## 阶段 2：深思流（Deep Agent）

深思流是完整的 **ReAct 循环**：Write → Run → Observe → Fix → Repeat。

```
run_deep_agent(prompt=TASK, model=deep_model,
               target_file="/tmp/check_primes.py", max_steps=8)
    │
    ├─ 1. 提高 token 上限
    │      model.max_output_tokens = max(512, 4096) → 4096
    │      复杂脚本需要更多 token，防止中途截断
    │
    ├─ 2. 构建工具列表
    │      [write_file, run_python_file, execute_bash_command]
    │
    ├─ 3. 构建任务指令（附加到用户 prompt 后面）
    │      TASK + "\n\nIMPORTANT: Follow these steps exactly:"
    │      "1. Use write_file(...) to save the script"
    │      "2. Use run_python_file(...) to run it"
    │      "3. If errors, fix and write_file again"
    │      "4. When it works, call final_answer(result)"
    │
    └─ 4. react_loop(task, model, tools, max_steps=8)
           │
           ▼  (进入 ReAct 循环，见下文)
```

### ReAct 循环详解

`react_loop` 是 deep agent 的核心，位于 `react_agent.py`：

```
┌─────────────────────────────────────────────────────────────┐
│                     ReAct 循环                               │
│                                                             │
│  system_prompt + task                                        │
│         │                                                    │
│         ▼                                                    │
│  ┌─ model.generate(messages, stop_sequences=["Observation:"])│
│  │         │                                                 │
│  │         ▼                                                 │
│  │  parse_code(response) → 提取 ```python 代码块             │
│  │         │                                                 │
│  │         ▼                                                 │
│  │  execute_code(code, tools) → exec() + 工具注入            │
│  │         │                                                 │
│  │    ┌────┴────┐                                           │
│  │    │         │                                            │
│  │  final_answer  正常结束                                    │
│  │    detected    │                                           │
│  │    │           ▼                                           │
│  │    ▼     Observation → 追加到 messages                    │
│  │  返回结果     │                                            │
│  │              ▼                                            │
│  │         继续下一轮 ──────────────────────────────────────────┘
```

每一轮的详细步骤：

#### 第 1 步：调用模型

```python
system_prompt = build_deep_system_prompt(tools)
# 返回约 30 行的系统提示词，包含工具签名和使用示例

messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user",   "content": task},
]

response = model.generate(messages, stop_sequences=["Observation:"])
output = response.content
```

`stop_sequences=["Observation:"]` 防止模型在同一轮输出中提前生成下一轮的 "Observation:" 前缀。

#### 第 2 步：解析代码

```python
code = parse_code(output)
```

三阶段回退解析：

```
原始模型输出
    │
    ├─ 剥离 <think>...</think>（Qwen 思考模式产物）
    ├─ 剥离 </think> 及其之前内容（SSE 流已消费开始标签）
    │
    ├─ 匹配 ```python ... ``` → 多个块用 \n\n 连接
    ├─ 回退：匹配 ``` ... ```
    └─ 回退：ast.parse() 直接解析未折叠文本
```

#### 第 3 步：执行代码

```python
result = execute_code(code, tools)
```

这是整个框架最核心的机制。见下文的「代码执行引擎」章节。

#### 第 4 步：构建观察结果

```python
if result.error:
    observation = f"Execution logs:\n{result.logs}\nError:\n{result.error}"
else:
    observation = f"Execution logs:\n{result.logs}"
    if result.output is not None:
        observation += f"\nLast output: {result.output}"
```

#### 第 5 步：判断是否结束

```python
if result.is_final_answer:
    return str(result.output)    # ← 模型调用了 final_answer()
# 否则追加到 messages 继续循环
messages.append({"role": "assistant", "content": output})
messages.append({"role": "user", "content": f"Observation:\n{observation}"})
```

### 实际运行轨迹（fast_vs_deep.py）

```
Step 1:
  Thought: 写一个脚本文件，包含埃拉托色尼筛法
  Code:    code = "def sieve...\n"
           write_file(filename="/tmp/check_primes.py", content=code)
           print(result)
  ──────────────────────────────────────────────────
  Observation:
    Written 685 bytes to /tmp/check_primes.py

Step 2:
  Thought: 脚本已写入，现在执行它
  Code:    output = run_python_file(filename="/tmp/check_primes.py")
           print(output)
           final_answer(output)
  ──────────────────────────────────────────────────
  Observation:
    【OK】
    5133
    541
  → final_answer detected → 返回结果 ✅
```

共 2 步完成。对比 smolagents 方案需要 8+ 步且经常不调用 final_answer。

---

## 代码执行引擎（核心机制）

`execute_code()` 位于 `code_executor.py`，替代 smolagents 的 1768 行 AST 解释器。

### 执行环境

```python
exec_ns = {
    "__name__": "__main__",
    "__builtins__": __builtins__,   # 完整的内置函数，没有白名单
}
```

**单一命名空间**：同时作为 `exec()` 的 globals 和 locals。这是关键设计决策——如果使用分离的 globals/locals，函数定义内部的 `import` 语句会不可见。

### 工具注入

```python
for t in tools:
    exec_ns[t.name] = t
```

工具直接以函数形式注入到执行环境。当模型写 `write_file(...)` 时，实际调用的是 `Tool.__call__()`，它转而调用底层的 Python 函数。

**没有自动打印包装器**：模型通过 `print(result)` 自行控制输出，避免重复打印。

### final_answer 注入

```python
final_answer_value: list[Any] = [None]
got_final_answer: list[bool] = [False]

def final_answer(x: Any = None) -> Any:
    final_answer_value[0] = x
    got_final_answer[0] = True
    return x

exec_ns["final_answer"] = final_answer
```

为什么用 `list[Any]` 而不是普通变量？

```
在 exec() 内部，赋值操作默认作用于局部作用域。
如果 final_answer 是外层函数定义的闭包变量：
    got_final_answer = False
    def final_answer(x):
        got_final_answer = True   ← 这会在 exec 的局部作用域创建新变量！
        ...

用 list[Any] 可变容器绕过这个问题：
    got_final_answer[0] = True    ← 修改的是列表元素，不是重新赋值
```

### stdout 捕获

```python
old_stdout = sys.stdout
captured = io.StringIO()
sys.stdout = captured

try:
    exec(compile(code, "<agent_code>", "exec"), exec_ns)
finally:
    sys.stdout = old_stdout

logs = captured.getvalue()   # 所有 print() 输出
```

### 执行结果提取

```python
if got_final_answer[0]:
    output = final_answer_value[0]    # 模型调用了 final_answer(x)
else:
    output = exec_ns.get("_", None)   # 回退：最后一条表达式的值
```

---

## 模型通信层

`ResponsesModel.generate()` 位于 `model.py`，通过 httpx 调用 `/v1/responses` 端点。

### 消息处理流程

```
generate(messages, stop_sequences, ...)
    │
    ├─ 1. get_clean_message_list(messages)
    │      合并连续同角色消息，展平 content 列表
    │
    ├─ 2. 分离系统消息
    │      将 system 角色消息提取为 instructions 字段
    │      其余消息放入 input_items 列表
    │
    ├─ 3. 构建请求体
    │      {
    │        "model": "ornith:9b",
    │        "input": [{"role": "user", "content": "..."}],
    │        "instructions": "system message",
    │        "temperature": 0.4,
    │        "max_output_tokens": 4096,
    │        "stream": false
    │      }
    │
    ├─ 4. _send_bulk(body)    ← stream=False
    │      httpx.post("/v1/responses", json=body)
    │             │
    │             ▼
    │      _extract_output_text(data)
    │      → 遍历 output[].content[].output_text
    │      → 拼接为完整字符串
    │
    ├─ 5. 应用 stop_sequences
    │      → 在第一个 ["Observation:"] 处截断
    │
    └─ 6. 返回 ChatMessage(content, token_usage, ...)
```

### Responses API 格式

Ollama 的 `/v1/responses` 端点遵循 OpenAI Responses API 规范：

```
请求:
{
  "model": "ornith:9b",
  "input": [{"role": "user", "content": "..."}],
  "instructions": "System prompt",
  "stream": false
}

响应:
{
  "output": [
    {"type": "reasoning", "summary": [...]},
    {
      "type": "message",
      "role": "assistant",
      "content": [
        {"type": "output_text", "text": "最终输出"}
      ]
    }
  ],
  "usage": {
    "input_tokens": 27,
    "output_tokens": 100
  }
}
```

`_extract_output_text()` 从 `output[].content[].output_text` 字段提取文本，跳过 `type: "reasoning"` 的思考过程块。

---

## 工具系统

### @tool 装饰器

```python
@tool
def write_file(filename: str, content: str) -> str:
    """Write text content to a file on disk."""
    ...

# 实际创建的是 Tool 实例：
# Tool(name="write_file",
#      description="Write text content to a file on disk.",
#      func=write_file,
#      inputs={"filename": {"type": "str"}, "content": {"type": "str"}})
```

`@tool` 自动从函数签名提取参数类型，从文档字符串提取描述。

### Tool 的 to_prompt()

```python
def to_prompt(self) -> str:
    params = ", ".join(f"{name}: {info.get('type', 'str')}"
                       for name, info in self.inputs.items())
    desc = self.description.strip().split("\n")[0]
    return f"- {self.name}({params}): {desc}"
```

输出示例：

```
- write_file(filename: str, content: str): Write text content to a file on disk.
- run_python_file(filename: str): Run a Python script file and capture its output.
- execute_bash_command(command: str): Execute a Bash/shell command on the local machine.
```

### 三个内置工具

| 工具 | 机制 | 超时 |
|------|------|------|
| `write_file(filename, content)` | `open(filename, "w").write(content)` | 无（本地 I/O） |
| `run_python_file(filename)` | `subprocess.run(["python3", filename])` | 30s |
| `execute_bash_command(command)` | `subprocess.run(command, shell=True)` | 30s |

`write_file` 在当前进程执行（只需要写磁盘）。`run_python_file` 和 `execute_bash_command` 走子进程隔离——这是框架的安全边界：模型通过 exec() 运行的代码是受控的，需要隔离的操作通过子进程工具完成。

---

## 系统提示词

### 极速流（12 词）

```python
"You are a fast code generator. "
"Output only executable Python code. "
"No explanation, no thinking blocks."
```

### 深思流（~30 行）

`build_deep_system_prompt(tools)` 动态构建，包含：

1. **工作流说明**：write_file → run_python_file → fix → final_answer
2. **规则**：用 print() 看输出，成功后立即 final_answer，不重复执行
3. **工具签名**：由 `Tool.to_prompt()` 动态生成
4. **完整示例**：

```python
code = "print(sum(range(1, 101)))"
result = write_file(filename="/tmp/test.py", content=code)
print(result)
output = run_python_file(filename="/tmp/test.py")
print(output)
final_answer(output)
```

设计原则：

| 原则 | 原因 |
|------|------|
| 短小（~30 行） | 9B 模型在长提示词中注意力易崩溃 |
| 无规划阶段 | 小模型多步规划容易迷失 |
| 明确 final_answer 触发 | 防止模型不停重跑代码 |
| 工具调用示例 | 教模型正确使用 write_file → run_python_file 流程 |

---

## 完整数据流图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         fast_vs_deep.py                                │
│                                                                         │
│  get_local_model() ──→ ResponsesModel (stream=false)                    │
│       │                                                                 │
│       ├── run_fast_agent(prompt, model)                                 │
│       │      │                                                          │
│       │      ├─ config.py:load_prompts()                                │
│       │      │      config/prompts.json → {"fast_agent": {...}}        │
│       │      │                                                          │
│       │      ├─ 构建 messages: [system, user, assistant(prefill)]       │
│       │      │                                                          │
│       │      └─ model.generate(messages)                                │
│       │             │                                                   │
│       │             ├─ types.get_clean_message_list()                   │
│       │             ├─ _send_bulk(body) → httpx.post()                  │
│       │             │      │                                            │
│       │             │      └─ _extract_output_text(data)                │
│       │             │                                                    │
│       │             └─ ChatMessage(content, token_usage)                │
│       │                                                                 │
│       └── run_deep_agent(prompt, model, target_file, max_steps)         │
│              │                                                          │
│              ├─ model.max_output_tokens = 4096                          │
│              ├─ tools = [write_file, run_python_file, bash]             │
│              │                                                          │
│              └─ react_loop(task, model, tools)                          │
│                     │                                                   │
│                     ├─ build_deep_system_prompt(tools)                  │
│                     │      ~30 行含工具签名 + 示例                       │
│                     │                                                   │
│                     └─ for step in 1..max_steps:                        │
│                            │                                            │
│                            ├─ model.generate(messages,                  │
│                            │      stop_sequences=["Observation:"])      │
│                            │                                            │
│                            ├─ parse_code(response)                      │
│                            │      <think> 剥离 → ```python 提取          │
│                            │                                            │
│                            ├─ execute_code(code, tools)                 │
│                            │      │                                     │
│                            │      ├─ exec_ns = {__builtins__, tools,    │
│                            │      │              final_answer}          │
│                            │      │                                     │
│                            │      ├─ exec(code, exec_ns)                │
│                            │      │     tool() → print() → Observation  │
│                            │      │                                     │
│                            │      └─ ExecutionResult(output, logs,      │
│                            │              is_final_answer, error)       │
│                            │                                            │
│                            ├─ if is_final_answer: return output ✅      │
│                            │                                            │
│                            └─ append Observation → continue             │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 模块依赖关系

```
types.py         ← 纯数据类型，被所有模块依赖
    ↑
tools.py         ← Tool 抽象，被 agent / react_agent / code_executor 依赖
    ↑
config.py        ← 配置文件加载，被 agent 依赖
    ↑
prompts.py        ← 系统提示词，被 react_agent 依赖
    ↑
model.py         ← 模型通信层，被 agent / react_agent 依赖
    ↑
code_executor.py  ← 代码执行引擎，被 react_agent 依赖
    ↑
react_agent.py   ← ReAct 循环编排，被 agent 依赖
    ↑
agent.py         ← 组合层，暴露公共 API（入口模块）

__init__.py      ← 导出所有公共 API
```

**关键设计规则**：依赖方向严格单向——`agent.py` 依赖所有其他模块，没有循环依赖。
每一层只依赖它下方的模块。

---

## 与 smolagents 方案的关键差异

| 维度 | smolagents 方案 | mini-agent 方案 |
|------|----------------|----------------|
| 代码执行 | 1768 行 AST 解释器（白名单） | ~110 行 exec()（无白名单） |
| 安全模型 | AST 分析 + BASE_PYTHON_TOOLS | 工具自身提供隔离（子进程） |
| Agent 循环 | ~800 行 CodeAgent | ~190 行 react_loop() |
| 系统提示词 | 174 行 YAML | ~30 行 Python 字符串 |
| 导入时间 | 2.8s | 0.71s |
| 依赖 | smolagents + httpx | 仅 httpx |
| Deep agent 步数 | 8 步未调用 final_answer | 2 步完成 |
