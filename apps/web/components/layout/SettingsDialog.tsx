"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils/cn";
import { meRequest, type AuthUser } from "@/lib/api/auth";
import {
  AGENT_ROLE_META,
  DEFAULT_AGENT_MODELS,
  MODEL_CHOICES,
  type AgentModelRole,
  getResolvedAgentModels,
  resetAgentModelsToDefaults,
  setAgentModelOverride,
} from "@/lib/agent-models";

import { SettingsUserManagement } from "@/components/layout/SettingsUserManagement";
import { SettingsSkillManagement } from "@/components/layout/SettingsSkillManagement";
import { SettingsInviteManagement } from "@/components/layout/SettingsInviteManagement";
import {
  SettingsPointAccount,
  SettingsPointPlans,
  SettingsPointUsage,
} from "@/components/layout/SettingsPointAccount";
import {
  DEFAULT_MCP_TOOL_MODELS,
  MCP_TOOL_MODEL_META,
  choicesForMcpTool,
  getResolvedMcpToolModels,
  resetMcpToolModelsToDefaults,
  setMcpToolModelOverride,
  type McpToolModelKey,
} from "@/lib/mcp-tool-models";
import {
  type ExecutorEngine,
  executorEngineLabel,
  getExecutorEngine,
  setExecutorEngine,
} from "@/lib/executor-engine";

type SettingsSection =
  | "account"
  | "usage"
  | "points"
  | "mode"
  | "models"
  | "mcp_tools"
  | "skills"
  | "invites"
  | "users";

export function SettingsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const [section, setSection] = useState<SettingsSection>("account");
  const [mode, setMode] = useState<ExecutorEngine>("native");
  const [models, setModels] = useState<Record<AgentModelRole, string>>(() => getResolvedAgentModels());
  const [mcpModels, setMcpModels] = useState<Record<McpToolModelKey, string>>(() => getResolvedMcpToolModels());
  const [meProfile, setMeProfile] = useState<AuthUser | null>(null);

  const isAdmin = meProfile?.role === "admin";

  useEffect(() => {
    if (!isAdmin && (section === "users" || section === "invites")) setSection("account");
  }, [isAdmin, section]);

  useEffect(() => {
    if (open) {
      setMode(getExecutorEngine());
      setModels(getResolvedAgentModels());
      setMcpModels(getResolvedMcpToolModels());
      setSection("account");
      void meRequest().then(setMeProfile);
    }
  }, [open]);

  function saveMode(next: ExecutorEngine) {
    setExecutorEngine(next);
    setMode(next);
  }

  function onModelChange(role: AgentModelRole, value: string) {
    setAgentModelOverride(role, value);
    setModels(getResolvedAgentModels());
  }

  function onResetModels() {
    resetAgentModelsToDefaults();
    setModels(getResolvedAgentModels());
  }

  function onResetMcpModels() {
    resetMcpToolModelsToDefaults();
    setMcpModels(getResolvedMcpToolModels());
  }

  function onMcpModelChange(key: McpToolModelKey, value: string) {
    setMcpToolModelOverride(key, value);
    setMcpModels(getResolvedMcpToolModels());
  }

  const agentModelGroups = [...new Set(MODEL_CHOICES.map((c) => c.group))];

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px]" />
        <Dialog.Content
          className={cn(
            "fixed left-[50%] top-[50%] z-50 flex translate-x-[-50%] translate-y-[-50%]",
            ["account", "usage", "points", "skills", "invites", "users"].includes(section)
              ? "h-[min(900px,calc(100vh-1.5rem))] w-[min(1120px,calc(100vw-1.5rem))]"
              : "h-[min(640px,calc(100vh-2rem))] w-[min(760px,calc(100vw-2rem))]",
            "overflow-hidden rounded-2xl border bg-background shadow-lg outline-none",
          )}
        >
          <div className="flex h-full min-h-0 w-full">
            <aside className="w-[168px] shrink-0 border-r bg-muted/30 px-3 py-4">
              <p className="px-2 pb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">设置</p>
              <nav className="space-y-1">
                <button
                  type="button"
                  onClick={() => setSection("account")}
                  className={cn(
                    "w-full rounded-lg px-2 py-2 text-left text-sm outline-none transition focus-visible:ring-2 focus-visible:ring-ring",
                    section === "account" ? "bg-background font-medium shadow-sm" : "text-muted-foreground hover:bg-muted/80",
                  )}
                >
                  账户
                </button>
                <button
                  type="button"
                  onClick={() => setSection("usage")}
                  className={cn(
                    "w-full rounded-lg px-2 py-2 text-left text-sm outline-none transition focus-visible:ring-2 focus-visible:ring-ring",
                    section === "usage" ? "bg-background font-medium shadow-sm" : "text-muted-foreground hover:bg-muted/80",
                  )}
                >
                  使用情况
                </button>
                <button
                  type="button"
                  onClick={() => setSection("points")}
                  className={cn(
                    "w-full rounded-lg px-2 py-2 text-left text-sm outline-none transition focus-visible:ring-2 focus-visible:ring-ring",
                    section === "points" ? "bg-background font-medium shadow-sm" : "text-muted-foreground hover:bg-muted/80",
                  )}
                >
                  积分管理
                </button>
                <div className="my-3 border-t" />
                <button
                  type="button"
                  onClick={() => setSection("mode")}
                  className={cn(
                    "w-full rounded-lg px-2 py-2 text-left text-sm outline-none transition focus-visible:ring-2 focus-visible:ring-ring",
                    section === "mode" ? "bg-background font-medium shadow-sm" : "text-muted-foreground hover:bg-muted/80",
                  )}
                >
                  Agent 运行模式
                </button>
                <button
                  type="button"
                  onClick={() => setSection("models")}
                  className={cn(
                    "w-full rounded-lg px-2 py-2 text-left text-sm outline-none transition focus-visible:ring-2 focus-visible:ring-ring",
                    section === "models" ? "bg-background font-medium shadow-sm" : "text-muted-foreground hover:bg-muted/80",
                  )}
                >
                  Agent 模型配置
                </button>
                <button
                  type="button"
                  onClick={() => setSection("mcp_tools")}
                  className={cn(
                    "w-full rounded-lg px-2 py-2 text-left text-sm outline-none transition focus-visible:ring-2 focus-visible:ring-ring",
                    section === "mcp_tools" ? "bg-background font-medium shadow-sm" : "text-muted-foreground hover:bg-muted/80",
                  )}
                >
                  MCP 模型配置
                </button>
                <button
                  type="button"
                  onClick={() => setSection("skills")}
                  className={cn(
                    "w-full rounded-lg px-2 py-2 text-left text-sm outline-none transition focus-visible:ring-2 focus-visible:ring-ring",
                    section === "skills" ? "bg-background font-medium shadow-sm" : "text-muted-foreground hover:bg-muted/80",
                  )}
                >
                  技能管理
                </button>
                {isAdmin ? (
                  <>
                    <button
                      type="button"
                      onClick={() => setSection("invites")}
                      className={cn(
                        "w-full rounded-lg px-2 py-2 text-left text-sm outline-none transition focus-visible:ring-2 focus-visible:ring-ring",
                        section === "invites" ? "bg-background font-medium shadow-sm" : "text-muted-foreground hover:bg-muted/80",
                      )}
                    >
                      邀请码管理
                    </button>
                    <button
                      type="button"
                      onClick={() => setSection("users")}
                      className={cn(
                        "w-full rounded-lg px-2 py-2 text-left text-sm outline-none transition focus-visible:ring-2 focus-visible:ring-ring",
                        section === "users" ? "bg-background font-medium shadow-sm" : "text-muted-foreground hover:bg-muted/80",
                      )}
                    >
                      用户管理
                    </button>
                  </>
                ) : null}
              </nav>
            </aside>

            <div className="flex min-w-0 flex-1 flex-col">
              <div className="flex items-start justify-between gap-3 border-b px-5 py-4">
                <div className="min-w-0 pr-2">
                  <Dialog.Title className="text-base font-semibold">
                    {section === "account"
                      ? "账户"
                      : section === "usage"
                        ? "使用情况"
                        : section === "points"
                          ? "积分管理"
                          : section === "mode"
                      ? "Agent 运行模式"
                      : section === "models"
                        ? "Agent 模型配置"
                        : section === "mcp_tools"
                          ? "MCP 模型配置"
                          : section === "skills"
                            ? "技能管理"
                            : section === "invites"
                              ? "邀请码管理"
                              : "用户管理"}
                  </Dialog.Title>
                  <Dialog.Description
                    className={cn(
                      "mt-1 text-sm text-muted-foreground",
                      ["account", "usage", "points"].includes(section) && "sr-only",
                    )}
                  >
                    {section === "account" ? (
                      <>查看当前账户、套餐与积分状态。</>
                    ) : section === "usage" ? (
                      <>查看积分变更流水。</>
                    ) : section === "points" ? (
                      <>使用邀请码升级套餐或充值永久积分。</>
                    ) : section === "mode" ? (
                      <>选择执行引擎（经 LiteLLM；<span className="whitespace-nowrap">agent-*</span> 为网关别名）。</>
                    ) : section === "models" ? (
                      <>
                        各 Agent 节点走网关{" "}
                        <code className="rounded bg-muted px-1 text-xs">model_group_alias</code>；与 MCP Hub 工具模型无关。
                        DeepSeek 见{" "}
                        <a
                          href="https://api-docs.deepseek.com/zh-cn/"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-medium text-primary underline-offset-4 hover:underline"
                        >
                          文档
                        </a>
                        。
                      </>
                    ) : section === "mcp_tools" ? (
                      <>
                        按工具名指定默认 <code className="rounded bg-muted px-1 text-xs">model</code>；调用时若 LLM
                        未传参则使用此处（经会话提交到 agent-core）。
                      </>
                    ) : section === "skills" ? (
                      <>管理 Claude 标准 Skill；启用后会在匹配任务中自动注入执行上下文。</>
                    ) : section === "invites" ? (
                      <>生成与审计套餐升级、永久积分充值邀请码，并维护永久积分。</>
                    ) : (
                      <>查看与维护已注册用户信息；仅「活跃」用户可登录。</>
                    )}
                  </Dialog.Description>
                </div>
                <Dialog.Close asChild>
                  <Button size="icon" variant="ghost" className="shrink-0" aria-label="关闭">
                    <X className="size-4" />
                  </Button>
                </Dialog.Close>
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
                {section === "account" ? (
                  <SettingsPointAccount profile={meProfile} onGetPoints={() => setSection("points")} />
                ) : section === "usage" ? (
                  <SettingsPointUsage />
                ) : section === "points" ? (
                  <SettingsPointPlans />
                ) : section === "skills" ? (
                  <SettingsSkillManagement isAdmin={isAdmin} currentUserId={meProfile?.id ?? null} />
                ) : section === "invites" && isAdmin ? (
                  <SettingsInviteManagement currentUserId={meProfile?.id ?? null} />
                ) : section === "users" && isAdmin ? (
                  <SettingsUserManagement currentUserId={meProfile?.id ?? null} />
                ) : section === "users" ? (
                  <p className="text-sm text-muted-foreground">仅管理员可访问用户管理。</p>
                ) : section === "mode" ? (
                  <div className="space-y-3">
                    <button
                      type="button"
                      onClick={() => saveMode("native")}
                      className={cn(
                        "w-full rounded-xl border px-4 py-3 text-left text-sm transition",
                        mode === "native"
                          ? "border-primary bg-primary/5 shadow-sm"
                          : "border-transparent bg-muted/40 hover:bg-muted/60",
                      )}
                    >
                      <p className="font-medium">{executorEngineLabel("native")}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        Chat Completions（<code className="rounded bg-muted px-1">/v1</code>），当前默认实现。
                      </p>
                    </button>
                    <button
                      type="button"
                      onClick={() => saveMode("anthropic")}
                      className={cn(
                        "w-full rounded-xl border px-4 py-3 text-left text-sm transition",
                        mode === "anthropic"
                          ? "border-primary bg-primary/5 shadow-sm"
                          : "border-transparent bg-muted/40 hover:bg-muted/60",
                      )}
                    >
                      <p className="font-medium">{executorEngineLabel("anthropic")}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        Messages API（<code className="rounded bg-muted px-1">/v1/messages</code> 经 LiteLLM），工具与沙盒与模式一相同。
                      </p>
                    </button>
                  </div>
                ) : section === "models" ? (
                  <div className="space-y-5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-xs text-muted-foreground">
                        默认各角色为 DeepSeek V4 Pro；多模态模型仅在「MCP 模型配置」中选择。修改写入本机浏览器，随下一条消息提交。
                      </p>
                      <Button type="button" variant="outline" size="sm" onClick={onResetModels}>
                        恢复 Agent 模型默认
                      </Button>
                    </div>
                    <div className="space-y-4">
                      {AGENT_ROLE_META.map((row) => (
                        <div key={row.key} className="grid gap-1.5 sm:grid-cols-[minmax(0,200px)_1fr] sm:items-center">
                          <div>
                            <p className="text-sm font-medium">
                              {row.alias}{" "}
                              <span className="font-normal text-muted-foreground">· {row.title}</span>
                            </p>
                            <p className="text-xs text-muted-foreground">{row.hint}</p>
                            <p className="text-[11px] text-muted-foreground/80">
                              默认：{DEFAULT_AGENT_MODELS[row.key]}
                            </p>
                          </div>
                          <select
                            id={`model-${row.key}`}
                            value={models[row.key]}
                            onChange={(e) => onModelChange(row.key, e.target.value)}
                            className={cn(
                              "h-9 w-full max-w-md rounded-md border border-input bg-background px-2 text-sm",
                              "ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                            )}
                          >
                            {!MODEL_CHOICES.some((c) => c.value === models[row.key]) ? (
                              <option value={models[row.key]}>{models[row.key]}（自定义）</option>
                            ) : null}
                            {agentModelGroups.map((g) => (
                              <optgroup key={g} label={g}>
                                {MODEL_CHOICES.filter((c) => c.group === g).map((c) => (
                                  <option key={c.value} value={c.value}>
                                    {c.label}
                                  </option>
                                ))}
                              </optgroup>
                            ))}
                          </select>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : section === "mcp_tools" ? (
                  <div className="space-y-5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-xs text-muted-foreground">
                        与 mcp-hub 各工具默认一致；下发起会话消息时一并提交。
                      </p>
                      <Button type="button" variant="outline" size="sm" onClick={onResetMcpModels}>
                        恢复 MCP 默认
                      </Button>
                    </div>
                    <div className="space-y-4">
                      {MCP_TOOL_MODEL_META.map((row) => {
                        const choices = choicesForMcpTool(row.key);
                        const cgroups = [...new Set(choices.map((c) => c.group))];
                        return (
                          <div
                            key={row.key}
                            className="grid gap-1.5 sm:grid-cols-[minmax(0,200px)_1fr] sm:items-center"
                          >
                            <div>
                              <p className="text-sm font-medium">
                                <code className="rounded bg-muted px-1 text-xs">{row.title}</code>
                              </p>
                              <p className="text-xs text-muted-foreground">{row.hint}</p>
                              <p className="text-[11px] text-muted-foreground/80">
                                默认：{DEFAULT_MCP_TOOL_MODELS[row.key]}
                              </p>
                            </div>
                            <select
                              id={`mcp-model-${row.key}`}
                              value={mcpModels[row.key]}
                              onChange={(e) => onMcpModelChange(row.key, e.target.value)}
                              className={cn(
                                "h-9 w-full max-w-md rounded-md border border-input bg-background px-2 text-sm",
                                "ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                              )}
                            >
                              {!choices.some((c) => c.value === mcpModels[row.key]) ? (
                                <option value={mcpModels[row.key]}>{mcpModels[row.key]}（自定义）</option>
                              ) : null}
                              {cgroups.map((g) => (
                                <optgroup key={g} label={g}>
                                  {choices
                                    .filter((c) => c.group === g)
                                    .map((c) => (
                                      <option key={c.value} value={c.value}>
                                        {c.label}
                                      </option>
                                    ))}
                                </optgroup>
                              ))}
                            </select>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
              </div>

              {["account", "usage", "points"].includes(section) ? null : (
                <p className="border-t px-5 py-3 text-xs text-muted-foreground">
                  Agent 运行模式、Agent 模型配置与 MCP 模型配置保存在本机浏览器；技能管理在服务端保存并影响后续任务。
                  {section === "invites" ? " 邀请码与积分调整在服务端即时生效。" : ""}
                  {isAdmin ? " 用户管理在服务端即时生效。" : ""}
                </p>
              )}
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
