"use client";

import { FormEvent, useState } from "react";
import { CheckCircle2, Loader2, PencilLine, XCircle } from "lucide-react";
import type { Locale } from "@/lib/i18n";
import type { ChatMessage, PlanStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { StreamingMarkdown } from "@/components/chat/StreamingMarkdown";

type PlanCardProps = {
  content: string;
  isStreaming: boolean;
  locale: Locale;
  message: ChatMessage;
  onApprovePlan: (messageId: string, planId: string) => void;
  onRevisePlan: (messageId: string, planId: string, feedback: string) => void;
};

const statusCopy: Record<PlanStatus, { zh: string; en: string }> = {
  streaming: { zh: "生成中", en: "Generating" },
  awaiting_approval: { zh: "等待确认", en: "Awaiting approval" },
  approved: { zh: "已批准", en: "Approved" },
  rejected: { zh: "已拒绝", en: "Rejected" },
  executing: { zh: "执行中", en: "Executing" },
  error: { zh: "出错", en: "Error" },
};

export function PlanCard({
  content,
  isStreaming,
  locale,
  message,
  onApprovePlan,
  onRevisePlan,
}: PlanCardProps) {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedback, setFeedback] = useState("");
  const plan = message.plan;
  const planId = plan?.planId;
  const status = plan?.status ?? (isStreaming ? "streaming" : "error");
  const canAct =
    Boolean(planId) &&
    status === "awaiting_approval" &&
    message.status !== "streaming" &&
    !isStreaming;
  const statusLabel = statusCopy[status]?.[locale] ?? status;
  const isBusy = status === "streaming" || status === "executing" || isStreaming;

  const submitRevision = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = feedback.trim();
    if (!canAct || !planId || !trimmed) return;
    onRevisePlan(message.id, planId, trimmed);
    setFeedback("");
    setFeedbackOpen(false);
  };

  return (
    <div className="overflow-hidden rounded-lg border border-line bg-surface shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-panel px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          {isBusy ? (
            <Loader2 className="h-4 w-4 shrink-0 animate-spin text-accent" />
          ) : status === "rejected" || status === "error" ? (
            <XCircle className="h-4 w-4 shrink-0 text-muted" />
          ) : (
            <CheckCircle2 className="h-4 w-4 shrink-0 text-accent" />
          )}
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-ink">
              Plan Mode Plan
            </div>
            <div className="text-xs text-muted">
              v{plan?.version ?? 1} · {statusLabel}
            </div>
          </div>
        </div>
        {planId && (
          <code className="max-w-full truncate rounded-md border border-line bg-surface px-2 py-1 text-xs text-muted">
            {planId}
          </code>
        )}
      </div>

      <div className="px-4 py-3">
        <StreamingMarkdown
          content={content || plan?.markdown || ""}
          emptyLabel={locale === "zh" ? "正在生成计划..." : "Generating plan..."}
          isStreaming={isStreaming}
          locale={locale}
        />
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-line px-3 py-2">
        <button
          className={cn(
            "inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium",
            canAct
              ? "bg-ink text-surface hover:opacity-85"
              : "cursor-not-allowed bg-line text-muted",
          )}
          disabled={!canAct || !planId}
          onClick={() => planId && onApprovePlan(message.id, planId)}
          type="button"
        >
          <CheckCircle2 className="h-4 w-4" />
          是，实施此计划
        </button>
        <button
          className={cn(
            "inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium",
            canAct
              ? "border-line text-ink hover:bg-panel"
              : "cursor-not-allowed border-line text-muted",
          )}
          disabled={!canAct || !planId}
          onClick={() => setFeedbackOpen((value) => !value)}
          type="button"
        >
          <PencilLine className="h-4 w-4" />
          否，我要修改
        </button>
      </div>

      {feedbackOpen && canAct && (
        <form className="border-t border-line p-3" onSubmit={submitRevision}>
          <textarea
            className="min-h-24 w-full resize-y rounded-lg border border-line bg-surface px-3 py-2 text-sm leading-6 text-ink outline-none focus:border-accent"
            onChange={(event) => setFeedback(event.target.value)}
            placeholder={
              locale === "zh"
                ? "写下你希望 AI 修改计划的地方"
                : "Describe what should change in the plan"
            }
            value={feedback}
          />
          <div className="mt-2 flex justify-end gap-2">
            <button
              className="rounded-lg px-3 py-2 text-sm text-muted hover:bg-panel hover:text-ink"
              onClick={() => setFeedbackOpen(false)}
              type="button"
            >
              取消
            </button>
            <button
              className="rounded-lg bg-ink px-3 py-2 text-sm font-medium text-surface disabled:cursor-not-allowed disabled:bg-line disabled:text-muted"
              disabled={!feedback.trim()}
              type="submit"
            >
              提交修改意见
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
