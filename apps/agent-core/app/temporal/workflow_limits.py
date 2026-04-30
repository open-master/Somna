"""SessionRunWorkflow 的 Activity 策略默认值。

针对「多步工具调用 + 偶尔 DB/Temporal 抖动」调优：
- 心跳窗口要显著大于 activities 心跳间隔，避免误杀长阻塞工具或 checkpoint 卡顿。
- 单次 attempt 墙钟要覆盖长程构建；仍失败时允许多次重试再对用户失败。
"""

from __future__ import annotations

from datetime import timedelta

# 单次 Activity attempt：从 ingest 到 finalize 的整段图（同一 user message）
ACTIVITY_START_TO_CLOSE_TIMEOUT = timedelta(minutes=60)

# Temporal 要求在此时间内至少收到一次 activity heartbeat（见 activities._HEARTBEAT_INTERVAL_SECONDS）
ACTIVITY_HEARTBEAT_TIMEOUT = timedelta(minutes=10)

# Activity 失败（心跳断、worker 问题等）时 Temporal 重试次数上限
ACTIVITY_MAXIMUM_ATTEMPTS = 5
