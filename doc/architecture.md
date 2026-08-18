# architecture.md — 整体架构与数据流

## 模块依赖图

```
types.py         ← 纯数据类型，被所有模块依赖
    ↑
tools.py         ← Tool 抽象，被 agent / react_agent / code_executor 依赖
    ↑
config.py        ← 配置加载，被 agent 依赖
    ↑
prompts.py       ← 系统提示词，被 agent 依赖
    ↑
model.py         ← 模型通信层，被 agent / react_agent 依赖
    ↑
code_executor.py ← 代码执行引擎，被 react_agent 依赖
    ↑
react_agent.py   ← ReAct 循环编排，被 agent 依赖
    ↑
agent.py         ← 组合层，暴露公共 API（入口模块）
```

**关键规则**：依赖方向严格单向——`agent.py` 依赖所有其他模块，无循环依赖。`hashline.py` 已删除（哈希锚点编辑引擎整块移除）。

---

## 两种模式数据流

### 极速流

```
run_fast_agent(prompt, model)
    │
    ├─ 1. 使用 FAST_AGENT_SYSTEM（12 词）作为系统提示词
    ├─ 2. 构建 messages:
    │      [system, user, assistant(prefill="```python\n")]
    ├─ 3. model.generate(messages) → ChatMessage
    └─ 4. 返回 prefill_text + content
```

### 深思流

```
run_deep_agent(prompt, model, max_steps)
    │
    ├─ tools = [read_file, run_python_file]
    ├─ task = prompt（原样传入，不追加固定步骤）
    └─ react_loop(task, model, tools, max_steps)
           │
           ├─ system_prompt = build_deep_system_prompt(tools)   # ~34 行
           ├─ messages = [system, user_task]
           ├─ for step in 1..max_steps:
           │     ├─ model.generate(messages, stop_sequences=["[Result]"])
             │     ├─ parse_step(output) → [(lang, path, content), ...]
             │     ├─ write_blocks(blocks) → ```file:path 整文件重写落盘
             │     ├─ 取首个 lang in {python,py} → exec_code
             │     ├─ execute_code(exec_code, tools) → ExecutionResult
             │     ├─ 错误恢复: E001–E004
           │     ├─ final_answer 检查 → 返回或继续
           │     └─ messages.append("[Result]") → 继续循环
           └─ return final_answer 或 fallback
```

---

## 核心设计决策表

| 决策 | 方案 | 理由 |
|------|------|------|
| 代码执行 | `exec()` 单一命名空间 | 无白名单误杀，导入可见，仅 ~120 行 |
| 安全边界 | 子进程工具 (`run_python_file`) | 真正隔离在子进程，exec 层完全开放 |
| final_answer 跨作用域 | `list[Any]` 可变容器 | exec 内赋值默认局部作用域，list 绕过 |
| 文件写入/编辑 | Markdown `````file:path``` 零转义整文件重写 | 9B 模型无法处理引号/转义嵌套，也不擅长抄哈希锚点做行级编辑 |
| 执行顺序 | 先写所有文件，再执行首个代码块 | 保证依赖文件就绪，模拟真实开发流程 |
| 停止序列 | `"Observation:"` | 防止模型在同一轮生成下一轮前缀 |
| 解析器唯一性 | 单一 `parse_step()` 服务循环 | 避免重复与不一致 |
| 系统提示词长度 | ~34 行 | 9B 模型注意力极易崩溃 |
| 错误恢复 | 运行时注入 user 消息（4 个错误码） | 系统提示词保持极短，按需注入 |

---

## ReAct 循环状态机

```
Step N
  │
  ├─ Model Generate
  │      │
  │      ├─ Loop detection: output == 上一步 → 提示改用 ```file:path 重写
  │      │
  │      ├─ Parse Step → Blocks
  │      │       │
  │      │       ├─ write_blocks() → ```file:path 整文件落盘
  │      │       └─ 无路径/用了 edit: → 提示改用 ```file:path
  │      │
  │      └─ Execute First Executable Block (python)
  │             │
  │             ├─ SyntaxError → 提示 ```file:path 零转义写入（最多 3 次）
  │             ├─ final_answer?
  │             │       ├─ 上一步报错? → E003 → Continue
  │             │       ├─ 无工具且未写文件? → E002 → Continue
  │             │       └─ 通过 → RETURN ✅
  │             │
  │             └─ Progress Check
  │                     ├─ wrote_target(写了文件) or final_answer → progress=0
  │                     └─ else → progress++ → ≥6: 停止
  │
  └─ Append [Result] → Next Step
```

---

## 关键文件尺寸

| 文件 | 行数 | 说明 |
|------|------|------|
| `types.py` | ~101 | 纯数据类型 |
| `tools.py` | ~69 | Tool 抽象 + 2 内置工具 |
| `code_executor.py` | ~128 | exec() 执行引擎 |
| `react_agent.py` | ~552 | ReAct 循环核心 + 整文件重写 |
| `prompts.py` | ~119 | 系统提示词 + 4 个错误码 |
| `model.py` | ~313 | ResponsesModel |
| `context.py` | ~160 | 上下文压缩器（可选） |
| `config.py` | ~151 | 配置加载 |
| `agent.py` | ~277 | 组合层 + 公共 API |
| **总计** | **~1910** | |

> 精简后比旧版（含 `hashline.py`）减少约 640 行；`hashline.py` 与 `test_hashline.py` 已删除。

---

## 安全模型详解

```
┌─────────────────────────────────────────────────────────────┐
│                      Agent 进程                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ exec_ns = {__builtins__, tools, final_answer}       │    │
│  │ exec(code, exec_ns)  ← 完全开放，无白名单            │    │
│  └─────────────────────────────────────────────────────┘    │
│           │                    │                            │
│           ▼                    ▼                            │
│    file: blocks（进程内 I/O）  run_python_file()            │
│                                (子进程 python3)             │
└─────────────────────────────────────────────────────────────┘
```

- **exec 层**：模型生成的代码可调用任何 Python 内置、导入任意库
- **工具层**：真正的副作用（文件写入、脚本执行）通过工具/子进程隔离
- **安全边界在工具**，而非 exec 沙盒

---

## 扩展点

| 扩展类型 | 方式 |
|----------|------|
| 新工具 | `@tool` 装饰函数 → 传给 `react_loop(tools=[...])` |
| 新模式 | `agent.py` 中组合 `model.generate()`、`code_executor.execute_code()`、`react_loop()` |
| 新模型商 | 实现 `generate(messages, stop_sequences)` 返回 `ChatMessage` |
| 新压缩器 | 实现 `compress(messages, model)` 传入 `react_loop(compressor=...)` |