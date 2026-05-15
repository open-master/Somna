export type AgentModelRole = "taskframe" | "planner" | "executor" | "coder" | "reasoner" | "longctx" | "cheap" | "skill";

/** 前端默认（具体 model id，经网关解析）；与 LiteLLM 别名可在网关侧独立配置。 */
export const DEFAULT_AGENT_MODELS: Record<AgentModelRole, string> = {
  taskframe: "deepseek-v4-pro",
  planner: "deepseek-v4-pro",
  executor: "deepseek-v4-pro",
  coder: "deepseek-v4-pro",
  reasoner: "deepseek-v4-pro",
  longctx: "deepseek-v4-pro",
  cheap: "deepseek-v4-pro",
  skill: "deepseek-v4-pro",
};

const STORAGE_KEY = "somna_agent_models";

export const AGENT_ROLE_META: {
  key: AgentModelRole;
  alias: string;
  title: string;
  hint: string;
}[] = [
  { key: "taskframe", alias: "agent-taskframe", title: "任务定调", hint: "意图与路由 JSON，宜快宜省" },
  { key: "planner", alias: "agent-planner", title: "规划", hint: "拆解任务与 TODO" },
  { key: "executor", alias: "agent-executor", title: "执行环", hint: "工具调用主循环" },
  { key: "coder", alias: "agent-coder", title: "写代码 / 调试", hint: "偏重代码与排错" },
  { key: "reasoner", alias: "agent-reasoner", title: "强推理", hint: "复杂推理与反思" },
  { key: "longctx", alias: "agent-longctx", title: "长文档", hint: "长上下文场景（预留）" },
  { key: "cheap", alias: "agent-cheap", title: "摘要 / 压缩", hint: "上下文压缩与轻量调用" },
  { key: "skill", alias: "agent-skill", title: "技能选择", hint: "判断本轮任务是否使用 Skill 及加载哪些资源" },
];

/**
 * Agent 模型页下拉选项（文本链路为主，不含 Qwen VL；多模态仅出现在 MCP 工具配置）。
 * DeepSeek 见 https://api-docs.deepseek.com/zh-cn/
 */
export const MODEL_CHOICES: { value: string; label: string; group: string }[] = [
  { group: "Kimi (Moonshot)", value: "kimi-k2-0905", label: "Kimi K2 0905" },
  { group: "Kimi (Moonshot)", value: "kimi-k2-turbo", label: "Kimi K2 Turbo" },
  { group: "Kimi (Moonshot)", value: "kimi-k2.6", label: "Kimi K2.6" },
  { group: "Qwen (DashScope)", value: "qwen3-max", label: "Qwen3 Max" },
  { group: "Qwen (DashScope)", value: "qwen-plus", label: "Qwen Plus" },
  { group: "Qwen (DashScope)", value: "qwen-turbo", label: "Qwen Turbo" },
  { group: "DeepSeek", value: "deepseek-v4-flash", label: "DeepSeek V4 Flash" },
  { group: "DeepSeek", value: "deepseek-v4-pro", label: "DeepSeek V4 Pro" },
];

function readOverrides(): Partial<Record<AgentModelRole, string>> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return {};
    return parsed as Partial<Record<AgentModelRole, string>>;
  } catch {
    return {};
  }
}

export function getResolvedAgentModels(): Record<AgentModelRole, string> {
  const o = readOverrides();
  return {
    taskframe: (o.taskframe?.trim() || DEFAULT_AGENT_MODELS.taskframe) as string,
    planner: (o.planner?.trim() || DEFAULT_AGENT_MODELS.planner) as string,
    executor: (o.executor?.trim() || DEFAULT_AGENT_MODELS.executor) as string,
    coder: (o.coder?.trim() || DEFAULT_AGENT_MODELS.coder) as string,
    reasoner: (o.reasoner?.trim() || DEFAULT_AGENT_MODELS.reasoner) as string,
    longctx: (o.longctx?.trim() || DEFAULT_AGENT_MODELS.longctx) as string,
    cheap: (o.cheap?.trim() || DEFAULT_AGENT_MODELS.cheap) as string,
    skill: (o.skill?.trim() || DEFAULT_AGENT_MODELS.skill) as string,
  };
}

export function setAgentModelOverride(role: AgentModelRole, value: string): void {
  if (typeof window === "undefined") return;
  const o = readOverrides();
  const v = value.trim();
  if (!v || v === DEFAULT_AGENT_MODELS[role]) {
    delete o[role];
  } else {
    o[role] = v;
  }
  if (Object.keys(o).length === 0) {
    window.localStorage.removeItem(STORAGE_KEY);
  } else {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(o));
  }
}

export function resetAgentModelsToDefaults(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(STORAGE_KEY);
}

export function getAgentModelOverride(role: AgentModelRole): string | undefined {
  const v = readOverrides()[role]?.trim();
  return v || undefined;
}
