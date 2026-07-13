# hashline.py — 上下文哈希与哈希锚点编辑

## 概述

每行代码根据 `prev + curr + next` 上下文计算 4 位十六进制哈希。相同内容在不同上下文得到不同哈希，编辑某行只失效相邻三行的锚点。

---

## 核心函数

| 函数 | 用途 |
|------|------|
| `tag_lines(text) -> str` | 每行加 `N#HHHH ` 前缀，可精确还原 |
| `strip_tags(text) -> str` | 剥离前缀还原纯文本 |
| `apply_edit_block(path, dsl_text) -> str` | 应用 ````edit:path` 块内容到文件 |

---

## ````edit:path` 块 DSL

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
| `replace` | 用新内容替换锚定行；若再给一个 `end` 锚点，则替换 `start..end` 含闭区间的**多行** |
| `insert_before` | 在锚定行前插入新行（可多行） |
| `insert_after` / `append` | 在锚定行后插入新行（可多行） |
| `prepend` | 在锚定行前插入新行（可多行，等同 `insert_before`） |
| `delete` | 删除锚定行 |

每行一条编辑指令。`replace` 的多行形式：指令行后跟若干行新内容，直到下一条指令或块结束。锚点 `N#HHHH` 从 `read_file()` 输出中直接复制。

多行替换示例（把第 12–15 行整体换成新内容，无需 `file:` 整文件覆盖）：

```
replace 12#a3f4 15#9abc
def greet(name):
    return f"Hello, {name}"
```


---

## 执行流程

1. 解析 DSL → `[{"op", "anchor", "content"}, ...]`
2. 读文件，按上下文哈希构建快照
3. 校验所有锚点（过期 → `[E_STALE_ANCHOR]` + 新鲜锚点）
4. 按行号降序自底向上应用（保证前面的锚点不失效）
5. 原子写入（临时文件 + rename）
6. 返回成功报告 + 受影响区域的新鲜锚点

---

## 哈希算法

```python
def _line_hash(prev: str, curr: str, nxt: str) -> str:
    raw = f"{prev}\\x00{curr}\\x00{nxt}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:4]
```

- 包含上下文 → 相同内容不同位置得到不同哈希
- 编辑行 N → 只失效 N-1, N, N+1 的锚点
- 4 位十六进制 = 65536 个可能值，上下文使碰撞概率极低
