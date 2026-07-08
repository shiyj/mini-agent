# react_loop 处理逻辑全解析

本文档逐阶段剖析 `react_agent.py` 中 `react_loop()` 函数的处理逻辑，涵盖代码解析、循环检测、进度守卫、观察截断、上下文压缩等所有机制。错误码 E001–E007 的定义见 `prompts.py` 中的 `ERROR_PROMPTS` 字典，各阶段通过 `format_error(code, target_file=..., var=...)` 注入。

---

## 整体流程

```
system_prompt + task
  │
  ▼
┌───────────────────────────────────────────────────────────────────┐
│                   对于 step in 1..max_steps:                       │
│                                                                   │
│  0. 上下文压缩 (可选)                                              │
│  0a. Token 阈值压缩 (可选)                                         │
│                                                                   │
│  1. 模型调用 → response                                           │
│     get_clean_message_list + flatten + model.generate              │
│     stop_sequences=["Observation:"]                                │
│                                                                   │
│  2. 输出去重 → 追加到 recent_outputs                                │
│     2a. 循环检测: 连续 3 次相同输出 → E006 警告                    │
│         连续 3 次 E006 → return E006                               │
│                                                                   │
│  3. parse_code(output) → 提取代码块                                │
│     解析失败 → E001 → continue                                     │
│                                                                   │
│  4. execute_code(code, tools) → ExecutionResult                    │
│     │                                                             │
│     ├─ wrote_file 追踪 (检查 result.logs 中 "Written")             │
│     │                                                             │
│     4a. 无进度守卫: 6 步无 write/verify/final → E007 → return      │
│                                                                   │
│  5. 构建 Observation → _truncate_observation                      │
│                                                                   │
│  6. final_answer 检查:                                            │
│     ├─ final_answer 但未写过文件 → E002 → continue                 │
│     └─ final_answer + 已写文件 → return output ✅                  │
│                                                                   │
│  7. 写入守卫 (step >= 3):                                         │
│     ├─ 代码写了变数但未写文件 → E004 (带变数名)                    │
│     ├─ 只读文件 → E003                                            │
│     └─ 通用无写入 → E005                                          │
│     → continue                                                     │
│                                                                   │
│  8. 追加到 messages → 继续循环                                     │
└───────────────────────────────────────────────────────────────────┘
  │
  ▼
Max steps reached → 返回最后一条 Observation 或 fallback 消息
```

---

## 阶段 0：上下文压缩

### 0. 按消息数压缩

```python
if compressor is not None:
    messages = compressor.compress(messages, model)
```

`ContextCompressor` (位于 `context.py`) 在消息数量超限时，将早期的 assistant+user 轮次合并为摘要。仅当指定了 `compressor` 参数时启用。

### 0a. 按 Token 阈值压缩

```python
if compressor is not None and hasattr(model, 'context_window') and hasattr(model, 'max_output_tokens'):
    total_chars = sum(len(msg.get('content', '')) for msg in messages)
    estimated_tokens = total_chars // 4
    max_input_tokens = model.context_window - model.max_output_tokens
    if estimated_tokens > max_input_tokens * 0.85:
        # 强制压缩
        messages = compressor.compress(messages, model)
```

基于字符数估算 token 数（~4 字符/token），在超过 `context_window - max_output_tokens` 的 85% 时触发强制压缩。此机制防止模型在长对话中因上下文超限而崩溃。

压缩使用 `CompressionTool` 作为子调用，将早期消息用 LLM 摘要替代。

---

## 阶段 1：模型调用

```python
cleaned = get_clean_message_list(messages, flatten_messages_as_text=True)
# 展平 content 列表 -> 纯文本
api_messages = [{"role": m["role"], "content": plain_text}]
response = model.generate(api_messages, stop_sequences=stop_sequences)
output = remove_content_after_stop_sequences(output, stop_sequences) or output
```

- `get_clean_message_list`：合并连续同角色消息、展平 content 列表。
- `stop_sequences=["Observation:"]`：防止模型在同一轮输出中提前生成下一轮的 "Observation:" 前缀。
- `remove_content_after_stop_sequences`：截断第一个 stop sequence 之后的内容。

---

## 阶段 2：循环检测 (E006)

```python
recent_outputs.append(output)
if len(recent_outputs) >= 3 and recent_outputs[-1] == recent_outputs[-2] == recent_outputs[-3]:
    consecutive_loops += 1
    if consecutive_loops >= 3:
        return prompts.format_error(prompts.E006_LOOP, target_file=target_file)
    messages.append({"role": "user", "content": prompts.format_error(prompts.E006_LOOP, target_file=target_file)})
    continue
else:
    consecutive_loops = 0
```

**判定条件：** 连续 3 轮模型输出字符串完全一致（`==` 比较）。

**处理流程：**
1. 第 1 次命中（第 3 次相同输出）：注入 E006 用户消息警告，`continue`。
2. 第 2 次命中（第 6 次相同输出）：再次注入 E006 警告。
3. 第 3 次命中（第 9 次相同输出）：不再警告，直接返回 E006 错误消息终止循环。

E006 恢复提示词要求模型停止探索，立即 `write_file → run_python_file → final_answer`。

> **设计意图：** 模型有时会陷入"不断打印同一个值、不断思考但不出代码"的死循环。3 次警告给模型纠正机会，3 次后硬止损。

---

## 阶段 3：代码解析 (parse_code)

```python
code = parse_code(output)
if code is None:
    messages.append({"role": "user", "content": prompts.format_error(prompts.E001_NO_CODE)})
    continue
```

`parse_code()` 的完整处理流程：

### 3.1 特殊 Token 剥离

```python
# 1. 剥离 <|mask_start|>...<|mask_end|> (Ollama 掩码 token)
text = re.sub(r'<\|mask_start\|>.*?<\|mask_end\|>', '', text, flags=re.DOTALL)
# 2. 剥离所有 <|...|> token
text = re.sub(r'<\|.*?\|>', '', text)
```

Ollama 的 Qwen 系模型有时会在输出中插入特殊掩码 token（`<|mask_start|>`、`<|im_end|>` 等），这些 token 不是有效代码，必须预先清除。

### 3.2 思考标签剥离

```python
# 3. 剥离 <think>...</think> (Qwen thinking 模式产物)
text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
# 4. 清理残留的单独标签
text = text.replace('</think>', '').replace('<think>', '')
```

Qwen 系模型（如 Ornith-9B）有原生思考模式，在 `content` 字段中输出 `<think>...</think>` 内部的推理过程。另外在 SSE 流式传输中，首帧可能已经消费了 `<think>` 开始标签，只留下 `</think>` 结束标签。

此处分两步清理：
- `re.sub(r'<think>.*?</think>', ...)`：剥离完整的 `<think>...</think>` 块。
- `.replace('</think>', '').replace('<think>', '')`：清理流式传输后残留的单独 `</think>` 或 `<think>` 标签。

### 3.3 代码块提取（四阶段回退）

```
1. ```python / ```py 块
   → re.findall(r"```(?:python|py)\s*\n(.*?)```", text, re.DOTALL)
   → 多个块用 \n\n 连接

1b. 未闭合的 ```python 块
   → re.search(r"```(?:python|py)\s*\n(.+)", text, re.DOTALL)
   → 模型因 token 上限被截断时兜底

2. ```bash / ```sh / ```shell 块
   → 转换为 execute_bash_command(repr(content)) 调用
   → 多个块用 \n\n 连接

2b. 未闭合的 ```bash 块

3. ``` (通用) 块
   → 当作 Python 代码

3b. 未闭合的 ``` 通用块

4. 裸代码 (无围栏)
   → ast.parse(text) 测试是否合法 Python
   → 合法则返回，否则返回 None → E001
```

> **设计原则：** 只要模型输出了可识别的代码块就执行，尽量不丢弃输出。只有完全无法解析时才注入 E001 恢复提示。

---

## 阶段 4：代码执行

```python
result = execute_code(code, tools)
```

内部流程 (位于 `code_executor.py`)：

1. 构建执行命名空间 `exec_ns = {__builtins__, tools, final_answer}`
2. `exec(compile(code, "<agent_code>", "exec"), exec_ns)`
3. 捕获 stdout
4. 检测 `got_final_answer[0]` 判断是否调用了 `final_answer()`
5. 返回 `ExecutionResult(output, logs, is_final_answer, error)`

### wrote_file 追踪

```python
if not result.error and result.logs and "Written" in result.logs:
    wrote_file = True
```

仅在 `write_file()` 实际执行成功（返回包含 "Written" 的日志）时才标记 `wrote_file = True`。仅仅在代码中提及 `write_file`（如在注释或未调用的函数定义中）不会触发此标记。

### 阶段 4a：无进度守卫 (E007)

```python
verified = "run_python_file(" in code or "execute_bash_command(" in code
made_progress = wrote_file or result.is_final_answer
if verified and not wrote_file:
    made_progress = False
if made_progress:
    no_progress = 0
else:
    no_progress += 1
if no_progress >= 6:
    return prompts.format_error(prompts.E007_NO_PROGRESS, target_file=target_file)
```

**判定条件：** 连续 6 步没有实际进度（没有写文件、没有验证、没有最终答案）。

**进度定义：**
| 行为 | 是否算进度 | 条件 |
|------|-----------|------|
| `write_file() ` 成功执行 | ✅ | `result.logs` 含 "Written" |
| `final_answer()` 被调用 | ✅ | `result.is_final_answer == True` |
| `run_python_file()` 或 `execute_bash_command()` | ❌ | 仅在已写文件后才算进度 |
| 仅打印/计算/定义函数 | ❌ | 原始代码执行，未保存到文件 |

**验证（verify）的特殊处理：** `run_python_file()` 和 `execute_bash_command()` 只有在 **已经写过文件** (`wrote_file == True`) 时才会计入进度。如果模型在没有写文件的情况下重复验证同一个有 bug 的文件，不算进度。

> **设计意图：** 阻止模型陷入"定义函数 → 打印 → 再定义 → 再打印"的死循环，强迫模型最终执行 `write_file` 持久化代码。

---

## 阶段 5：观察截断

```python
if result.error:
    observation = f"Execution logs:\n{result.logs}\nError:\n{result.error}"
else:
    observation = f"Execution logs:\n{result.logs}"
    if result.output is not None:
        observation += f"\nLast output: {result.output}"

if max_observation_length > 0:
    observation = _truncate_observation(observation, max_observation_length)
```

`_truncate_observation` 实现：

```python
def _truncate_observation(text, max_length):
    if max_length <= 0 or len(text) <= max_length:
        return text
    half = max_length // 2
    return text[:half] + f"\n... [truncated {len(text) - max_length} chars] ...\n" + text[-half:]
```

保留头尾各一半字符，中间截断。默认 `max_observation_length = 2000`，可通过 `react_loop()` 参数配置。设为 `0` 可禁用截断。

> **设计意图：** 防止超长输出（如打印大数据集）撑爆上下文窗口。截断后模型仍能看到输出的头部和尾部，保留关键信息。

---

## 阶段 6：final_answer 守卫 (E002)

```python
if result.is_final_answer:
    if not wrote_file:
        messages.append({"role": "user", "content": prompts.format_error(prompts.E002_FINAL_NO_WRITE, target_file=target_file)})
        continue
    return str(result.output) if result.output is not None else result.logs
```

**守卫条件：** 模型调用了 `final_answer()`，但在此之前从未成功调用过 `write_file()`。

**处理流程：**
1. 注入 E002 恢复提示，明确告诉模型：文件还在磁盘上，和你执行前的状态一样。
2. `continue`，不终止循环。

> **设计意图：** 防止模型"修复"代码后直接 `final_answer`，但修复只在 `exec()` 的临时命名空间中生效，磁盘上的原始文件未被修改。E002 强制模型先 `write_file()` 持久化修复结果。

---

## 阶段 7：写入守卫 (E003/E004/E005)

```python
if (not result.error
        and "write_file(" not in code
        and "run_python_file(" not in code
        and "execute_bash_command(" not in code
        and step >= 3):
```

**触发条件（全部满足）：**
1. 代码执行成功（无错误）
2. 代码中不包含 `write_file(` 调用
3. 代码中不包含 `run_python_file(` 或 `execute_bash_command(` 调用（验证步骤免检）
4. `step >= 3`（前 2 轮允许探索性代码）

**三种细分类别：**

| 错误码 | 判定条件 | 提示内容 |
|--------|---------|---------|
| **E004** | 代码将修复内容赋值给一个变数（如 `CODE = """..."""`），且赋值内容包含 `def`/`import`/`class`/`return`/`print(` 等代码特征 | 告诉模型具体变数名，建议 `write_file(filename='{target_file}', content={var})` |
| **E003** | 代码执行了 `open()`/`.read()`/`read()` 等读取操作，但没有任何写入 | 提示模型只读了文件，必须用 write_file 写入 |
| **E005** | 以上都不匹配（通用无写入） | 通用提示：运行代码不保存文件无法修复磁盘上的文件 |

E004 的变数检测逻辑：

```python
var = None
m = re.search(r'(\w+)\s*=\s*("""|\'\'\')', code)
if m:
    val = code[m.end():m.end() + 300]
    if re.search(r'\b(def |import |class |return |print\()', val):
        var = m.group(1)
```

先搜索 `变数名 = """` 或 `变数名 = '''` 模式，然后检查赋值内容的前 300 字符是否包含代码特征。如果是，将变数名注入 E004 的 `{var}` 占位符。

> **设计意图：** 9B 模型常犯的错误是将修正内容赋给一个变数（`FIXED_CODE = """..."""`），然后直接 `final_answer` 或只 `print`——从不调用 `write_file()`。E004 直接告诉模型该变数的名字，把"用什么内容写哪个文件"这句话喂给模型，极大提高后续 step 的成功率。

---

## 阶段 8：追加到对话历史

```python
messages.append({"role": "assistant", "content": output})
messages.append({"role": "user", "content": f"Observation:\n{observation}"})
```

将本轮模型的输出和观察结果追加到 `messages` 列表，供下一轮 `model.generate()` 使用。

---

## 最大步数耗尽

```python
# Max steps reached
if messages and messages[-1].get("role") == "user":
    last_obs = messages[-1]["content"]
    if "Execution logs:\n" in last_obs:
        return last_obs.split("Execution logs:\n", 1)[1]
return "Max steps reached without final answer"
```

当 `for` 循环自然结束（`max_steps` 耗尽）时，尝试从最后一条 Observation 中提取执行日志作为最终答案。如果最后一条消息不是 user 消息或没有执行日志，返回默认 fallback 字符串。

---

## 错误码速查表

| 错误码 | 触发阶段 | 触发条件 | 处理方式 |
|--------|---------|---------|---------|
| E001 | 阶段 3 | `parse_code()` 返回 `None` | 注入用户消息，`continue` |
| E002 | 阶段 6 | `final_answer()` + 未写文件 | 注入用户消息，`continue` |
| E003 | 阶段 7 | 代码只读文件（step ≥ 3） | 注入用户消息，`continue` |
| E004 | 阶段 7 | 代码赋值修复内容到变数但未写文件 | 注入用户消息（含变数名），`continue` |
| E005 | 阶段 7 | 代码无写入动作（通用） | 注入用户消息，`continue` |
| E006 | 阶段 2a | 连续 3 次相同输出 | 前 2 次注入用户消息；第 3 次直接 `return` |
| E007 | 阶段 4a | 连续 6 步无进度 | 直接 `return` |

---

## target_file 参数

`target_file` 在 `react_loop()` 签名中定义为：

```python
def react_loop(..., target_file: str = "output.py") -> str:
```

此参数的作用是让错误恢复提示（E002、E004、E006、E007）能够指名道姓地告诉模型应该写入哪个文件。例如 E004 的提示为：

> Warning E004: You assigned the corrected code to the variable `{var}` but never saved it. Call write_file(filename='**{target_file}**', content={var}) right now to persist the fix.

保持 `target_file` 在函数参数中（而不是硬编码在系统提示词中）的原因：
1. **系统提示词保持极短**（`build_deep_system_prompt` 的 `target_file` 参数仅用于工作流示例）。
2. **不同调用者可指定不同目标文件**，由 `agent.py` 中的 `run_deep_agent_with_config` 传入。
3. **`react_loop` 负责注入恢复提示**，它需要知道目标文件名。
