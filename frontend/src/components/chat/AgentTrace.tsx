"use client";

import { createContext, useContext, useState } from "react";
import { Check, Loader2, Terminal, TriangleAlert } from "lucide-react";
import type { ChatMessage } from "@/lib/types";
import type { Locale } from "@/lib/i18n";

export const AgentActionsContext = createContext<{
  approve: (messageId: string, callId: string, approved: boolean) => Promise<void>;
  resume: (messageId: string) => Promise<void>;
  running: boolean;
} | null>(null);

const zhStatus: Record<string, string> = {
  running: "执行中", waiting_approval: "等待授权", done: "已完成", error: "执行失败",
  cancelled: "已停止", denied: "已拒绝", interrupted: "执行中断",
};
const zhTools: Record<string, string> = {
  workspace_list: "浏览目录", workspace_read: "读取文件", workspace_search: "搜索文件",
  workspace_write: "写入文件", workspace_edit: "修改文件", run_command: "执行命令",
};

export function AgentTrace({ message, locale }: { message: ChatMessage; locale: Locale }) {
  const actions = useContext(AgentActionsContext);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const agent = message.workspaceAgent;
  if (!agent) return null;
  const zh = locale === "zh";
  const invoke = async (id: string, action: () => Promise<void>) => {
    setPending(id);
    setError("");
    try { await action(); } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setPending(null); }
  };

  return (
    <section aria-label={zh ? "工作区执行记录" : "Workspace activity"} className="mb-3 overflow-hidden rounded-xl border border-line bg-panel text-xs" data-testid="agent-trace">
      <div className="flex items-center gap-2 px-3 py-2.5">
        {agent.status === "running" ? <Loader2 className="h-4 w-4 animate-spin text-accent" /> : <Terminal className="h-4 w-4 text-accent" />}
        <span className="font-medium">{zh ? "工作区 Agent" : "Workspace agent"}</span>
        <span className="ml-auto text-muted">{zh ? zhStatus[agent.status] : agent.status}</span>
      </div>
      <p className="break-all border-t border-line px-3 py-2 font-mono text-[11px] text-muted">{agent.workspacePath}</p>
      <div className="max-h-[28rem] overflow-auto">
        {agent.calls.map((call) => {
          const failed = call.status === "error" || call.result?.ok === false;
          const waiting = call.status === "waiting_approval" && message.status === "streaming";
          const label = zh ? zhTools[call.tool] || call.tool : call.tool;
          const details = call.tool === "run_command"
            ? String(call.arguments.command || "")
            : String(call.arguments.path || call.arguments.query || ".");
          return (
            <details className="border-t border-line px-3 py-2" key={call.id} open={waiting || call.status === "running" || failed || undefined}>
              <summary className="cursor-pointer break-all leading-5">
                <span className="mr-1 inline-flex align-middle">
                  {call.status === "running" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : failed ? <TriangleAlert className="h-3.5 w-3.5 text-red-600" /> : <Check className="h-3.5 w-3.5 text-muted" />}
                </span>
                <span className="font-medium">{label}</span>{" · "}
                <span className={failed ? "text-red-600" : "text-muted"}>{zh ? zhStatus[failed ? "error" : call.status] : failed ? "error" : call.status}</span>
                <span className="ml-2 font-mono text-muted">{details.slice(0, 140)}</span>
              </summary>
              <pre className="my-2 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-md bg-surface p-2 font-mono text-[11px]">{JSON.stringify(call.arguments, null, 2)}</pre>
              {call.result ? <ToolResult result={call.result} locale={locale} /> : null}
              {waiting && actions ? (
                <div className="my-2 flex flex-wrap items-center gap-2">
                  <p className="w-full text-muted">{zh ? "将在本机执行此操作。请检查命令或文件修改内容。" : "This action runs on your computer. Review the command or file changes."}</p>
                  <button className="rounded-md bg-accent px-3 py-1.5 text-surface disabled:opacity-50" disabled={pending !== null} onClick={() => void invoke(call.id, () => actions.approve(message.id, call.id, true))} type="button">{zh ? "允许此次操作" : "Allow once"}</button>
                  <button className="rounded-md border border-line px-3 py-1.5 disabled:opacity-50" disabled={pending !== null} onClick={() => void invoke(call.id, () => actions.approve(message.id, call.id, false))} type="button">{zh ? "拒绝" : "Deny"}</button>
                  {pending === call.id ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                </div>
              ) : null}
            </details>
          );
        })}
      </div>
      {agent.error ? <p className="px-3 py-2 text-red-600">{agent.error}</p> : null}
      {(message.status === "interrupted" || message.status === "error") && actions ? (
        <div className="space-y-2 border-t border-line p-3">
          <p className="text-muted">{zh ? "文件修改保留在工作区。继续时会使用已保存的执行记录。" : "Workspace changes are preserved. Continue from the saved execution record."}</p>
          <button className="rounded-md border border-line bg-surface px-3 py-1.5 disabled:opacity-50" disabled={pending !== null || actions.running} onClick={() => void invoke("resume", () => actions.resume(message.id))} type="button">{zh ? "继续任务" : "Resume task"}</button>
        </div>
      ) : null}
      {error ? <p role="alert" className="px-3 py-2 text-red-600">{error}</p> : null}
    </section>
  );
}

function ToolResult({ result, locale }: { result: Record<string, unknown>; locale: Locale }) {
  const textKeys = ["stdout", "stderr", "diff", "error"];
  const hasText = textKeys.some((key) => typeof result[key] === "string" && result[key]);
  return (
    <div className="space-y-1.5">
      {result.exit_code !== undefined ? <p className="text-muted">exit: {String(result.exit_code)}{result.timed_out ? " · timed out" : ""}{result.duration_seconds !== undefined ? ` · ${String(result.duration_seconds)}s` : ""}</p> : null}
      {typeof result.cwd === "string" ? <p className="break-all font-mono text-muted">cwd: {result.cwd}</p> : null}
      {hasText ? textKeys.map((key) => typeof result[key] === "string" && result[key] ? (
        <div key={key}>
          <p className="text-muted">{key}</p>
          <pre className={`max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md bg-surface p-2 font-mono text-[11px] ${key === "error" || key === "stderr" ? "text-red-600" : ""}`}>{String(result[key])}</pre>
        </div>
      ) : null) : <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md bg-surface p-2 font-mono text-[11px]">{JSON.stringify(result, null, 2)}</pre>}
      {result.truncated || result.stdout_truncated || result.stderr_truncated || result.output_truncated || result.log_truncated || result.diff_truncated ? (
        <p className="text-amber-700">{locale === "zh" ? "部分输出或差异已截断" : "Some output or diff was truncated"}</p>
      ) : null}
      {result.log_paths ? (
        <details className="py-1 text-muted">
          <summary className="cursor-pointer">{locale === "zh" ? "本地日志位置" : "Local log paths"}</summary>
          <pre className="mt-1 whitespace-pre-wrap break-all font-mono text-[11px]">{JSON.stringify(result.log_paths, null, 2)}</pre>
        </details>
      ) : null}
    </div>
  );
}
