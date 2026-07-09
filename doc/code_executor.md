# code_executor.py — 代码执行引擎

## 概述

仅 ~120 行 `exec()` 实现，无白名单，完整内置函数。

---

## 核心函数

### `execute_code(code: str, tools: list[Tool], timeout: int = 30) -> ExecutionResult`

在隔离命名空间执行代码，注入工具和 `final_answer`。

```python
exec_ns = {
    "__name__": "__main__",
    "__builtins__": __builtins__,   # 完整内置函数，无白名单
}
# 注入工具（自动打印返回值）
for t in tools:
    exec_ns[t.name] = _make_wrapper(t)
# 注入 final_answer
exec_ns["final_answer"] = final_answer
```

### `ExecutionResult`
```python
@dataclass
class ExecutionResult:
    output: Any                    # final_answer 的值，或最后表达式的值
    logs: str                      # 捕获的 stdout
    is_final_answer: bool          # 是否调用了 final_answer()
    error: str | None              # 异常信息
    tool_calls: list[str]          # 本轮调用过的工具名
```

---

## 关键设计决策

| 问题 | 方案 |
|------|------|
| `exec()` 分离 globals/locals 导致函数内 `import` 不可见 | 单一 `exec_ns` 字典同时作 globals 和 locals |
| `final_answer` 赋值不跨作用域 | 用 `list[Any]` 可变容器：`final_answer_value[0] = x` |
| `exec()` 不捕获表达式返回值 | 工具自动 `print()` 返回值；回退取 `exec_ns.get("_")` |
| 连续调用 `final_answer()` 覆盖值 | `got_final_answer[0] = True` 后不再修改 |
| 安全性 | 不在 exec 层限制；文件执行、Shell 走子进程工具隔离 |

---

## 执行流程

```
execute_code(code, tools)
    │
    ├─ 构建 exec_ns = {__builtins__, tools, final_answer}
    ├─ 捕获 stdout → io.StringIO
    ├─ exec(compile(code, "<agent_code>", "exec"), exec_ns)
    │      │
    │      ├─ 工具调用 → 打印返回值 → 记录 tool_calls
    │      └─ final_answer(x) → 设置标志 + 保存值
    │
    ├─ 恢复 stdout
    ├─ 组装 ExecutionResult
    └─ 返回
```

---

## 用法示例

```python
from mini_agent import run_deep_agent, get_local_model
from mini_agent.code_executor import execute_code
from mini_agent.tools import write_file, run_python_file

model = get_local_model("ornith:9b")

# 直接使用（不经过 ReAct 循环）
code = """
result = write_file("test.py", "print('hello')")
print(result)
output = run_python_file("test.py")
final_answer(output)
"""
result = execute_code(code, [write_file, run_python_file])
print(result.logs)
print(result.is_final_answer)  # True
```