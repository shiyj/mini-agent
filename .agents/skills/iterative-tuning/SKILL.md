---
name: iterative-tuning
description: >
  运行 fix_python_error.py 进行 N 轮测试，覆盖 8 个温度值，统计结果并输出到带时间戳的 test_suite JSONL 文件。脚本仅负责汇总和定位原始请求/响应日志，失败根因分析由 LLM 读取日志完成。支持 --rounds 参数。
---

## 技能功能

在 8 个温度设置（0.1-0.8）下运行 `examples/fix_python_error.py`，每个温度 N 轮（默认 10 轮），总共 N × 8 次测试。每次测试结果立即追加到带时间戳的 `test_suite_{timestamp}.jsonl` 文件，即使中断也能保留部分结果。

## 两个独立工具

**`iterative_tune.py`** — 仅运行批量测试。在 8 个温度下启动 `fix_python_error.py`，每次测试向带时间戳的 `test_suite_{timestamp}.jsonl` 追加一条 JSON 记录，然后退出。**不具备分析功能**。

```bash
# 先激活虚拟环境
source .venv/bin/activate

# 运行测试
python3 .agents/skills/iterative-tuning/iterative_tune.py            # 80 次测试 (默认 10 轮)
python3 .agents/skills/iterative-tuning/iterative_tune.py --rounds 1 # 8 次测试
python3 .agents/skills/iterative-tuning/iterative_tune.py --rounds 5 # 40 次测试
```

**`analyze.py`** — **仅读取** `test_suite_*.jsonl` 并生成汇总 / 查询原始日志。**不运行任何测试**。

```bash
# 先激活虚拟环境
source .venv/bin/activate

# 汇总：总计、各温度成功率、错误码频次
python3 .agents/skills/iterative-tuning/analyze.py
python3 .agents/skills/iterative-tuning/analyze.py --suite test_suite_2026-07-14_19-48-26.jsonl

# 查询：包含某错误码的测试及其原始日志路径
python3 .agents/skills/iterative-tuning/analyze.py --error E004

# 查询：失败的测试及其原始日志
python3 .agents/skills/iterative-tuning/analyze.py --failed
```

`analyze.py` 无参数时打印汇总；`--error E0XX` 和 `--failed` 打印匹配的 `round_id`/`temperature` 以及对应测试的 `fix_python_error_*.jsonl` 请求/响应日志路径。**不解释失败原因** —— 那是 LLM 的工作。

## 输出文件

所有输出写入 `examples/logs/`。**技能目录不写任何文件。**

| 路径 | 内容 |
|------|------|
| `examples/logs/test_suite_{timestamp}.jsonl` | 每行一条 JSON，记录单次测试结果 |
| `examples/logs/fix_python_error_{timestamp}.jsonl` | 单次测试的请求/响应原始日志 |

每次运行创建新的 `test_suite_{timestamp}.jsonl`（时间戳 = 运行开始时间）。

## test_suite JSONL 结构

```json
{
  "timestamp": "2026-07-14_16-48-18",
  "round_id": 0,
  "temperature": 0.1,
  "success": false,
  "steps": 8,
  "duration": 12.5,
  "error": "",
  "error_counts": {"E004": 3, "E001": 2},
  "log_file": "examples/logs/fix_python_error_2026-07-14_16-48-18.jsonl"
}
```

`error_counts` 记录本次测试中各错误码 (E001–E004) 出现的次数。`log_file` 指向该测试的请求/响应日志。

## 请求/响应日志格式

`fix_python_error_*.jsonl` 为行分隔 JSON。每条包含：
- `direction`：`request` / `response`
- `model`：模型名称
- `body`：请求/响应体。`request` 时 `body.input` 为消息列表；`role == "user"` 的消息包含注入的错误码提示（如 `"Error E004: ..."`），`role == "assistant"` 的消息包含模型之前的输出。`body.instructions` 为系统提示词。

这是 LLM 阅读以理解**失败原因**的原始素材。

## 分析工作流（LLM 驱动）

`analyze.py` 只负责汇总和定位。实际根因分析由 LLM 完成：

1. `iterative_tune.py --rounds N` 生成数据。
2. `analyze.py` 查看整体概况（哪个温度弱、哪个错误码占主导）。
3. `analyze.py --error E004`（或主导错误码）获取包含该错误码的 `fix_python_error_*.jsonl` 日志文件列表。
4. **阅读这些日志文件**（`role == "user"` 的请求条目包含注入的错误提示 + 模型之前的输出）。从实际模型输出判断**为什么失败** —— 如模型只检查代码未写入文件，或反复产出相同错误编辑。
4. `analyze.py --failed` 列出失败测试，阅读其日志找到放弃前的最后错误及模型行为。
5. 基于日志发现提出具体修复（提示词措辞、`react_agent.py` / `prompts.py` 代码、默认温度等）。