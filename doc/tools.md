# tools.py — 工具抽象与内置工具

## Tool 类

```python
class Tool:
    name: str          # 函数名，如 "read_file"
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
def run_python_file(filename: str) -> str:
    """Run a Python script file and capture its output."""
    ...
```

自动提取：
- `name` = 函数名
- `description` = docstring 首行
- `inputs` = 从 `inspect.signature` 推断 `{param: {"type": "str"}}`

---

## 内置工具

| 工具 | 签名 | 机制 | 超时 |
|------|------|------|------|
| `read_file` | `read_file(filename, offset=0, limit=0) -> str` | 进程内 `open().read()`，返回**纯文本** | 无 |
| `run_python_file` | `run_python_file(filename) -> str` | `subprocess.run(["python3", filename])` | 30s |

### read_file

- 返回文件原始内容（**无哈希标签**）
- `offset`/`limit` 可读取文件范围（1-indexed，`0` = 全部）
- 读取失败返回 `Error reading ...` 字符串

### 设计原则
- 极简：`read_file` / `run_python_file` 两个工具足够完成"读→改→验→终"的闭环。

---

## 编辑 = 整文件重写：````file:path``` 块

**编辑不通过 Python 工具**，而是通过 Markdown 块在 `write_blocks` 阶段落盘：

- 写整个文件用 ````file:<path>``` 块（原始内容、零转义）：

```
```file:path/to/file.py
def add(a, b):
    return a + b
```
```

- 模型必须输出**完整**修正内容，没有行级/锚点编辑语法。
- ````file:``` 块先于首个 ````python``` 块写到磁盘。

> 为什么不用哈希锚点行编辑？9B 小模型能可靠地整文件重写，但很难逐字抄对 `12#a3f4` 这类锚点；哈希编辑光是纠错/重试就要消耗好几步。整文件重写把这个复杂度直接消除。

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
1. 记录 `tool_calls` 列表（用于观察截断豁免）
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