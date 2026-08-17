# prompts.md — 系统提示词与错误码

## 概述

所有提示词文本集中在 `src/mini_agent/prompts.py`，**系统提示词极短**（极速流 12 词，深思流 ~34 行），错误恢复通过运行时注入用户消息实现。

---

## 极速流系统提示词

常量 `FAST_AGENT_SYSTEM`，12 个英文单词，配合 prefill 使用。
prefill 配置（`FAST_AGENT_PREFILL` / `FAST_AGENT_PREFILL_TEXT`）也定义在此文件中。

---

## 深思流系统提示词

`build_deep_system_prompt(tools)` 动态构建，约 34 行，包含：

- **Workflow**：read_file → `````file:path``` 整文件重写 → `````python``` 运行 → final_answer
- **Rules**：先写文件再执行、仅第一个代码块运行、工具自动打印、不写文件则禁止 final_answer
- **文件写入**：````file:path` 零转义示例（唯一的编辑方式，无行级语法）
- **Tools**：由 `Tool.to_prompt()` 动态生成签名列表

---

## 错误码机制

**设计原则**：系统提示词保持极短，特定失败时运行时注入**针对性恢复提示**作为用户消息。

```python
E001_NO_CODE = "E001_NO_CODE"
E002_FINAL_NO_ACTION = "E002_FINAL_NO_ACTION"
E003_FINAL_AFTER_ERROR = "E003_FINAL_AFTER_ERROR"
E004_NO_WRITE = "E004_NO_WRITE"

ERROR_PROMPTS = { ... }

def format_error(code: str, detail: str | None = None) -> str:
    return ERROR_PROMPTS[code].format(detail=detail or "")
```

---

## 错误码速查表 (E001–E004)

| 代码 | 触发条件 | 关键提示内容 |
|------|---------|-------------|
| **E001** | `parse_step()` 返回空列表，无 ````python``` 块 | 要求加 ````python``` 块并给出模板 |
| **E002** | `final_answer()` 但从未调用任何工具、也从未用 ````file:``` 写文件 | 要求先做真实工作再 final_answer |
| **E003** | `final_answer()` 紧接在报错步骤之后 | 要求先修错再 final_answer |
| **E004** | step≥3，代码跑了但没写任何文件 | 要求用 ````file:path``` 落盘再验证 |

> 精简后错误码从 13 个砍到 4 个：旧的 E004–E013 全部是哈希锚点编辑（E005–E008 写警告、E009 过期锚点、E011/E013 路径占位符）催生的兜底逻辑，整文件重写协议下都不再需要。

---

## 进度判定关键点

- `wrote_target = True`：本步通过 ````file:``` 块写入了文件
- `made_progress = wrote_target or is_final_answer`：既没写文件也没收尾 = 无进度
- `has_used_tool`：调用过工具**或**写过 ````file:``` 块，都算"做了工作"（否则 E002 拒绝第一个 final_answer）