"use client";

import {
  ChevronLeft,
  ChevronRight,
  MessageSquare,
  Plus,
  Settings,
  Trash2,
  UserRound,
} from "lucide-react";
import { copy, type Locale } from "@/lib/i18n";
import type { ChatHistoryItem } from "@/lib/types";
import { cn } from "@/lib/utils";

type SidebarProps = {
  chats: ChatHistoryItem[];
  collapsed: boolean;
  currentChatId: string | null;
  navigationDisabled?: boolean;
  locale: Locale;
  onDeleteChat: (chatId: string) => void;
  onNewChat: () => void;
  onOpenSettings: () => void;
  onSelectChat: (chatId: string) => void;
  onToggleCollapsed: () => void;
  runningChatIds?: string[];
};

export function Sidebar({
  chats,
  collapsed,
  currentChatId,
  navigationDisabled = false,
  locale,
  onDeleteChat,
  onNewChat,
  onOpenSettings,
  onSelectChat,
  onToggleCollapsed,
  runningChatIds = [],
}: SidebarProps) {
  const t = copy[locale];
  const labels = getLabels(locale);

  return (
    <aside
      className={cn(
        "hidden h-screen shrink-0 border-r border-line bg-panel transition-[width] duration-200 lg:flex lg:flex-col",
        collapsed ? "w-[72px]" : "w-[260px]",
      )}
    >
      <div className="flex h-14 items-center gap-2 px-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-ink text-sm font-semibold text-surface">
          L
        </div>
        {!collapsed && (
          <div className="min-w-0 flex-1 truncate text-sm font-semibold">
            LabAI
          </div>
        )}
        <button
          aria-label={collapsed ? t.expandSidebar : t.collapseSidebar}
          className="rounded-lg p-2 text-muted hover:bg-surface hover:text-ink"
          onClick={onToggleCollapsed}
          type="button"
        >
          {collapsed ? (
            <ChevronRight className="h-4 w-4" />
          ) : (
            <ChevronLeft className="h-4 w-4" />
          )}
        </button>
      </div>

      <nav className="space-y-1 px-2 py-2">
        <SidebarButton
          collapsed={collapsed}
          disabled={navigationDisabled}
          icon={Plus}
          label={labels.newChat}
          onClick={onNewChat}
        />
        <SidebarButton
          collapsed={collapsed}
          icon={Settings}
          label={t.settings}
          onClick={onOpenSettings}
        />
      </nav>

      {!collapsed && (
        <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto px-2 py-3">
          <div className="px-2 pb-2 text-xs font-semibold text-muted">
            {labels.chatHistory}
          </div>
          <div className="space-y-1">
            {chats.map((chat) => {
              const chatRunning = chat.has_active_run || runningChatIds.includes(chat.id);
              return (
              <div
                className={cn(
                  "group flex w-full items-start rounded-lg text-sm hover:bg-surface",
                  chat.id === currentChatId && "bg-surface",
                )}
                key={chat.id}
              >
                <button
                  className="flex min-w-0 flex-1 items-start gap-2 px-2.5 py-2 text-left disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={false}
                  onClick={() => onSelectChat(chat.id)}
                  title={chat.title}
                  type="button"
                >
                  <MessageSquare className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
                  <span className="min-w-0">
                    <span className="block truncate">{chat.title}</span>
                    {chat.last_message_preview && (
                      <span className="mt-0.5 block truncate text-xs text-muted">
                        {chat.last_message_preview}
                      </span>
                    )}
                    {chatRunning && (
                      <span className="mt-0.5 block text-xs text-accent">
                        {labels.running}
                      </span>
                    )}
                  </span>
                </button>
                <button
                  aria-label={`${labels.deleteChat}: ${chat.title}`}
                  className="mr-1 mt-1.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted opacity-0 transition hover:bg-panel hover:text-red-600 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent group-hover:opacity-100 disabled:cursor-not-allowed disabled:opacity-30"
                  disabled={chatRunning}
                  onClick={(event) => {
                    event.stopPropagation();
                    onDeleteChat(chat.id);
                  }}
                  title={chatRunning ? labels.runningDelete : labels.deleteChat}
                  type="button"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
              );
            })}
            {chats.length === 0 && (
              <div className="rounded-lg px-2.5 py-3 text-sm text-muted">
                {labels.emptyHistory}
              </div>
            )}
          </div>
        </div>
      )}

      <div className="mt-auto border-t border-line p-2">
        <button className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left hover:bg-surface">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface text-muted">
            <UserRound className="h-4 w-4" />
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">{t.user}</div>
              <div className="text-xs text-muted">{t.localWorkspace}</div>
            </div>
          )}
        </button>
      </div>
    </aside>
  );
}

function SidebarButton({
  collapsed,
  icon: Icon,
  label,
  onClick,
  disabled = false,
}: {
  collapsed: boolean;
  disabled?: boolean;
  icon: typeof Plus;
  label: string;
  onClick?: () => void;
}) {
  return (
    <button
      className={cn(
        "flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm hover:bg-surface",
        collapsed && "justify-center px-0",
        disabled && "cursor-not-allowed opacity-50 hover:bg-transparent",
      )}
      disabled={disabled}
      onClick={onClick}
      title={collapsed ? label : undefined}
      type="button"
    >
      <Icon className="h-4 w-4 shrink-0 text-muted" />
      {!collapsed && <span className="truncate">{label}</span>}
    </button>
  );
}

function getLabels(locale: Locale) {
  if (locale === "zh") {
    return {
      chatHistory: "历史对话记录",
      deleteChat: "删除对话",
      emptyHistory: "还没有历史对话",
      generating: "正在生成，完成后再切换对话",
      newChat: "新对话",
      running: "运行中",
      runningDelete: "运行中，暂不能删除",
    };
  }

  return {
    chatHistory: "Chat history",
    deleteChat: "Delete chat",
    emptyHistory: "No chats yet",
    generating: "Generation is running. Switch chats after it finishes.",
    newChat: "New chat",
    running: "Running",
    runningDelete: "Running chats cannot be deleted",
  };
}
