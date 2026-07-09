# react_agent.py — ReAct 循环核心

## 概述

约 200 行实现完整 Thought → Code → Observation 循环。

---

## 核心函数

### `react_loop(...) -> str`

```python
def react_loop(
    system_prompt: str,
    task: str,
    model: ResponsesModel,
    tools: list[Tool],
    max_steps: int = 8,
    max_observation_length: int = 2000,
    verbose: bool = True,
    compressor: ContextCompressor | None = None,
    target_file: str = "output.py",
) -> str:
```

返回最终答案字符串，或错误消息。

---

## 循环流程

```
for step in 1..max_steps:
  0. 上下文压缩（可选）
  0a. Token 阈值压缩（可选）

  1. 模型调用
     response = model.generate(messages, stop_sequences=["Observation:"])
     output = response.content

  2a. 循环检测
     连续 3 次相同输出 → E006 警告
     连续 3 次 E006 → 返回 E006 终止

  3. parse_step(output) → blocks
     空列表 → E001 → continue

   4. write_blocks(blocks, step, target_file)
      所有 ```lang[:path]``` 块写入磁盘
      ```edit:path``` 块 → 哈希锚点编辑（校验锚点、自底向上应用）
      wrote_this_step, edit_reports = write_blocks(...)
      锚点过期 (E_STALE_ANCHOR) → E009 → continue

   5. 执行第一个可执行块
      遍历 blocks，首个 lang in {python,py,bash,sh,shell} → exec_code
      无可执行块但有 edit_reports → 作为 edit-only step 继续
      无可执行块且无 edit_reports → E001 → continue

     result = execute_code(exec_code, tools)

  5a. SyntaxError 恢复 (E008)
     最多 3 次，注入 E008 恢复提示

  5b. wrote_file 追踪
     result.tool_calls 中有 "write_file" 或 wrote_this_step → wrote_file = True

  5c. 无进度守卫 (E007)
     verified = "run_python_file" in tool_calls or "execute_bash_command" in tool_calls
     made_progress = wrote_this_step or result.is_final_answer
     if verified and not wrote_file: made_progress = False
     连续 6 步无进度 → E007 → return

   6. 构建 Observation
      edit_reports 前置（成功/新鲜锚点/过期锚点）
      error → "Execution logs + Error"
      success → "Execution logs + Last output"
      read_file 调用时不截断

  7. final_answer 检查
     if result.is_final_answer:
       if not wrote_file: E002 → continue
       return result.output

  7a. 写入守卫 (step>=3)
     无写入、无验证、无 final_answer → E003/E004/E005 → continue

  8. 追加到 messages 继续循环

Max steps 耗尽 → 返回最后一条 Observation 或 fallback
```

---

## parse_step(text) → List[(lang, path, content)]

```python
def parse_step(text: str) -> list[tuple[str, str | None, str]]:
```

**识别格式：**
```
```lang
content
```
```lang:path
content
```
```

**正则：**
```python
r"```([A-Za-z0-9_-]+)(?::([^\n]+))?\s*\n(.*?)(?:```|$)"
```

**分类：**

| lang | path | 行为 |
|------|------|------|
| `file` | 必须有 | 原始内容写入 path |
| `python`/`py`/`bash`/`sh`/`shell` | 可选 | 写入 path 或临时文件记录，**标记可执行** |
| 其他 | 可选 | 视为原始文件写入 |

**返回：** 按文档顺序的 `(lang, path_or_None, content)` 列表。

---

## write_blocks(blocks, step, target_file, verbose) → (bool, list[str])

```python
def write_blocks(
    blocks: list[tuple[str, str | None, str]],
    step: int,
    target_file: str,
    verbose: bool = False,
) -> tuple[bool, list[str]]:
```

**逻辑：**
```python
for lang, path, content in blocks:
    if lang == "edit":
        # 哈希锚点编辑（校验锚点 → 自底向上应用 → 原子写）
        result = apply_edit_block(full_path, content)
        edit_reports.append(result)
        wrote_any = True
        continue

    if path is None:
        if lang in EXECUTABLE_LANGS:
            ext = "py" if lang in {"python","py"} else "sh"
            path = f"/tmp/mini-agent-{ts}-{step}.{ext}"
        else:
            continue  # 跳过无路径非可执行块

    full_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    wrote_any = True
```

**返回：** `(wrote_any, edit_reports)` — 是否写入了至少一个文件，以及编辑块的结果列表（成功报告或 `[E_STALE_ANCHOR]`）。

---

## 可执行语言集合

```python
_EXECUTABLE_LANGS = {"python", "py", "bash", "sh", "shell"}
```

---

## 关键状态变量

| 变量 | 含义 |
|------|------|
| `wrote_file` | 历史上曾成功写入文件（含工具调用） |
| `wrote_this_step` | 本步通过 `write_blocks` 写入文件 |
| `no_progress` | 连续无进度步数 |
| `syntax_warns` | SyntaxError 恢复次数（最多 3） |
| `consecutive_loops` | E006 连续触发次数 |

---

## 进度判定细节

```python
verified = "run_python_file" in result.tool_calls or "execute_bash_command" in result.tool_calls
made_progress = wrote_this_step or result.is_final_answer
if verified and not wrote_file:
    made_progress = False  # 未写文件前跑测试不算进度
```

---

## 观察截断

```python
if max_observation_length > 0 and "read_file" not in result.tool_calls:
    observation = _truncate_observation(observation, max_observation_length)
```

- 保留头尾各一半，中间标记截断字符数
- `read_file` 调用豁免（模型显式请求看全文，中间藏 bug）

---

## 辅助函数

| 函数 | 用途 |
|------|------|
| `_truncate_observation(text, max_len)` | 头尾保留截断 |
| `_log(verbose, text, limit)` | 条件打印到 stderr，可限制显示长度 |

> **解析器唯一入口**：`parse_step()` 是唯一的 Markdown 块解析函数，同时服务于
> 活循环（`react_loop`）与离线日志分析（`iterative_tune.py` 调用 `parse_step` 后取
> 第一个可执行块）。早期独立的 `parse_code()` 已删除，避免逻辑重复。