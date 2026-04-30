# @somna/prompts

Agent 各角色的提示词，版本化。

## 目录约定

```text
packages/prompts/
├── system/       通用系统约束（所有角色共享的前缀）
├── planner/      规划者（产 TODO）
├── executor/     执行者（调工具、产答案）
├── critic/       审查者（M3 启用）
└── compact/      上下文压缩器
```

每个角色下按版本存 markdown：`v1.md`, `v2.md` ...

## 使用

Python 端：

```python
from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[3] / "packages/prompts"

def load_prompt(role: str, version: str = "v1") -> str:
    return (PROMPTS / role / f"{version}.md").read_text(encoding="utf-8")
```

或通过 `somna_prompts` 包（未来可选）。

## 约束

- 提示词中的变量用 `{{var_name}}`（Jinja2 风格）
- 每个版本改动必须在文件头部写 "Changelog" 区块
- 提示词不得引用具体模型 ID（保持与 LiteLLM 别名解耦）
