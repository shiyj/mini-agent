# why another ai agent?

* 专门为9b的小模型设计；
* 不要考虑复杂的上下文管理和记忆，一次执行一个任务；
* 别管什么命中缓存，本地使用；
* 架构简单，层级清晰，操作直接，代码轻量

# 细节
* 全是块，```python/bash/edit/file,方便模型理解结构；
* edit：hash