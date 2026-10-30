"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, File, Folder, FolderOpen, Loader2, RefreshCw, X } from "lucide-react";
import type { Locale } from "@/lib/i18n";
import type { WorkspaceListing, WorkspaceMode } from "@/lib/types";
import { fetchWorkspace, fetchWorkspaceFile, inspectWorkspace, pickWorkspaceFolder } from "@/lib/api";

type Props = {
  chatId: string | null;
  path: string | null;
  mode: WorkspaceMode;
  disabled: boolean;
  locale: Locale;
  onChange: (path: string | null, mode: WorkspaceMode) => Promise<void>;
};

export function WorkspacePanel({ chatId, path, mode, disabled, locale, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState(path || "");
  const [selectedMode, setSelectedMode] = useState(mode);
  const [listing, setListing] = useState<WorkspaceListing | null>(null);
  const [relativePath, setRelativePath] = useState(".");
  const [preview, setPreview] = useState<{ path: string; content: string; truncated?: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const pickerRequest = useRef<AbortController | null>(null);
  useEffect(() => () => pickerRequest.current?.abort(), []);
  const [error, setError] = useState("");
  const zh = locale === "zh";
  const act = async (action: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await action(); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  };
  const browse = (relative: string) => act(async () => {
    if (!path) return;
    const result = chatId ? await fetchWorkspace(chatId, relative) : await inspectWorkspace(path);
    setListing(result); setRelativePath(chatId ? relative : "."); setPreview(null);
  });

  return (
    <section className="shrink-0 border-b border-line bg-panel/50 px-3 py-2 sm:px-5" aria-label={zh ? "本地工作区" : "Local workspace"}>
      <button type="button" className="flex w-full items-center gap-2 text-left text-xs" onClick={() => {
        setOpen(!open);
        if (!open && path) void browse(".");
      }}>
        <FolderOpen className="h-4 w-4 shrink-0 text-accent" />
        <span className="min-w-0 flex-1 truncate" title={path || ""}>{path || (zh ? "绑定本地文件夹，让 Agent 在项目中工作" : "Open a local folder for your agent")}</span>
        {path ? <span className="shrink-0 text-muted">{mode === "auto" ? (zh ? "自动执行" : "Automatic") : (zh ? "操作前审批" : "Ask before changes")}</span> : null}
        <ChevronDown className={`h-3.5 w-3.5 shrink-0 ${open ? "rotate-180" : ""}`} />
      </button>
      {open ? (
        <div className="mt-3 max-h-[45vh] space-y-3 overflow-auto pb-1 text-xs">
          <div className="flex flex-wrap gap-2">
            <label className="min-w-[180px] flex-1">
              <span className="mb-1 block text-muted">{zh ? "后端所在电脑的文件夹绝对路径" : "Absolute folder path on the backend computer"}</span>
              <input aria-label={zh ? "工作区路径" : "Workspace path"} className="w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono outline-none focus:border-accent" disabled={disabled || busy} onChange={(event) => setInput(event.target.value)} placeholder={zh ? "例如 C:\\Users\\你的用户名\\项目" : "e.g. C:\\Projects\\my-app"} value={input} />
            </label>
            <button type="button" className="flex items-center gap-1.5 self-end rounded-lg border border-line bg-surface px-3 py-2 disabled:opacity-50" disabled={disabled || busy} onClick={() => void act(async () => {
              setPicking(true);
              const controller = new AbortController();
              pickerRequest.current = controller;
              try {
                const selection = await pickWorkspaceFolder(input.trim() || path || undefined, controller.signal);
                if (!selection.cancelled && selection.path) setInput(selection.path);
              } finally { pickerRequest.current = null; setPicking(false); }
            })}>
              {picking ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FolderOpen className="h-3.5 w-3.5" />}
              {zh ? "浏览文件夹" : "Browse folders"}
            </button>
            <label>
              <span className="mb-1 block text-muted">{zh ? "执行权限" : "Permissions"}</span>
              <select aria-label={zh ? "执行权限" : "Permissions"} className="rounded-lg border border-line bg-surface px-2 py-2" disabled={disabled || busy} value={selectedMode} onChange={(event) => setSelectedMode(event.target.value as WorkspaceMode)}>
                <option value="approve">{zh ? "写入和命令需审批" : "Approve writes and commands"}</option>
                <option value="auto">{zh ? "授权自动执行" : "Allow automatic execution"}</option>
              </select>
            </label>
            <button type="button" className="self-end rounded-lg bg-accent px-3 py-2 text-surface disabled:opacity-50" disabled={disabled || busy || !input.trim()} onClick={() => void act(async () => {
              const result = await inspectWorkspace(input.trim());
              await onChange(result.path, selectedMode);
              setInput(result.path); setListing(result); setRelativePath("."); setPreview(null);
            })}>{zh ? "绑定文件夹" : "Open folder"}</button>
            {path ? <button type="button" className="self-end rounded-lg border border-line px-3 py-2 disabled:opacity-50" disabled={disabled || busy} onClick={() => void act(async () => { await onChange(null, "approve"); setInput(""); setSelectedMode("approve"); setListing(null); setPreview(null); })}>{zh ? "解绑" : "Disconnect"}</button> : null}
          </div>
          <p className="leading-5 text-muted">{zh ? "文件直接保存在这个文件夹。命令以你的系统用户身份在本机运行，不受 Python 沙箱限制。" : "Files are saved directly in this folder. Commands run locally as your system user, outside the Python sandbox."}{selectedMode === "auto" ? (zh ? " 自动执行会允许 Agent 无需逐次确认就修改文件和运行命令。" : " Automatic mode allows changes and commands without individual approval.") : ""}</p>
          {disabled ? <p className="text-muted">{zh ? "任务运行时不能更换工作区或权限。" : "Stop the task before changing the workspace or permissions."}</p> : null}
          {picking ? <p role="status" className="text-muted">{zh ? "请在系统窗口中选择文件夹；选好后点击“绑定文件夹”。" : "Choose a folder in the system dialog, then click Open folder."}</p> : busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {error ? <p className="text-red-600" role="alert">{error}</p> : null}
          {path && listing ? (
            <div className="rounded-lg border border-line bg-surface p-2">
              <div className="mb-2 flex items-center gap-2 text-muted">
                <span className="min-w-0 flex-1 truncate font-mono">{relativePath}</span>
                {relativePath !== "." ? <button type="button" disabled={busy} onClick={() => void browse(relativePath.replace(/\\/g, "/").split("/").slice(0, -1).join("/") || ".")}>{zh ? "上一级" : "Parent"}</button> : null}
                <button type="button" aria-label={zh ? "刷新文件列表" : "Refresh files"} disabled={busy} onClick={() => void browse(relativePath)}><RefreshCw className="h-3.5 w-3.5" /></button>
              </div>
              <div className="grid max-h-36 grid-cols-1 gap-1 overflow-auto sm:grid-cols-2">
                {listing.entries.map((entry) => <button className="flex min-w-0 items-center gap-2 rounded px-2 py-1.5 text-left hover:bg-panel disabled:opacity-60" key={entry.path} type="button" disabled={busy || !chatId} onClick={() => {
                  if (entry.kind === "directory") { void browse(entry.path); return; }
                  void act(async () => {
                    if (!chatId) return;
                    const result = await fetchWorkspaceFile(chatId, entry.path);
                    setPreview({ path: entry.path, content: result.content ?? result.text ?? "", truncated: result.truncated });
                  });
                }}>{entry.kind === "directory" ? <Folder className="h-3.5 w-3.5 shrink-0 text-accent" /> : <File className="h-3.5 w-3.5 shrink-0 text-muted" />}<span className="truncate">{entry.name}</span></button>)}
              </div>
              {!listing.entries.length ? <p className="p-2 text-muted">{zh ? "文件夹为空" : "Empty folder"}</p> : null}
              {!chatId ? <p className="mt-2 text-muted">{zh ? "发送第一条消息后可浏览子目录和预览文件。" : "Send your first message to browse subfolders and preview files."}</p> : null}
              {listing.truncated ? <p className="mt-2 text-muted">{zh ? "仅显示部分条目，可让 Agent 搜索其他文件。" : "Showing some entries. Ask the agent to search for more files."}</p> : null}
            </div>
          ) : null}
          {preview ? <div className="rounded-lg border border-line bg-surface p-2"><div className="mb-2 flex items-center gap-2"><span className="min-w-0 flex-1 break-all font-mono">{preview.path}</span><button type="button" aria-label={zh ? "关闭预览" : "Close preview"} onClick={() => setPreview(null)}><X className="h-4 w-4" /></button></div><pre className="max-h-52 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px]">{preview.content}</pre>{preview.truncated ? <p className="mt-2 text-muted">{zh ? "仅显示文件开头，可让 Agent 继续读取。" : "File preview truncated. Ask the agent to read further."}</p> : null}</div> : null}
        </div>
      ) : null}
    </section>
  );
}
