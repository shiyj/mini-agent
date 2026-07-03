# mini-agent 实现文档

## 概述

mini-agent 是一个基于 Hugging Face `smolagents` 库的本地轻量级编程 Agent，
通过 `OllamaResponsesModel`（基于 httpx 调用 OpenAI Responses API）对接本地 Ollama 模型（如 ornith:9b），
提供**极速流**和**深思流**两种 ReAct 模式。

设计哲学：
- **模型由调用者传入** — Agent 不依赖 `config.json`，不接触模型配置
- **配置与流程分离** — 提示词模板在 `config/prompts.json` 中集中管理
- **ReAct 架构** — 两种模式在 reasoning + acting 的深度上有本质区别

---

## 项目结构

```
mini_agent/
├── config/
│   └── prompts.json             # 所有提示词模板（唯一配置文件）
├── doc/
│   └── implementation.md        # 本文档
├── examples/
│   ├── basic_usage.py           # 最简使用：创建模型 + 运行 fast agent
│   ├── fast_vs_deep.py          # 极速流 vs 深思流 对比
│   └── custom_model.py          # 手动构造 LiteLLMModel 传入
├── src/
│   └── mini_agent/
│       ├── __init__.py          # 公共 API 导出
│       ├── config.py            # 提示词加载器（无模型配置）
│       ├── model.py             # OllamaResponsesModel 实现
│       ├── sandbox_patch.py     # 沙盒补丁
│       └── agent.py             # ReAct Agent 实现
├── verify/
│   └── verify.py                # 端到端验证脚本
├── pyproject.toml               # 项目元数据与依赖
└── README.md                    # 使用说明
```

---

## 配置文件

`config/prompts.json` 是唯一配置文件，包含两种模式的所有提示词模板，
支持 `{prompt}`、`target_file}` 等占位符由 Python 在运行时填入。

---

## 核心模块

### `src/mini_agent/config.py`

- `load_prompts()`: 读取 `config/prompts.json`，返回提示词模板字典
- `build_prompt(section, **kwargs)`: 加载模板并填入参数
- 使用模块级缓存，避免重复读取磁盘
- ⚠ 不再包含 `OllamaConfig` / `load_config` 等模型相关代码

### `src/mini_agent/model.py`

自定义的 `OllamaResponsesModel` 类，继承自 smolagents 的 `Model`：

- 使用 `httpx` 直接调用 Ollama 的 `/v1/responses` 端点
- 支持 SSE 流式传输，首 Token 到达约 0.3 秒
- 从响应中提取 `output_text` 内容
- 支持工具调用（通过 Responses API `tools` 参数）
- 支持 `stop_sequences` 和 `max_output_tokens`

### `src/mini_agent/sandbox_patch.py`

smolagents 沙盒的 monkey-patch 补丁：

- **黑名单机制**：允许所有 Python 内置函数，仅封禁危险函数
- **文件夹保护**：可选限制 `open()` 只能访问指定目录

### `src/mini_agent/agent.py`

**模型工厂（可选）**

- `get_local_model(model_name, api_base, temperature, max_output_tokens)` → `OllamaResponsesModel`
  用户可完全跳过此函数，直接用 `OllamaResponsesModel(...)` 构造。

**三个工具函数：**

| 工具 | 说明 |
|------|------|
| `write_file(filename, content)` | 将字符串写入文件 |
| `run_python_file(filename)` | 执行 Python 文件并捕获输出 |
| `execute_bash_command(command)` | 执行任意 shell 命令 |

**两种 ReAct 模式：**

| | 极速流 | 深思流 |
|---|---|---|
| 函数 | `run_fast_agent(prompt, model)` | `run_deep_agent(prompt, model, target_file, max_steps=8)` |
| 本质 | 单次推理，无 Acting | 完整 ReAct 循环 |
| 工具 | 无 | write_file + run_python_file + execute_bash_command |
| 温度建议 | 0.0（确定性输出） | 0.4~0.5（多样性试错） |
| 适用场景 | 简单生成、语法转换 | 复杂脚本、调试、迭代任务 |

**架构对比：**

```
Fast Agent (极速流):
  Reason (单步思考) → 输出代码
  └── 本质是纯生成，不涉及 Acting 步骤

Deep Agent (深思流):
  Reason → Act (写文件) → Observe (执行+读结果) → Reason → Act → ...
  └── 完整的 ReAct：思考 → 行动 → 观察 → 再思考
```

两者的区别不只是提示词不同——deep agent 持有执行工具、有更多的 `max_steps` 来允许多轮 Reasoning + Acting 循环，而 fast agent 只有一步推理。

---

## Prefill 机制（极速流）

### 问题

ornith:9b（Qwen 系）等模型有 Ollama 原生的「思考模式」。
收到代码生成请求时，模型会把推理过程写入 `message.thinking` 字段，
在 `message.content` 中输出最终代码。但如果任务稍复杂（如 "Fibonacci one-liner"），
模型会在 thinking 中反复试错多个方案，消耗 2000-4000 token 仍未输出 content。

实测数据（同一 prompt, temperature=0.0, num_predict=400）：

| 任务 | thinking 长度 | content | eval_tokens | 耗时 |
|------|--------------|---------|-------------|------|
| sum 1 to 100 | 258B | ✅ 100B | 128 | 5.9s |
| Fibonacci one-liner | 1138B | ❌ 0B | 400（截断） | 10.7s |
| Fibonacci one-liner (无限制) | 13326B | ❌ 0B | 4058 | 79s |

### 根因

模型在 thinking 中写了 4 个不同方案，每个都自我否定：
```
让我想个 one-liner...
方案A → 太复杂了
等等，换方案B → __setitem__ 返回 None，不行
再试 lambda → 不对
用 walrus operator → （被 token 限制截断）
```
token 用完后 content 仍为空。

### 解决方案：Assistant Prefill

在消息列表末尾追加一条 assistant 消息，预填代码块开头：

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

7 个任务的完整测试结果：

| 任务 | 无 prefill | 有 prefill |
|------|-----------|-----------|
| Fibonacci one-liner | ❌ 10.3s / 500t | ✅ 1.3s / 48t |
| Palindrome 函数 | ✅ 2.1s / 89t | ✅ 1.7s / 69t |
| CSV 读取脚本 | ✅ 8.0s / 385t | ✅ 2.1s / 90t |
| 反转链表 | ✅ 8.6s / 414t | ✅ 7.1s / 338t |
| 下载网页提取链接 | ✅ 9.1s / 435t | ✅ 3.0s / 134t |
| 合并有序链表 | ✅ 6.3s / 297t | ✅ 5.3s / 250t |
| sum 1 to 100 | ✅ 1.9s / 81t | ✅ 0.8s / 20t |

Prefill 在所有任务上都成功，且 token 数减少 50-90%，耗时显著降低。

### 配置方式

`config/prompts.json` 中 `fast_agent` 段新增两个配置项：

```json
{
  "fast_agent": {
    "system_instruction": "...",
    "prefill": true,
    "prefill_text": "```python\n"
  }
}
```

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `prefill` | bool | `true` | 是否启用 assistant prefill |
| `prefill_text` | string | `` ```python\n `` | 预填到 assistant 消息的文本 |

也可在调用时通过参数覆盖：`run_fast_agent(prompt, model, prefill=False)`。

---

## 沙盒配置（Sandbox Patch）

### 背景

smolagents 的 `LocalPythonExecutor` 对 Python 内置函数使用**白名单机制**。
`BASE_PYTHON_TOOLS` 中的函数可被模型直接调用，而其他内置函数（如 `bytearray`, `bytes`, `memoryview` 等）则会被阻止。

当模型尝试调用 `bytearray(1000)` 时，会触发：

```
InterpreterError: Forbidden function evaluation: 'bytearray' is not among the explicitly allowed tools or defined/imported in the preceding code
```

> `additional_authorized_imports` 参数只控制 `import` 语句，**不影响函数调用**。

### 解决方案：黑名单机制 + 文件夹保护

`src/mini_agent/sandbox_patch.py` 通过 monkey-patch 方式修改 smolagents 的沙盒行为：

1. **黑名单机制**：允许所有 Python 内置函数，仅封禁危险函数
2. **文件夹保护**：可选限制 `open()` 只能访问指定目录

### 配置选项

```python
# mini_agent/sandbox_patch.py

# 封禁的内置函数（代码执行、导入绕过等）
FORBIDDEN_BUILTINS: set[str] = {
    "eval", "exec", "compile", "__import__",
    "breakpoint", "help", "input",
    "globals", "locals", "vars", "dir",
    "getattr", "setattr", "delattr", "hasattr",
    "type", "property", "super",
}

# 文件夹保护：open() 只能访问该目录
# 设置为 None 表示不限制
OPEN_FOLDER_PROTECTION: str | None = None
```

### 工作原理

当模型执行 `bytearray(1000)`：

1. `evaluate_call` 检查：是否在 `state`/`static_tools`/`custom_tools`/`ERRORS` 中？否
2. 是 Python 内置函数？是
3. 在黑名单中？否
4. → 将 `bytearray` 添加到 `state`，允许调用

当 `open("file.txt")` 且启用了文件夹保护：

1. 内置函数检查通过
2. `open` 解析为 `_safe_open` 包装函数
3. 包装函数检查路径是否在 `OPEN_FOLDER_PROTECTION` 范围内
4. 通过 → 调用真实的 `builtins.open`

### 验证效果

**修改前**：
```
InterpreterError: Forbidden function evaluation: 'bytearray'
```

**修改后**：
```
Step 1: 成功执行 bytearray(1000) → 返回 bytearray(b'\x00...')
```

---

## 验证流程

验证脚本位于 `verify/verify.py`，执行三步检查：

1. **Prompt 加载**: 确认 `prompts.json` 正确读取，模板渲染正常
2. **模型连通性**: 调用 Ollama 模型返回一条简短回复
3. **Deep Agent — 循环优化**: 让 Agent 编写质数筛法对比脚本并自动纠错

第三步验证 Agent 的"生成 → 执行 → 修复"闭环能力，
任务要求编写**埃拉托色尼筛法**和**轮式优化筛法**两种实现，对比性能并验证结果一致。

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
| `custom_model.py` | 跳过 `get_local_model`，直接构造 `OllamaResponsesModel` |

---

## 依赖

- Python >= 3.10
- `smolagents` >= 1.26.0
- `httpx` >= 0.27.0
- 本地 Ollama 服务 + 可用模型