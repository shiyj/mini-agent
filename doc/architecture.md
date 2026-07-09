# architecture.md — 整体架构与数据流

## 模块依赖图

```
types.py         ← 纯数据类型，被所有模块依赖
    ↑
tools.py         ← Tool 抽象，被 agent / react_agent / code_executor 依赖
    ↑
config.py        ← 配置加载，被 agent 依赖
    ↑
prompts.py       ← 系统提示词，被 react_agent 依赖
    ↑
model.py         ← 模型通信层，被 agent / react_agent 依赖
    ↑
code_executor.py ← 代码执行引擎，被 react_agent 依赖
    ↑
react_agent.py   ← ReAct 循环编排，被 agent 依赖
    ↑
agent.py         ← 组合层，暴露公共 API（入口模块）
```

**关键规则**：依赖方向严格单向——`agent.py` 依赖所有其他模块，无循环依赖。

---

## 两种模式数据流

### 极速流

```
run_fast_agent(prompt, model)
    │
    ├─ 1. load_prompts() → fast_agent.system_instruction
    ├─ 2. 构建 messages:
    │      [system, user, assistant(prefill="```python\n")]
    ├─ 3. model.generate(messages) → ChatMessage
    └─ 4. 返回 prefill_text + content
```

### 深思流

```
run_deep_agent(prompt, model, target_file, max_steps)
    │
    ├─ model.max_output_tokens = max(512, 4096)
    ├─ tools = [write_file, run_python_file, execute_bash_command]
    ├─ task = prompt + "IMPORTANT: Follow these steps..."
    └─ react_loop(task, model, tools, max_steps, target_file)
           │
           ├─ system_prompt = build_deep_system_prompt(tools, target_file)
           ├─ messages = [system, user_task]
           ├─ for step in 1..max_steps:
           │     ├─ model.generate(messages, stop_sequences=["Observation:"])
           │     ├─ parse_step(output) → [(lang, path, content), ...]
           │     ├─ write_blocks(blocks) → 所有文件先写入磁盘
           │     ├─ 取首个 lang in {python,py,bash,sh,shell} → exec_code
           │     ├─ execute_code(exec_code, tools) → ExecutionResult
           │     ├─ 错误恢复: E001/E002/E003/E004/E005/E006/E007/E008
           │     ├─ final_answer 检查 → 返回或继续
           │     └─ messages.append(Observation) → 继续循环
           └─ return final_answer 或 fallback
```

---

## 核心设计决策表

| 决策 | 方案 | 替代方案 | 理由 |
|------|------|----------|------|
| 代码执行 | `exec()` 单一命名空间 | AST 白名单 (smolagents 1768 行) | 无白名单误杀，导入可见，仅 120 行 |
| 安全边界 | 子进程工具 (`run_python_file`, `bash`) | 沙盒解释器 | 真正隔离在子进程，exec 层完全开放 |
| final_answer 跨作用域 | `list[Any]` 可变容器 | `nonlocal` 闭包 | exec 内赋值默认局部作用域，list 绕过 |
| 文件写入 | Markdown ````file:path` 零转义 | `write_file(content="...")` 字符串嵌套 | 9B 模型无法处理引号/转义嵌套 |
| 执行顺序 | 先写所有文件，再执行首个代码块 | 解析到 `write_file` 即执行 | 保证依赖文件就绪，模拟真实开发流程 |
| 停止序列 | `"Observation:"` | 无 / 自定义 | 防止模型在同一轮生成下一轮前缀 |
| 思考模式剥离 | `parse_step` 仅剥 `<|...|>` 特殊 token | smolagents 无此处理 | Ollama Qwen 系可能在输出插入掩码 token |
| 解析器唯一性 | 单一 `parse_step()` 服务活循环 + 日志分析 | 多套解析逻辑并存 | 删除 `parse_code()`，避免重复与不一致 |
| 系统提示词长度 | ~30 行 | 174 行 (smolagents) | 9B 模型注意力极易崩溃 |
| 错误恢复 | 运行时注入 user 消息 | 硬编码进系统提示词 | 系统提示词保持极短，按需注入 |

---

## ReAct 循环状态机

```
Step N
  │
  ├─ Model Generate
  │      │
  │      ├─ Parse Step → Blocks
  │      │       │
  │      │       └─ write_blocks() → All files on disk
  │      │
  │      └─ Execute First Executable Block
  │             │
  │             ├─ SyntaxError → E008 (max 3)
  │             ├─ final_answer?
  │             │       ├─ wrote_file? → RETURN ✅
  │             │       └─ E002 → Continue
  │             │
  │             ├─ Progress Check
  │             │       ├─ wrote_this_step → progress=0
  │             │       ├─ verified (run_python_file/bash) → progress=0 (iff wrote_file)
  │             │       ├─ final_answer → progress=0
  │             │       └─ else → progress++ → ≥6: E007
  │             │
  │             └─ Write Guard (step≥3, no write, no verify, no final)
  │                     ├─ var assignment → E004
  │                     ├─ read only → E003
  │                     └─ generic → E005
  │
  └─ Append Observation → Next Step
```

---

## 与 smolagents 对比

| 维度 | smolagents | mini-agent |
|------|------------|------------|
| 导入时间 | 2.8s | 0.71s (-75%) |
| 外部依赖 | smolagents + httpx | **仅 httpx** |
| 代码执行 | 1768 行 AST 解释器（白名单） | ~120 行 exec()（无白名单） |
| Agent 循环 | ~800 行 CodeAgent | ~200 行 react_loop() |
| 系统提示词 | 174 行 YAML | ~30 行 Python 字符串 |
| 沙盒限制 | `bytearray` 等被拦截 | **无限制** |
| Deep agent 步数 | 8 步常未调用 final_answer | **2-3 步完成** |
| 安全模型 | 白名单 + AST 分析 | **工具本身隔离（子进程）** |
| 参数校验 | 参数名必须 `argument`/`args` | **无此限制** |
| 文件写入 | `write_file(content="...")` 字符串嵌套 | **````file:path` 零转义** |
| 导入可见性 | 分离 globals/locals 导致 import 不可见 | **单一 exec_ns 解决** |

---

## 关键文件尺寸

| 文件 | 行数 | 说明 |
|------|------|------|
| `types.py` | ~80 | 纯数据类型 |
| `tools.py` | ~120 | Tool 抽象 + 3 内置工具 |
| `code_executor.py` | ~120 | exec() 执行引擎 |
| `react_agent.py` | ~530 | ReAct 循环核心 |
| `prompts.py` | ~180 | 系统提示词 + 错误码 |
| `model.py` | ~280 | ResponsesModel |
| `config.py` | ~60 | 配置加载 |
| `agent.py` | ~320 | 组合层 + 公共 API |
| **总计** | **~1700** | vs smolagents 3000+ |

---

## 安全模型详解

```
┌─────────────────────────────────────────────────────────────┐
│                      Agent 进程                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ exec_ns = {__builtins__, tools, final_answer}       │    │
│  │ exec(code, exec_ns)  ← 完全开放，无白名单            │    │
│  └─────────────────────────────────────────────────────┘    │
│           │                    │                    │        │
│           ▼                    ▼                    ▼        │
│    write_file()         run_python_file()       bash()      │
│    (进程内 I/O)         (子进程 python3)      (子进程 sh)   │
└─────────────────────────────────────────────────────────────┘
```

- **exec 层**：模型生成的代码可调用任何 Python 内置、导入任意库
- **工具层**：真正的副作用（文件写入、脚本执行、Shell）通过子进程隔离
- **安全边界在工具**，而非 exec 沙盒

---

## 扩展点

| 扩展类型 | 方式 |
|----------|------|
| 新工具 | `@tool` 装饰函数 → 传入 `run_deep_agent(tools=[...])` |
| 新模式 | `agent.py` 中组合 `model.generate()`、`execute_code()`、`react_loop()` |
| 新模型商 | 实现 `generate(messages, stop_sequences)` 返回 `ChatMessage` |
| 新压缩器 | 实现 `compress(messages, model)` 传入 `react_loop(compressor=...)` |