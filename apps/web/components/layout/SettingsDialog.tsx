"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils/cn";
import {
  AGENT_ROLE_META,
  DEFAULT_AGENT_MODELS,
  MODEL_CHOICES,
  type AgentModelRole,
  getResolvedAgentModels,
  resetAgentModelsToDefaults,
  setAgentModelOverride,
} from "@/lib/agent-models";
import {
  type ExecutorEngine,
  executorEngineLabel,
  getExecutorEngine,
  setExecutorEngine,
} from "@/lib/executor-engine";

type SettingsSection = "mode" | "models";

export function SettingsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const [section, setSection] = useState<SettingsSection>("mode");
  const [mode, setMode] = useState<ExecutorEngine>("native");
  const [models, setModels] = useState<Record<AgentModelRole, string>>(() => getResolvedAgentModels());

  useEffect(() => {
    if (open) {
      setMode(getExecutorEngine());
      setModels(getResolvedAgentModels());
      setSection("mode");
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

  const groups = [...new Set(MODEL_CHOICES.map((c) => c.group))];

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px]" />
        <Dialog.Content
          className={cn(
            "fixed left-[50%] top-[50%] z-50 flex h-[min(560px,calc(100vh-2rem))] w-[min(720px,calc(100vw-2rem))] translate-x-[-50%] translate-y-[-50%]",
            "overflow-hidden rounded-2xl border bg-background shadow-lg outline-none",
          )}
        >
          <div className="flex h-full min-h-0">
            <aside className="w-[168px] shrink-0 border-r bg-muted/30 px-3 py-4">
              <p className="px-2 pb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">设置</p>
              <nav className="space-y-1">
                <button
                  type="button"
                  onClick={() => setSection("mode")}
                  className={cn(
                    "w-full rounded-lg px-2 py-2 text-left text-sm transition",
                    section === "mode" ? "bg-background font-medium shadow-sm" : "text-muted-foreground hover:bg-muted/80",
                  )}
                >
                  Agent 模式
                </button>
                <button
                  type="button"
                  onClick={() => setSection("models")}
                  className={cn(
                    "w-full rounded-lg px-2 py-2 text-left text-sm transition",
                    section === "models" ? "bg-background font-medium shadow-sm" : "text-muted-foreground hover:bg-muted/80",
                  )}
                >
                  模型
                </button>
              </nav>
            </aside>

            <div className="flex min-w-0 flex-1 flex-col">
              <div className="flex items-start justify-between gap-3 border-b px-5 py-4">
                <div className="min-w-0 pr-2">
                  <Dialog.Title className="text-base font-semibold">
                    {section === "mode" ? "Agent 模式" : "模型"}
                  </Dialog.Title>
                  <Dialog.Description className="mt-1 text-sm text-muted-foreground">
                    {section === "mode" ? (
                      <>选择执行引擎（经 LiteLLM；<span className="whitespace-nowrap">agent-*</span> 为网关别名）。</>
                    ) : (
                      <>
                        与网关{" "}
                        <code className="rounded bg-muted px-1 text-xs">model_group_alias</code> 对齐；可覆盖默认。
                        DeepSeek 模型见{" "}
                        <a
                          href="https://api-docs.deepseek.com/zh-cn/"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-medium text-primary underline-offset-4 hover:underline"
                        >
                          DeepSeek API 文档
                        </a>
                        。
                      </>
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
                {section === "mode" ? (
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
                ) : (
                  <div className="space-y-5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-xs text-muted-foreground">
                        默认：与当前仓库 LiteLLM 配置一致；修改后写入本机浏览器，随下一条消息提交。
                      </p>
                      <Button type="button" variant="outline" size="sm" onClick={onResetModels}>
                        恢复模型默认
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
                            {groups.map((g) => (
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
                )}
              </div>

              <p className="border-t px-5 py-3 text-xs text-muted-foreground">
                Agent 模式与模型偏好均保存在本机浏览器；发送下一条消息时生效。
              </p>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
