# mini-agent 实现文档

## 概述

mini-agent 是一个基于 Hugging Face `smolagents` 库的本地轻量级编程 Agent，
通过 `LiteLLMModel` 对接本地 Ollama 模型（如 ornith:9b），
提供**极速流**和**深思流**两种 ReAct 模式。

设计哲学：
- **模型由调用者传入** — Agent 不依赖 `config.json`，不接触模型配置
- **配置与流程分离** — 提示词模板在 `config/prompts.json` 中集中管理
- **ReAct 架构** — 两种模式在 reasoning + acting 的深度上有本质区别

---

## 项目结构

```
mini_agent/
├── config/
│   └── prompts.json             # 所有提示词模板（唯一配置文件）
├── doc/
│   └── implementation.md        # 本文档
├── examples/
│   ├── basic_usage.py           # 最简使用：创建模型 + 运行 fast agent
│   ├── fast_vs_deep.py          # 极速流 vs 深思流 对比
│   └── custom_model.py          # 手动构造 LiteLLMModel 传入
├── src/
│   └── mini_agent/
│       ├── __init__.py          # 公共 API 导出
│       ├── config.py            # 提示词加载器（无模型配置）
│       └── agent.py             # ReAct Agent 实现
├── verify/
│   └── verify.py                # 端到端验证脚本
├── pyproject.toml               # 项目元数据与依赖
└── README.md                    # 使用说明
```

---

## 配置文件

`config/prompts.json` 是唯一配置文件，包含两种模式的所有提示词模板，
支持 `{prompt}`、`{target_file}` 等占位符由 Python 在运行时填入。

---

## 核心模块

### `src/mini_agent/config.py`

- `load_prompts()`: 读取 `config/prompts.json`，返回提示词模板字典
- `build_prompt(section, **kwargs)`: 加载模板并填入参数
- 使用模块级缓存，避免重复读取磁盘
- ⚠ 不再包含 `OllamaConfig` / `load_config` 等模型相关代码

### `src/mini_agent/agent.py`

**模型工厂（可选）**

- `get_local_model(model_name, api_base, temperature, ctx_size)` → `LiteLLMModel`
  用户可完全跳过此函数，直接用 `LiteLLMModel(...)` 构造。

**三个工具函数：**
| 工具 | 说明 |
|------|------|
| `write_file(filename, content)` | 将字符串写入文件 |
| `run_python_file(filename)` | 执行 Python 文件并捕获输出 |
| `execute_bash_command(command)` | 执行任意 shell 命令 |

**两种 ReAct 模式：**

| | 极速流 | 深思流 |
|---|---|---|
| 函数 | `run_fast_agent(prompt, model, max_steps=1)` | `run_deep_agent(prompt, model, target_file, max_steps=8)` |
| 本质 | 单次推理，无 Acting | 完整 ReAct 循环 |
| 工具 | 无 | write_file + run_python_file + execute_bash_command |
| 温度建议 | 0.0（确定性输出） | 0.4~0.5（多样性试错） |
| 适用场景 | 简单生成、语法转换 | 复杂脚本、调试、迭代任务 |

**架构对比：**

```
Fast Agent (极速流):
  Reason (单步思考) → 输出代码
  └── 本质是纯生成，不涉及 Acting 步骤

Deep Agent (深思流):
  Reason → Act (写文件) → Observe (执行+读结果) → Reason → Act → ...
  └── 完整的 ReAct：思考 → 行动 → 观察 → 再思考
```

两者的区别不只是提示词不同——deep agent 持有执行工具、有更多的 `max_steps` 来允许多轮 Reasoning + Acting 循环，而 fast agent 只有一步推理。

---

## 验证流程

验证脚本位于 `verify/verify.py`，执行三步检查：

1. **Prompt 加载**: 确认 `prompts.json` 正确读取，模板渲染正常
2. **模型连通性**: 调用 Ollama 模型返回一条简短回复
3. **Deep Agent — 循环优化**: 让 Agent 编写质数筛法对比脚本并自动纠错

第三步验证 Agent 的"生成 → 执行 → 修复"闭环能力，
任务要求编写**埃拉托色尼筛法**和**轮式优化筛法**两种实现，对比性能并验证结果一致。

运行验证：

```bash
cd mini_agent
uv run python3 verify/verify.py
```

---

## 使用示例

`examples/` 目录包含三个可直接运行的示例：

| 文件 | 说明 |
|------|------|
| `basic_usage.py` | 创建模型 → 运行 fast agent，最简流程 |
| `fast_vs_deep.py` | 同一任务分别在两种模式下执行，观察行为差异 |
| `custom_model.py` | 跳过 `get_local_model`，直接构造 `LiteLLMModel` |

---

## 依赖

- Python >= 3.10
- smolagents[litellm] >= 1.26.0
- 本地 Ollama 服务 + 可用模型
