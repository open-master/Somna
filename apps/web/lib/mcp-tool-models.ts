/**
 * MCP Hub 内置工具的默认模型（与 mcp-hub `app/config.py` 对齐）。
 * 与 Agent LLM 角色（LiteLLM）分离，按工具名存储。
 */

export type McpToolModelKey =
  | "visual_critique"
  | "wan_text2image"
  | "wan_t2v"
  | "wan_i2v"
  | "wan_r2v"
  | "wan_video_edit"
  | "minimax_tts";

/** 与 mcp-hub 环境默认值一致 */
export const DEFAULT_MCP_TOOL_MODELS: Record<McpToolModelKey, string> = {
  visual_critique: "qwen3-vl-plus",
  wan_text2image: "wan2.2-t2i-flash",
  wan_t2v: "wan2.2-t2v-plus",
  wan_i2v: "happyhorse-1.0-i2v",
  wan_r2v: "wan2.7-r2v",
  wan_video_edit: "wan2.7-videoedit",
  minimax_tts: "speech-2.6-hd",
};

const STORAGE_KEY = "somna_mcp_tool_models";
const LEGACY_AGENT_KEY = "somna_agent_models";

function _readMcpRaw(): Partial<Record<McpToolModelKey, string>> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return {};
    return parsed as Partial<Record<McpToolModelKey, string>>;
  } catch {
    return {};
  }
}

function _migrateLegacyVisualCritique(): void {
  if (typeof window === "undefined") return;
  try {
    const raw = window.localStorage.getItem(LEGACY_AGENT_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const vc = parsed?.visual_critique;
    if (typeof vc !== "string" || !vc.trim()) return;
    const cur = _readMcpRaw();
    if (cur.visual_critique) {
      delete parsed.visual_critique;
      window.localStorage.setItem(LEGACY_AGENT_KEY, JSON.stringify(parsed));
      return;
    }
    const next = { ...cur, visual_critique: vc.trim() };
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    delete parsed.visual_critique;
    if (Object.keys(parsed).length === 0) {
      window.localStorage.removeItem(LEGACY_AGENT_KEY);
    } else {
      window.localStorage.setItem(LEGACY_AGENT_KEY, JSON.stringify(parsed));
    }
  } catch {
    /* ignore */
  }
}

/** 将旧键 `wan_text2video` 合并到 `wan_t2v` */
function _migrateLegacyWanText2video(): void {
  if (typeof window === "undefined") return;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const legacy = parsed.wan_text2video;
    if (typeof legacy !== "string" || !legacy.trim()) return;
    delete parsed.wan_text2video;
    if (!parsed.wan_t2v) {
      parsed.wan_t2v = legacy.trim();
    }
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(parsed));
  } catch {
    /* ignore */
  }
}

export const MCP_TOOL_MODEL_META: {
  key: McpToolModelKey;
  title: string;
  hint: string;
}[] = [
  {
    key: "visual_critique",
    title: "visual_critique",
    hint: "版式/审美评审（百炼多模态）；未传 tool 参数 model 时使用",
  },
  {
    key: "wan_text2image",
    title: "wan_text2image",
    hint: "万相文生图；未传 model 时使用",
  },
  {
    key: "wan_t2v",
    title: "wan_t2v",
    hint: "文生视频；未传 model 时使用",
  },
  {
    key: "wan_i2v",
    title: "wan_i2v",
    hint: "图生视频（首帧等）；未传 model 时使用",
  },
  {
    key: "wan_r2v",
    title: "wan_r2v",
    hint: "参考生视频；未传 model 时使用",
  },
  {
    key: "wan_video_edit",
    title: "wan_video_edit",
    hint: "视频编辑；未传 model 时使用",
  },
  {
    key: "minimax_tts",
    title: "minimax_tts",
    hint: "MiniMax 语音合成；未传 model 时使用",
  },
];

/** 视觉评审：多模态 + 文本（与 Agent 列表分离，此处保留 VL） */
export const MCP_MODEL_CHOICES_VISUAL: { value: string; label: string; group: string }[] = [
  { group: "Qwen (DashScope)", value: "qwen3-vl-plus", label: "Qwen3 VL Plus" },
  { group: "Qwen (DashScope)", value: "qwen3-vl-flash", label: "Qwen3 VL Flash" },
  { group: "Qwen (DashScope)", value: "qwen3-max", label: "Qwen3 Max" },
];

/** 万相文生图 */
export const MCP_MODEL_CHOICES_WAN_T2I: { value: string; label: string; group: string }[] = [
  { group: "万相", value: "wan2.2-t2i-flash", label: "Wan 2.2 T2I Flash" },
  { group: "万相", value: "wan2.2-t2i-plus", label: "Wan 2.2 T2I Plus" },
  { group: "万相", value: "wan2.5-t2i-preview", label: "Wan 2.5 T2I Preview" },
  { group: "万相", value: "wan2.6-t2i", label: "Wan 2.6 T2I" },
];

/** 文生视频 t2v */
export const MCP_MODEL_CHOICES_WAN_T2V: { value: string; label: string; group: string }[] = [
  { group: "HappyHorse", value: "happyhorse-1.0-t2v", label: "HappyHorse 1.0 文生视频" },
  { group: "万相 2.7", value: "wan2.7-t2v-2026-04-25", label: "万相 2.7 文生视频" },
  { group: "万相 2.2", value: "wan2.2-t2v-plus", label: "Wan 2.2 T2V Plus" },
  { group: "万相 2.2", value: "wan2.2-t2v-flash", label: "Wan 2.2 T2V Flash" },
];

/** 图生视频 i2v */
export const MCP_MODEL_CHOICES_WAN_I2V: { value: string; label: string; group: string }[] = [
  { group: "HappyHorse", value: "happyhorse-1.0-i2v", label: "HappyHorse 1.0 图生视频（首帧）" },
  { group: "万相 2.7", value: "wan2.7-i2v-2026-04-25", label: "万相 2.7 图生视频" },
];

/** 参考生视频 r2v */
export const MCP_MODEL_CHOICES_WAN_R2V: { value: string; label: string; group: string }[] = [
  { group: "HappyHorse", value: "happyhorse-1.0-r2v", label: "HappyHorse 1.0 参考生视频" },
  { group: "万相 2.7", value: "wan2.7-r2v", label: "万相 2.7 参考生视频" },
];

/** 视频编辑 */
export const MCP_MODEL_CHOICES_WAN_VIDEO_EDIT: { value: string; label: string; group: string }[] = [
  { group: "HappyHorse", value: "happyhorse-1.0-video-edit", label: "HappyHorse 1.0 视频编辑" },
  { group: "万相 2.7", value: "wan2.7-videoedit", label: "万相 2.7 视频编辑" },
];

/** MiniMax TTS */
export const MCP_MODEL_CHOICES_MINIMAX_TTS: { value: string; label: string; group: string }[] = [
  { group: "MiniMax", value: "speech-2.6-hd", label: "Speech 2.6 HD" },
  { group: "MiniMax", value: "speech-2.6-turbo", label: "Speech 2.6 Turbo" },
  { group: "MiniMax", value: "speech-01-hd", label: "Speech 01 HD" },
  { group: "MiniMax", value: "speech-01-turbo", label: "Speech 01 Turbo" },
];

export function choicesForMcpTool(key: McpToolModelKey): { value: string; label: string; group: string }[] {
  switch (key) {
    case "visual_critique":
      return MCP_MODEL_CHOICES_VISUAL;
    case "wan_text2image":
      return MCP_MODEL_CHOICES_WAN_T2I;
    case "wan_t2v":
      return MCP_MODEL_CHOICES_WAN_T2V;
    case "wan_i2v":
      return MCP_MODEL_CHOICES_WAN_I2V;
    case "wan_r2v":
      return MCP_MODEL_CHOICES_WAN_R2V;
    case "wan_video_edit":
      return MCP_MODEL_CHOICES_WAN_VIDEO_EDIT;
    case "minimax_tts":
      return MCP_MODEL_CHOICES_MINIMAX_TTS;
    default:
      return [];
  }
}

function readOverrides(): Partial<Record<McpToolModelKey, string>> {
  _migrateLegacyVisualCritique();
  _migrateLegacyWanText2video();
  return _readMcpRaw();
}

export function getResolvedMcpToolModels(): Record<McpToolModelKey, string> {
  const o = readOverrides();
  return {
    visual_critique: (o.visual_critique?.trim() || DEFAULT_MCP_TOOL_MODELS.visual_critique) as string,
    wan_text2image: (o.wan_text2image?.trim() || DEFAULT_MCP_TOOL_MODELS.wan_text2image) as string,
    wan_t2v: (o.wan_t2v?.trim() || DEFAULT_MCP_TOOL_MODELS.wan_t2v) as string,
    wan_i2v: (o.wan_i2v?.trim() || DEFAULT_MCP_TOOL_MODELS.wan_i2v) as string,
    wan_r2v: (o.wan_r2v?.trim() || DEFAULT_MCP_TOOL_MODELS.wan_r2v) as string,
    wan_video_edit: (o.wan_video_edit?.trim() || DEFAULT_MCP_TOOL_MODELS.wan_video_edit) as string,
    minimax_tts: (o.minimax_tts?.trim() || DEFAULT_MCP_TOOL_MODELS.minimax_tts) as string,
  };
}

export function setMcpToolModelOverride(key: McpToolModelKey, value: string): void {
  if (typeof window === "undefined") return;
  const o = readOverrides();
  const v = value.trim();
  if (!v || v === DEFAULT_MCP_TOOL_MODELS[key]) {
    delete o[key];
  } else {
    o[key] = v;
  }
  if (Object.keys(o).length === 0) {
    window.localStorage.removeItem(STORAGE_KEY);
  } else {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(o));
  }
}

export function resetMcpToolModelsToDefaults(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(STORAGE_KEY);
}
