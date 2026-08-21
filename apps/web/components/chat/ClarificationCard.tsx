"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, CircleHelp, Loader2, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { postMessage } from "@/lib/api/sessions";
import { getExecutorEngine } from "@/lib/executor-engine";
import { useChatStore } from "@/lib/store/chat";
import { usePlanStore } from "@/lib/store/plan";
import { useSessionStore } from "@/lib/store/session";
import { useTaskFrameStore } from "@/lib/store/taskFrame";

const DELEGATE_ANSWER = "由 Somna 根据目标选择合适方案";

export function ClarificationCard({ sessionId }: { sessionId: string }) {
  const phase = useSessionStore((state) => state.phase);
  const questions = useTaskFrameStore((state) => state.questions);
  const questionRunId = useTaskFrameStore((state) => state.runId);
  const clearTaskFrame = useTaskFrameStore((state) => state.clear);
  const clearPlan = usePlanStore((state) => state.clear);
  const pushUser = useChatStore((state) => state.pushUser);
  const rollbackLastUserMessage = useChatStore((state) => state.rollbackLastUserMessage);
  const setPhase = useSessionStore((state) => state.setPhase);
  const setRunId = useSessionStore((state) => state.setRunId);
  const upsertSession = useSessionStore((state) => state.upsertSession);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [customAnswers, setCustomAnswers] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setAnswers({});
    setCustomAnswers({});
    setError(null);
  }, [questionRunId]);

  const complete = useMemo(
    () =>
      questions.length > 0 &&
      questions.every(
        (question) =>
          Boolean(answers[question.id]?.trim()) || Boolean(customAnswers[question.id]?.trim()),
      ),
    [answers, customAnswers, questions],
  );

  if (phase !== "waiting_user" || questions.length === 0) return null;

  function choose(questionId: string, answer: string) {
    setAnswers((current) => ({ ...current, [questionId]: answer }));
    setCustomAnswers((current) => ({ ...current, [questionId]: "" }));
    setError(null);
  }

  function writeCustom(questionId: string, answer: string) {
    setCustomAnswers((current) => ({ ...current, [questionId]: answer }));
    if (answer.trim()) {
      setAnswers((current) => ({ ...current, [questionId]: "" }));
    }
    setError(null);
  }

  function delegateAll() {
    setAnswers(
      Object.fromEntries(questions.map((question) => [question.id, DELEGATE_ANSWER])),
    );
    setCustomAnswers({});
    setError(null);
  }

  async function submit() {
    if (!complete || submitting) return;
    const answerText = [
      "针对你的确认，我的选择如下：",
      ...questions.map((question, index) => {
        const answer = customAnswers[question.id]?.trim() || answers[question.id]?.trim();
        return `${index + 1}. ${question.prompt}\n回答：${answer}`;
      }),
    ].join("\n\n");

    setSubmitting(true);
    setError(null);
    pushUser(answerText);
    try {
      const response = await postMessage(sessionId, answerText, [], getExecutorEngine());
      clearPlan();
      clearTaskFrame();
      setPhase("planning");
      setRunId(response.run_id);
      const existing = useSessionStore
        .getState()
        .sessions.find((session) => session.id === sessionId);
      upsertSession({
        id: sessionId,
        title: existing?.title ?? "新会话",
        createdAt: existing?.createdAt,
        workflowId: existing?.workflowId ?? null,
        status: "running",
        runId: response.run_id,
        lastRunTerminal: null,
        awaitingUser: false,
        updatedAt: new Date().toISOString(),
      });
    } catch (cause) {
      rollbackLastUserMessage();
      setError(cause instanceof Error ? cause.message : "提交失败，请重试");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section
      className="mx-auto w-full max-w-3xl px-4 pb-3"
      aria-labelledby="clarification-title"
    >
      <div className="overflow-hidden rounded-2xl border border-amber-500/25 bg-[linear-gradient(145deg,hsl(var(--card)),hsl(var(--muted)/0.38))] shadow-[0_14px_40px_-26px_hsl(var(--foreground)/0.38)]">
        <header className="flex items-start justify-between gap-4 border-b border-border/70 px-4 py-3.5 sm:px-5">
          <div className="flex min-w-0 gap-3">
            <div className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-xl bg-amber-500/12 text-amber-600 dark:text-amber-400">
              <CircleHelp className="size-4" aria-hidden />
            </div>
            <div>
              <h2 id="clarification-title" className="text-sm font-semibold tracking-tight">
                执行前需要你的确认
              </h2>
              <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
                快速选择或补充说明，确认后我会继续处理任务。
              </p>
            </div>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-8 shrink-0 gap-1.5 rounded-full px-3 text-xs text-muted-foreground"
            onClick={delegateAll}
            disabled={submitting}
          >
            <Sparkles className="size-3.5" aria-hidden />
            全部交给 Somna
          </Button>
        </header>

        <div className="space-y-5 px-4 py-4 sm:px-5">
          {questions.map((question, index) => {
            const selected = answers[question.id] ?? "";
            const custom = customAnswers[question.id] ?? "";
            const choices = Array.from(new Set([...question.options, DELEGATE_ANSWER]));
            return (
              <fieldset key={question.id} disabled={submitting} className="space-y-2.5">
                <legend className="flex w-full gap-2 text-sm leading-6">
                  <span className="font-mono text-xs text-muted-foreground">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <span className="font-medium">{question.prompt}</span>
                </legend>
                <div className="flex flex-wrap gap-2 pl-6">
                  {choices.map((choice) => {
                    const active = selected === choice && !custom.trim();
                    return (
                      <button
                        key={choice}
                        type="button"
                        aria-pressed={active}
                        onClick={() => choose(question.id, choice)}
                        className={[
                          "inline-flex min-h-9 items-center gap-1.5 rounded-xl border px-3 py-1.5 text-left text-xs leading-5 transition",
                          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                          active
                            ? "border-foreground/25 bg-foreground text-background shadow-sm"
                            : "border-border/80 bg-background/70 text-foreground hover:border-foreground/25 hover:bg-accent",
                        ].join(" ")}
                      >
                        {active ? <Check className="size-3.5 shrink-0" aria-hidden /> : null}
                        {choice}
                      </button>
                    );
                  })}
                </div>
                {question.allow_custom ? (
                  <div className="pl-6">
                    <Textarea
                      value={custom}
                      onChange={(event) => writeCustom(question.id, event.target.value)}
                      placeholder="或者补充你的具体要求…"
                      rows={2}
                      className="min-h-16 resize-none rounded-xl bg-background/70 text-sm"
                    />
                  </div>
                ) : null}
              </fieldset>
            );
          })}
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-border/70 bg-background/45 px-4 py-3 sm:px-5">
          <p className="min-h-5 text-xs text-destructive" role="alert">
            {error}
          </p>
          <Button
            type="button"
            className="h-9 shrink-0 rounded-xl px-4"
            onClick={() => void submit()}
            disabled={!complete || submitting}
          >
            {submitting ? (
              <Loader2 className="mr-2 size-4 animate-spin" aria-hidden />
            ) : (
              <Check className="mr-2 size-4" aria-hidden />
            )}
            {submitting ? "正在继续" : "确认并继续"}
          </Button>
        </footer>
      </div>
    </section>
  );
}
