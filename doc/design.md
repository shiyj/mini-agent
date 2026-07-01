
# 基于 smolagents 的本地轻量级编程 Agent 开发规范指南

本文件定义了如何基于 Hugging Face 的 `smolagents` 库，围绕本地小模型（如 Qwen2.5-Coder、Ornith-9B）构建一个兼顾“极致速度”与“深度自动纠错”的本地编程/脚本执行智能体（Agent）。

---

## 一、 开发前置：核心设计哲学

1. **代码即行动 (Code-First)**：充分利用 `smolagents` 的 `CodeAgent` 特性，让本地小模型直接输出原生 Python 代码片段来调用工具，规避本地模型容易写错 JSON Schema 的硬伤。
2. **轻量与低显存**：保持框架代码极简，把宝贵的上下文窗口和显存全部留给本地模型，杜绝类似 LangChain 的臃肿抽象。
3. **两极化场景自适应**：通过参数和架构控制，完美支持“极速流（无思考、单步输出）”与“深思流（外挂执行、反复自动纠错）”两种极端业务场景。

---

## 二、 基础脚手架：本地模型与通用工具配置

无论哪种运行模式，均统一采用 `LiteLLMModel` 对接本地 Ollama，并外挂系统级终端工具。

```python
import subprocess
from smolagents import CodeAgent, LiteLLMModel, tool

# 1. 统一本地模型加载函数
def get_local_model(model_name: str, temperature: float = 0.2, ctx_size: int = 8192):
    """适配本地 Ollama 终端的底层模型配置"""
    return LiteLLMModel(
        model_id=f"ollama_chat/{model_name}",
        api_base="http://localhost:11434/v1",
        temperature=temperature,
        num_ctx=ctx_size
    )

# 2. 核心外挂工具：本地终端执行器（深思流的关键桥梁）
@tool
def execute_bash_command(command: str) -> str:
    """
    在当前电脑的本地终端中执行一条 Bash/Shell 命令（如运行 python 脚本、执行测试或环境检查），
    并实时捕获标准输出(stdout)与标准错误(stderr)。

    Args:
        command: 要在终端运行的完整命令字符串。例如 'python test_script.py'
    """
    try:
        # 设置 30 秒超时防死锁
        res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        if res.returncode == 0:
            return f"【执行成功】\n终端输出:\n{res.stdout}"
        else:
            return f"【执行失败】\n错误码: {res.returncode}\n错误日志(Stderr):\n{res.stderr}"
    except Exception as e:
        return f"【系统异常】: {str(e)}"

```

---

## 三、 场景开发规范

### 1. 🏎️ 极速完成流 (Fast Track)

* **业务目标**：应对简单的编程语法、代码去重、单纯的逻辑函数生成。要求模型以最快速度吐出代码，不消耗额外显存与时间去进行本地验证。
* **配置核心**：将温度降至 `0.0` 追求绝对确定性，限制最大步数，且**无需**外挂任何 Bash 执行工具。

```python
def run_fast_agent(prompt: str, model_size: str = "qwen2.5-coder:1.5b"):
    """极速流 Agent 实例：追求极致速度与极低显存占用"""
    # 极速流首选 1.5B/2B 级极致轻量模型，温度锁死 0.0 防止废话
    model = get_local_model(model_name=model_size, temperature=0.0)
    
    agent = CodeAgent(
        tools=[],  # 纯代码任务不需要外挂工具
        model=model,
        max_steps=2,  # 严格限制步骤，不允许陷入长考
        additional_authorized_imports=["math", "json", "os", "sys"]
    )
    
    # 强力 Prompt 注入，物理切断小模型的唠叨本能
    system_instruction = "你是一个极速代码生成器。请直接输出可执行的 Python 代码或核心逻辑，严禁带有任何多余的解释、问候或 `<thinking>` 思考块。"
    
    return agent.run(f"{system_instruction}\n\n任务需求: {prompt}")

```

### 2. 🧠 深思验证流 (Deep-Thinking Loop)

* **业务目标**：应对复杂的脚本编写、涉及本地文件修改、或需要自动调试（Debug）的复杂任务。允许模型反复试错、看报错、自我修正，用工程的时间换取代码的高质量。
* **配置核心**：推荐选用具备强化学习（RL）思考背景的 7B/9B 模型（如 Ornith-9B 或 Qwen2.5-Coder-7B），稍微调高温度提供多样性解题思路，并赋予 `execute_bash_command` 工具权限，放大 `max_steps`。

```python
def run_deep_agent(prompt: str, target_file: str = "output.py", model_size: str = "ornith:9b"):
    """深思流 Agent 实例：通过本地运行反馈、反复验证纠错以保证质量"""
    # 适当给 0.4~0.5 温度，当第一遍代码报错时，允许小模型在下一轮尝试其他语法思路
    model = get_local_model(model_name=model_size, temperature=0.5, ctx_size=16384)
    
    agent = CodeAgent(
        tools=[execute_bash_command],  # 赋予控制终端和自我测试的权力
        model=model,
        max_steps=8,  # 给予充足的试错迭代轮数（建议 5-10 轮之间）
        additional_authorized_imports=["os", "sys", "time", "subprocess", "pandas"]
    )
    
    # 指引 Prompt：引导模型进入【生成 -> 执行测试 -> 看报错 -> 修复】的正向反馈循环
    deep_instruction = f"""
    你是一个具备高鲁棒性的自动化编程智能体。
    请在当前目录下编写或修改 `{target_file}` 文件以满足用户的最终需求。
    
    【核心守则】：
    1. 你必须使用 `execute_bash_command` 工具去实际运行你的脚本（例如通过执行 `python {target_file}`）。
    2. 如果在执行中发现任何语法错误、缺少第三方包（ModuleNotFoundError）或运行时 Traceback 报错，不要气馁！
    3. 请仔细分析终端返回的错误日志（Stderr），并在下一轮的 Action 代码块中针对性地进行修复或依赖补全，直至脚本能够顺利闭环运行并产出预期结果。
    """
    
    return agent.run(f"{deep_instruction}\n\n用户最终需求: {prompt}")

```

---

## 四、 本地小模型调优防翻车红线

当指示 AI 进一步基于此库开发更高级的功能时，必须遵守以下策略：

1. **System Prompt 必须“瘦身”**：本地小模型在面对超过 1000 词的繁琐 System Prompt 时，注意力极易崩溃（表现为无视工具约束或陷入格式混乱）。尽量保持 Prompt 精炼直白。
2. **用 Python 标准库替代外部复杂依赖**：在 Agent 内部，尽量多授权给模型 `os`, `sys`, `pathlib`, `shutil` 等原生库。模型对标准库的代码记忆深度远超第三方冷门库，能大幅提升单次编写的成功率。
3. **断点隔离**：如果发现模型在深思流中陷入了“同一个 Bug 连续卡死 3 轮”的怪圈，开发时应在框架层介入判断：若最近 3 轮的 `Thought` 或报错日志完全一致，强制抛出异常结束任务，防止本地硬件陷入无效发热。

