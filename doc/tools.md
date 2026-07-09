# tools.py — 工具抽象与内置工具

## Tool 类

```python
class Tool:
    name: str          # 函数名，如 "write_file"
    description: str   # 文档字符串首段
    func: Callable     # 底层 Python 函数
    inputs: dict       # 输入参数 schema，从类型注解自动提取

    def to_prompt(self) -> str:   # 生成提示词中的函数签名
    def __call__(self, **kwargs) -> Any:  # 调用底层函数
```

---

## @tool 装饰器

```python
@tool
def write_file(filename: str, content: str) -> str:
    """Write text content to a file on disk."""
    ...
```

自动提取：
- `name` = 函数名
- `description` = docstring 首行
- `inputs` = 从 `inspect.signature` 推断 `{param: {"type": "str"}}`

---

## 四个内置工具

| 工具 | 签名 | 机制 | 超时 |
|------|------|------|------|
| `read_file` | `read_file(filename: str) -> str` | 进程内 `open().read()`，永不截断 | 无 |
| `write_file` | `write_file(filename: str, content: str) -> str` | 进程内 `open().write()` | 无 |
| `run_python_file` | `run_python_file(filename: str) -> str` | `subprocess.run(["python3", filename])` | 30s |
| `execute_bash_command` | `execute_bash_command(command: str) -> str` | `subprocess.run(command, shell=True)` | 30s |

### 设计原则
- `read_file` / `write_file`：本地 I/O，无需隔离
- `run_python_file` / `execute_bash_command`：走子进程，**这是安全边界**——模型通过 `exec()` 跑的代码不受限，但真正的文件执行、Shell 命令在子进程隔离

---

## 工具在 execute_code 中的注入

```python
for t in tools:
    def _make_wrapper(tool: Tool):
        def _wrapper(*args, **kwargs):
            called.append(tool.name)
            result = tool(*args, **kwargs)
            if result is not None:
                print(result)          # 自动打印返回值
            return result
        return _wrapper
    exec_ns[t.name] = _make_wrapper(t)
```

**行为：**
1. 记录 `tool_calls` 列表（用于进度判定、观察截断豁免）
2. 调用底层函数
3. 返回值非 None 自动 `print()` → 出现在 Observation 中

---

## 扩展新工具

在 `agent.py` 中添加：

```python
@tool
def my_tool(param: str) -> str:
    """Description here."""
    ...

# 自动被 run_deep_agent 捕获
```

无需修改其他模块。