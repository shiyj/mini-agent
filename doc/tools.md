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

## 四个内置工具

| 工具 | 签名 | 机制 | 超时 |
|------|------|------|------|
| `read_file` | `read_file(filename, raw=False, offset=0, limit=0) -> str` | 进程内 `open().read()`，默认每行带 `N#HHHH` 哈希标签 | 无 |
| `run_python_file` | `run_python_file(filename) -> str` | `subprocess.run(["python3", filename])` | 30s |

### read_file 的哈希标签

默认返回格式（每行前缀 `行号#4位哈希 `）：

```
1#a3f4 def add(a, b):
2#8d2c     return a + b
```

- 哈希 = `sha1(prev + curr + next)` 前 4 位 → 相同内容不同上下文得到不同哈希
- `raw=True` 返回纯文本
- `offset`/`limit` 可读取文件范围（1-indexed）

### 设计原则
- `read_file`：本地 I/O，无需隔离

---

## 精准编辑：````edit:path``` 块

编辑不通过 Python 工具，而是通过 Markdown 块在 `write_blocks` 阶段直接落盘：

```
```edit:path/to/file.py
replace 12#a3f4 print(y)
insert_before 15#9abc new_line()
insert_after 20#def0 another()
delete 25#1234
```
```

| 操作 | 说明 |
|------|------|
| `replace` | 用新内容替换锚定行 |
| `insert_before` | 在锚定行前插入新行 |
| `insert_after` | 在锚定行后插入新行 |
| `delete` | 删除锚定行 |

锚点 `N#HHHH` 从 `read_file()` 的输出中直接复制。所有编辑在同一快照上校验（自底向上应用），过期锚点返回 `[E_STALE_ANCHOR]` 并附新鲜锚点。

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