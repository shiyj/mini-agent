# mini-agent 设计规范指南

## 核心设计哲学

1. **从小模型出发。** 所有设计决策以 9B 级本地模型（如 Qwen2.5-Coder、Ornith-9B）的注意力特性为基准。提示词精简、无规划阶段、明确的停止条件。

2. **零外部 Agent 依赖。** 不依赖 smolagents、LangChain 等框架。调用 Ollama API、执行代码、管理 ReAct 循环全部自实现。仅 `httpx` 作为 HTTP 客户端。

3. **两极化场景自适应。** 极速流（单步代码生成，无执行）与深思流（完整 ReAct 循环，自动纠错）共享同一代码库，通过参数选择模式。

4. **模型由调用者创建并传入。** Agent 不接触模型配置——`config.json` 中只有提示词模板，模型参数由调用者决定。

---

## 模块职责划分

```
types.py         纯数据类型（无业务逻辑）
tools.py         工具抽象层（无业务逻辑）
code_executor.py 代码执行引擎（exec + 工具注入）
react_agent.py   ReAct 循环编排（提示词 → 解析 → 执行 → 观察 → 循环）
prompts.py       系统提示词定义（小模型优化）
model.py         模型通信层（httpx + SSE）
config.py        配置文件加载（prompts.json）
agent.py         组合层（组装工具 + 执行循环，暴露公共 API）
```

**依赖方向：** `agent.py` → 所有其他模块。`types.py` 和 `tools.py` 是叶子模块，无内部依赖。

---

## 两模式详细规范

### 🏎️ 极速流（Fast Agent）

**业务目标：** 简单代码生成、语法转换、代码模板。追求最快速度，不做执行验证。

**配置要点：**

| 参数 | 建议值 | 说明 |
|------|--------|------|
| `temperature` | 0.0 | 确定性输出，禁止废话 |
| 工具 | 无 | 纯生成，不涉及执行 |
| 步骤 | 单次模型调用 | 无循环 |
| Prefill | 默认开启 | 跳过 Qwen thinking 模式 |

**架构流程：**

```
调用者传入 model + prompt
  → 读取 fast_agent system_instruction（~12 词）
  → 可选：追加 assistant prefill 消息
  → model.generate(messages)
  → 返回原始输出
```

### 🧠 深思流（Deep Agent）

**业务目标：** 复杂脚本编写、调试、迭代任务。允许模型反复试错，用时间换质量。

**配置要点：**

| 参数 | 建议值 | 说明 |
|------|--------|------|
| `temperature` | 0.4~0.5 | 提供多样性试错思路 |
| `max_steps` | 8~15 | 充足的 ReAct 循环轮数 |
| `max_output_tokens` | ≥4096 | 复杂脚本需要更多 token |
| 工具 | write_file + run_python_file + execute_bash_command | 文件 + 执行 + shell |

**架构流程：**

```
react_loop(task, model, tools, max_steps):
  system_prompt = build_deep_system_prompt(tools)  # ~30 行
  messages = [system, user_task]

  for step in 1..max_steps:
    response = model.generate(messages, stop_sequences=["Observation:"])
    code = parse_code(response)               # 剥离 <think>，提取 ```python 块
    result = execute_code(code, tools)         # exec() + 工具 + final_answer 注入
    if result.is_final_answer:
      return result.output                     # 模型调用 final_answer → 结束
    messages.append(Observation)               # 继续循环
```

---

## 代码执行安全模型

弃用 smolagents 的 AST 白名单沙盒（1768 行，仍会漏掉 `bytearray` 等内置函数），
采用**执行-隔离分离**方案：

| 层级 | 机制 | 安全边界 |
|------|------|----------|
| 顶层代码 | `exec()` 注入 `write_file` 等工具 | 无限制——工具调用走子进程 |
| 文件执行 | `run_python_file()` → `subprocess.run(["python3", filename])` | 子进程隔离，30s 超时 |
| Shell 命令 | `execute_bash_command()` → `subprocess.run(command, shell=True)` | 用户自行控制 |

**设计原则：** 不在代码执行层做安全限制（白名单/黑名单），而是通过工具设计让模型
自然地将需要隔离的操作委托给子进程工具。模型直接 `exec()` 的代码能做任何事——
但不需要限制，因为重要的操作（文件 I/O、运行脚本、shell）都由工具完成。

---

## ReAct 循环关键设计

### 1. 提示词优化规则

- **极短系统提示词：** 极速流 12 词，深思流 ~30 行。小模型在长提示词中注意力易崩溃。
- **明确的 final_answer 触发：** "When the script works, call final_answer(result) IMMEDIATELY"
- **无规划阶段：** 小模型在多步规划中容易迷失，直接进入写代码 → 执行 → 修复循环。
- **工具使用示例：** 提示词中包含完整的 `write_file → run_python_file → final_answer` 代码示例。
- **防重复指令：** "Never repeat working code"（防止模型不断重跑同一个脚本）。

### 2. Code Parsing（三阶段回退）

```
1. 剥离 <think>...</think>（Qwen thinking 产物）
2. 匹配 ```python 代码块，合并多个块
3. 回退：匹配 ``` 泛代码块
4. 再回退：ast.parse() 直接解析未折叠文本
```

### 3. 停止序列

`stop_sequences = ["Observation:"]`——防止模型在同一轮输出中提前生成
下一轮的 Observation 前缀。此设计减少了 token 消耗和解析错误。

### 4. exec() 执行注意事项

| 已踩过的坑 | 解决方案 |
|-----------|----------|
| 分离 globals/locals 导致 `import` 在函数中不可见 | 单一 `exec_ns` 字典 |
| `final_answer` 赋值不跨作用域 | `list[Any]` 可变容器 |
| `exec()` 不捕获表达式返回值 | 工具自动 `print()` 返回值 |
| 连续调用 `final_answer()` 覆盖值 | `got_final_answer[0] = True` 后不再修改 |

---

## Prefill 技术细节

极速流的 prefill 机制是应对 Qwen 系模型思考模式的关键优化。

**技术原理：** 在消息列表末尾追加 `{"role": "assistant", "content": "```python\n"}`。
Ollama 的 Qwen 模型在收到 assistant 角色已有内容时，认为思考阶段已结束，
直接从代码块中间开始续写，完全跳过 `thinking` 字段生成。

**适用条件：**
- 仅适用于 Qwen 系模型（或有 thinking 模式的模型）
- 仅适用于极速流（深思流需要完整思考过程）
- 仅在预填代码块格式时有效（如 ` ```python\n `）

**实现细节：**
- 从 `config/prompts.json` 读取 `prefill` 和 `prefill_text`
- 运行时也可通过参数覆盖：`run_fast_agent(prompt, model, prefill=False)`
- 在 `generate()` 之前追加 assistant 消息
- 返回结果时如果 prefill 启用且有内容，拼接 `prefill_text + content`

---

## 开发注意事项

### 小模型调优

1. **提示词必须精简。** 小模型面对超 500 词的 system prompt 时注意力极易崩溃。
   极速流仅需 12 词，深思流约 30 行。

2. **用标准库替代第三方库。** 模型对 `os`、`sys`、`pathlib` 等标准库的记忆深度
   远超第三方冷门库，单次编写成功率更高。

3. **思考模式处理。** Qwen 系模型会在 `thinking` 字段中消耗大量 token，
   `parse_code()` 必须正确剥离这些块的残留。

4. **温度渐进。** 验证脚本中失败重试时温度从 0.45 逐渐上升，给模型更多多样性。

### 扩展指南

- **添加新工具：** 在 `agent.py` 中用 `@tool` 装饰器定义函数，自动获得输入 schema。
  工具会自动注入到 `run_deep_agent` 的执行环境。

- **新增模式：** 在 `agent.py` 中添加新函数，组合现有的 `model.generate()`、
  `execute_code()`、`react_loop()` 等原语。

- **切换模型提供商：** 实现与 `OllamaResponsesModel` 相同接口的类
  （`generate(messages, stop_sequences, ...)` 返回 `ChatMessage`），传入 agent。

### 常见陷阱

1. **exec() 命名空间：** 始终使用单一 `exec_ns` 字典同时作为 globals/locals，
   否则函数内部无法访问外部 `import` 的模块。

2. **final_answer 注入：** `final_answer` 不能在 exec 外部定义闭包变量直接赋值，
   需用可变容器（`list[Any]`）。因为 `exec()` 中的赋值操作默认在局部作用域。

3. **SSE 思考模式残留：** Ollama 流式传输可能只发送 `</think>`（开始标签
   在首帧中被消费）。`parse_code()` 必须处理"只有结束标签"的情况。

4. **工具自动打印：** `exec()` 不捕获表达式返回值，工具必须用 `print()`
   输出返回值才能出现在 Observation 中。
