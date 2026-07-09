# mini-agent 文档索引

本目录按模块组织文档，每个模块一个文件：

| 文件 | 对应模块 | 内容 |
|------|---------|------|
| [types.md](types.md) | `types.py` | 数据类型、消息处理 |
| [tools.md](tools.md) | `tools.py` | Tool 抽象、@tool 装饰器、内置工具 |
| [code_executor.md](code_executor.md) | `code_executor.py` | exec() 执行引擎、工具注入、final_answer |
| [react_agent.md](react_agent.md) | `react_agent.py` | ReAct 循环、Markdown 块解析、文件写入、错误恢复 |
| [prompts.md](prompts.md) | `prompts.py` | 系统提示词、错误码 (E001–E008) |
| [model.md](model.md) | `model.py` | ResponsesModel、SSE、消息处理 |
| [config.md](config.md) | `config.py` | 配置加载 |
| [agent.md](agent.md) | `agent.py` | 组合层、公共 API、两种模式入口 |
| [architecture.md](architecture.md) | 整体架构 | 数据流图、模块依赖、核心设计决策 |

---

## 快速导航

- **核心循环**：[react_agent.md](react_agent.md)
- **代码执行**：[code_executor.md](code_executor.md)
- **提示词/错误码**：[prompts.md](prompts.md)
- **模型通信**：[model.md](model.md)
- **工具系统**：[tools.md](tools.md)
- **整体架构**：[architecture.md](architecture.md)