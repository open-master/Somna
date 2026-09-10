"""Specific, shared executor budget diagnostics."""


def budget_reason(*, tool_turns, total_turns, tokens, max_tools, max_turns, max_tokens):
    for label, value, limit in (
        ("工具执行轮数", tool_turns, max_tools),
        ("模型调用轮数", total_turns, max_turns),
        ("累计 Token", tokens, max_tokens),
    ):
        if value >= limit:
            return f"已达到{label}上限（{value}/{limit}）"
    return None
