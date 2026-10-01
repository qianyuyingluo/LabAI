"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { MessageSquare, Plus, Settings, Trash2, X } from "lucide-react";
import { copy, type Locale } from "@/lib/i18n";
import type { ChatHistoryItem } from "@/lib/types";
import { cn } from "@/lib/utils";

type MobileSidebarProps = {
  chats: ChatHistoryItem[];
  currentChatId: string | null;
  locale: Locale;
  navigationDisabled?: boolean;
  open: boolean;
  onDeleteChat: (chatId: string) => void;
  onNewChat: () => void;
  onOpenChange: (open: boolean) => void;
  onOpenSettings: () => void;
  onSelectChat: (chatId: string) => void;
  runningChatIds?: string[];
};

export function MobileSidebar({
  chats,
  currentChatId,
  locale,
  navigationDisabled = false,
  onDeleteChat,
  onNewChat,
  onOpenChange,
  onOpenSettings,
  onSelectChat,
  open,
  runningChatIds = [],
}: MobileSidebarProps) {
  const t = copy[locale];
  const labels = getLabels(locale);

  return (
    <Dialog.Root onOpenChange={onOpenChange} open={open}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/20 lg:hidden" />
        <Dialog.Content className="fixed inset-y-0 left-0 z-50 flex w-[82vw] max-w-xs flex-col border-r border-line bg-panel shadow-xl lg:hidden">
          <div className="flex h-14 items-center justify-between px-4">
            <Dialog.Title className="font-semibold">LabAI</Dialog.Title>
            <Dialog.Close asChild>
              <button
                aria-label={t.closeSidebar}
                className="rounded-lg p-2 text-muted hover:bg-surface hover:text-ink"
                type="button"
              >
                <X className="h-4 w-4" />
              </button>
            </Dialog.Close>
          </div>

          <div className="space-y-1 px-2">
            <button
              className={cn(
                "flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-surface",
                navigationDisabled && "cursor-not-allowed opacity-50 hover:bg-transparent",
              )}
              disabled={navigationDisabled}
              onClick={onNewChat}
              type="button"
            >
              <Plus className="h-4 w-4 text-muted" />
              {labels.newChat}
            </button>
            <button
              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-surface"
              onClick={onOpenSettings}
              type="button"
            >
              <Settings className="h-4 w-4 text-muted" />
              {t.settings}
            </button>
          </div>

          <div className="mt-4 px-4 text-xs font-semibold text-muted">
            {labels.chatHistory}
          </div>
          <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto p-2">
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
                  className="flex min-w-0 flex-1 items-start gap-2 px-3 py-2 text-left disabled:cursor-not-allowed disabled:opacity-50"
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
                  className="mr-1 mt-1.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted transition hover:bg-panel hover:text-red-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-30"
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
              <div className="rounded-lg px-3 py-3 text-sm text-muted">
                {labels.emptyHistory}
              </div>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
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
