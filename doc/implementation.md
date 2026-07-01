# mini-agent 实现文档

## 概述

mini-agent 是一个基于 Hugging Face `smolagents` 库的本地轻量级编程 Agent，
通过 `LiteLLMModel` 对接本地 Ollama 模型（如 ornith:9b），
提供"极速生成"和"深思验证"两种工作模式。

设计哲学遵循 **配置与流程分离** 原则——所有可调参数和提示词模板均以 JSON 文件
存放在 `config/` 目录中，Python 代码只负责加载和执行。

---

## 项目结构

```
mini_agent/
├── config/
│   ├── config.json              # Ollama 模型连接配置
│   └── prompts.json             # 所有提示词模板
├── doc/
│   └── implementation.md        # 本文档
├── src/
│   └── mini_agent/
│       ├── __init__.py          # 公共 API 导出
│       ├── config.py            # 配置文件加载器
│       └── agent.py             # Agent 实现（极速流 + 深思流）
├── verify/
│   └── verify.py                # 端到端验证脚本
├── pyproject.toml               # 项目元数据与依赖
└── README.md                    # 使用说明
```

---

## 配置文件

### `config/config.json` — 模型连接

```json
{
  "provider": "ollama",
  "base_url": "http://172.20.104.158:11434/v1",
  "default_model": "ornith:9b",
  "models": { ... }
}
```

- `base_url`: Ollama 服务的 OpenAI 兼容 API 地址
- `default_model`: 默认使用的模型标签

### `config/prompts.json` — 提示词模板

所有提示词集中管理，支持 `{prompt}`、`{target_file}` 等占位符，
代码层通过字符串格式化填入执行期变量。

---

## 核心模块

### `src/mini_agent/config.py`

- `load_config()`: 读取 `config/config.json`，返回 `OllamaConfig` 对象
- `load_prompts()`: 读取 `config/prompts.json`，返回提示词模板字典
- 使用模块级缓存，避免重复读取磁盘

### `src/mini_agent/agent.py`

提供三个工具和两个 Agent 实现：

**工具函数：**
| 工具 | 说明 |
|------|------|
| `write_file(filename, content)` | 将字符串写入文件 |
| `run_python_file(filename)` | 执行 Python 文件并捕获输出 |
| `execute_bash_command(command)` | 执行任意 shell 命令 |

**Agent 模式：**

1. **极速流** (`run_fast_agent`)
   - 温度: 0.0（确定性输出）
   - 最大步数: 2
   - 无外部工具
   - 适用：简单代码生成、语法转换

2. **深思流** (`run_deep_agent`)
   - 温度: 0.4~0.5（多样性试错）
   - 最大步数: 15
   - 挂载 `write_file` + `run_python_file` + `execute_bash_command`
   - 流程：生成代码 → 写入文件 → 执行 → 读错误 → 修复 → 重复

---

## 验证流程

验证脚本位于 `verify/verify.py`，执行三步检查：

1. **Config 加载**: 确认 `config/config.json` 正确读取
2. **模型连通性**: 调用 Ollama 模型返回一条简短回复
3. **Deep Agent — 循环优化**: 让 Agent 编写质数筛法对比脚本并自动纠错

第三步核心是验证 Agent 的"生成 → 执行 → 修复"闭环能力，
任务要求编写**埃拉托色尼筛法**和**轮式优化筛法**两种实现，
对比性能并验证结果一致。

运行验证：

```bash
cd mini_agent
uv run python3 verify/verify.py
```

验证通过标准：
- ❌ 不允许 `@ts-ignore` 或 `as any` 式跳过（Python 无类型压制问题）
- ❌ 不允许空 catch 块
- ❌ 不允许删除失败测试来"通过"
- ✅ 生成的脚本必须实际运行并输出正确质数集合

---

## 配置分离原则

所有可能被外部调整的值都不应硬编码在 Python 中：

| 配置项 | 位置 | 示例值 |
|--------|------|--------|
| Ollama 地址 | `config/config.json` | `http://172.20.104.158:11434/v1` |
| 默认模型 | `config/config.json` | `ornith:9b` |
| 极速流提示词 | `config/prompts.json` | `fast_agent.system_instruction` |
| 深思流提示词 | `config/prompts.json` | `deep_agent.system_prompt` |
| Agent 步数上限 | 代码参数 | `max_steps=15` |

步数上限 `max_steps`、温度 `temperature` 等执行期参数保留在 Python 调用处，
因为它们属于**调用策略**而非**静态配置**，不同场景需要不同值。

---

## 依赖

- Python >= 3.10
- smolagents[litellm] >= 1.26.0
- 本地 Ollama 服务 + 可用模型
