# prompts.md — 系统提示词与错误码

## 概述

所有提示词文本集中在 `src/mini_agent/prompts.py`，**系统提示词极短**（极速流 12 词，深思流 ~30 行），错误恢复通过运行时注入用户消息实现。

---

## 极速流系统提示词

常量 `FAST_AGENT_SYSTEM`，12 个英文单词，配合 prefill 使用。
prefill 配置（`FAST_AGENT_PREFILL` / `FAST_AGENT_PREFILL_TEXT`）也定义在此文件中。

---

## 深思流系统提示词

`build_deep_system_prompt(tools, target_file)` 动态构建，约 35 行，包含：

- **Workflow**：read_file → ````file:path`/````edit:path` 写文件 → ````python` 运行 → final_answer
- **Rules**：先写文件再执行、仅第一个代码块运行、工具自动打印、成功后立即 final_answer
- **How to write a file**：````file:path` 零转义示例
- **Precise editing**：````edit:path` 哈希锚点编辑（replace/insert_before/insert_after/delete）
- **Tools**：由 `Tool.to_prompt()` 动态生成签名列表
- **Important**：必须保存文件、必须验证、必须 final_answer

---

## 错误码机制

**设计原则**：系统提示词保持极短，特定失败时运行时注入**针对性恢复提示**作为用户消息。

```python
ERROR_PROMPTS = {
    "E001_NO_CODE": "...",
    "E002_FINAL_NO_WRITE": "...",
    ...
}

def format_error(code: str, target_file: str | None = None, var: str | None = None) -> str:
    return ERROR_PROMPTS[code].format(target_file=target_file or "", var=var or "")
```

---

## 错误码速查表 (E001–E009)

| 代码 | 触发条件 | 关键提示内容 |
|------|---------|-------------|
| **E001** | `parse_step()` 返回空列表 | 要求模型加 ````python`/````bash` 块 |
| **E002** | `final_answer()` 但 `wrote_file == False` | 要求先 ````file:path` 或 `write_file()` 保存再 final_answer |
| **E003** | step≥3，代码只读文件未写入 | 要求用 ````file:path` 覆盖文件 |
| **E004** | step≥3，代码赋值修复内容到变量但未写入 | 要求用 ````file:path` 或 `write_file(content={var})` 写入 |
| **E005** | step≥3，代码无写入动作（通用） | 要求保存文件而非仅运行/打印 |
| **E006** | 连续 3 次相同输出 | 要求停止循环，立即 ````file:path` + run_python_file + final_answer |
| **E007** | 连续 6 步无进度 | 要求保存文件到磁盘 |
| **E008** | 模型代码 SyntaxError | 引导用 ````file:path` 零转义写文件，避免字符串嵌套 |
| **E009** | ````edit:path` 锚点过期 | 要求重新 `read_file()` 获取新鲜锚点后重试 |

---

## 进度判定关键点

- `wrote_file = True`：本步写入了 ````file:```/````edit:```/````python:```/````bash:``` 块，**或**调用了 `write_file` 工具
- `verified = "run_python_file" in tool_calls or "execute_bash_command" in tool_calls`
- `made_progress = wrote_this_step or verified or is_final_answer`
- 验证**仅在已写文件后**算进度（防止反复跑有 bug 文件）