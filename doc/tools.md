# tools.md — 工具抽象与内置工具

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

**关键区别**：不验证参数名必须为 `argument` 或 `args`（smolagents 要求），更宽松。

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

## 三个内置工具

### write_file(filename, content)

```python
def write_file(filename: str, content: str) -> str:
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Written {len(content)} bytes to {filename}"
```

- 同步写入，**在当前进程**（仅磁盘 I/O，无隔离需求）
- 返回确认消息，自动被 `execute_code` 打印到 Observation

---

### run_python_file(filename)

```python
def run_python_file(filename: str) -> str:
    result = subprocess.run(
        ["python3", filename],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode == 0:
        return f"【OK】\n{result.stdout}"
    else:
        return f"【FAILED exit={result.returncode}】\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
```

- **子进程隔离**，30s 超时
- 返回完整 stdout/stderr，供模型观察

---

### execute_bash_command(command)

```python
def execute_bash_command(command: str) -> str:
    result = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=30,
    )
    ...
```

- 任意 shell 命令，子进程隔离
- 用户自行控制安全性

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