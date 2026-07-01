# mini-agent

基于 `smolagents` 和本地 Ollama 模型的轻量级编程 Agent，提供**极速生成**与**深思验证**两种工作模式。

## 设计原则

**配置与流程分离。** 所有可调参数和提示词模板以 JSON 文件存放在 `config/` 目录中，Python 代码只负责加载和执行，无需修改代码即可调整行为。

- `config/config.json` — Ollama 连接配置（地址、模型）
- `config/prompts.json` — Agent 提示词模板

详细实现说明见 [doc/implementation.md](doc/implementation.md)。

---

## 快速开始

### 前置条件

- Python >= 3.10
- 本地 [Ollama](https://ollama.com) 服务运行中
- 可用模型（如 `ornith:9b`）

### 安装

```bash
cd mini_agent
uv sync
```

### 配置

编辑 `config/config.json`，将 `base_url` 和 `default_model` 改为你的 Ollama 地址和模型：

```json
{
  "base_url": "http://YOUR_OLLAMA_HOST:11434/v1",
  "default_model": "ornith:9b"
}
```

---

## 使用

### 极速流 — 快速代码生成

```python
from mini_agent import run_fast_agent

result = run_fast_agent("Write a function to compute Fibonacci numbers")
print(result)
```

温度锁定 `0.0`，最大 2 步，无外部工具，适合简单语法转换和代码生成。

### 深思流 — 自动纠错循环

```python
from mini_agent import run_deep_agent

result = run_deep_agent(
    "Write a script that downloads a webpage and extracts all links",
    target_file="output.py",
)
print(result)
```

温度 `0.4~0.5`，最大 15 步，挂载文件写入/执行工具。
Agent 自动执行：生成代码 → 写入文件 → 运行 → 读错误 → 修复 → 重复。

---

## 验证

运行端到端验证：

```bash
uv run python3 verify/verify.py
```

验证步骤：
1. **Config 加载** — 确认 `config/config.json` 正确读取
2. **模型连通性** — 调用 Ollama 模型返回简单回复
3. **Deep Agent 循环优化** — 让 Agent 编写质数筛法对比脚本并自动纠错

验证通过标准：Agent 必须能在"生成 → 执行 → 修复"循环中产出正确结果。

---

## 项目结构

```
mini_agent/
├── config/
│   ├── config.json              # Ollama 连接配置
│   └── prompts.json             # 提示词模板
├── doc/
│   └── implementation.md        # 实现细节与总结
├── src/
│   └── mini_agent/
│       ├── __init__.py
│       ├── config.py            # 配置加载器
│       └── agent.py             # Agent 实现
├── verify/
│   └── verify.py                # 验证脚本
├── pyproject.toml
└── README.md
```

---

## 依赖

- `smolagents[litellm]` — Hugging Face Agent 框架
- 本地 Ollama 服务
