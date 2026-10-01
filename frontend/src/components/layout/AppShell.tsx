"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Locale } from "@/lib/i18n";
import type {
  Attachment,
  ChatHistoryDetail,
  ChatHistoryItem,
  ChatMessage,
  ChatMessageMetadata,
  MessageStatus,
  MessageType,
  ModelProfile,
  ModelProfileInput,
  PlanPrompt,
  PlanStatus,
} from "@/lib/types";
import type { BackendMessage, RunStartResponse } from "@/lib/api";
import {
  createModelProfile,
  deleteChat,
  deleteModelProfile,
  fetchChatDetail,
  fetchChatHistory,
  fetchModelProfiles,
  fetchPlanModePrompts,
  getUploadedFileUrl,
  openRunMessageStream,
  startApprovePlanRun,
  startChatRun,
  startPlanGenerateRun,
  startRevisePlanRun,
  testModelProfile,
  updateModelProfile,
  uploadFile,
} from "@/lib/api";
import {
  buildLongStressResponse,
  buildStressHistory,
  createId,
  mapMessages,
} from "@/lib/chat-store";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { ChatViewport } from "@/components/chat/ChatViewport";
import { MobileSidebar } from "@/components/layout/MobileSidebar";
import { ResultPanel } from "@/components/layout/ResultPanel";
import { SettingsSheet } from "@/components/layout/SettingsSheet";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";
import { PlanPromptDialog } from "@/components/layout/PlanPromptDialog";

type SseEvent = {
  event: string;
  data: Record<string, unknown>;
};

type ChatSession = {
  attachments: Attachment[];
  chatId: string | null;
  draftText: string;
  loaded: boolean;
  messageIds: string[];
  messagesById: Record<string, ChatMessage>;
  planMode: boolean;
};

const SELECTED_MODEL_STORAGE_KEY = "labai.selectedModelProfileId";
const CURRENT_CHAT_STORAGE_KEY = "labai.currentChatId";
const DRAFT_SESSION_KEY = "draft";

function createEmptySession(chatId: string | null = null): ChatSession {
  return {
    attachments: [],
    chatId,
    draftText: "",
    loaded: chatId === null,
    messageIds: [],
    messagesById: {},
    planMode: false,
  };
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function chunkText(text: string, size = 5) {
  const chunks: string[] = [];
  for (let index = 0; index < text.length; index += size) {
    chunks.push(text.slice(index, index + size));
  }
  return chunks;
}

function readStorage(key: string) {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function writeStorage(key: string, value: string) {
  if (typeof window === "undefined") return;
  try {
    if (value) {
      window.localStorage.setItem(key, value);
    } else {
      window.localStorage.removeItem(key);
    }
  } catch {
    // localStorage can be unavailable in some privacy modes.
  }
}

function readSelectedModelProfileId() {
  return readStorage(SELECTED_MODEL_STORAGE_KEY);
}

function writeSelectedModelProfileId(modelProfileId: string) {
  writeStorage(SELECTED_MODEL_STORAGE_KEY, modelProfileId);
}

function readCurrentChatId() {
  return readStorage(CURRENT_CHAT_STORAGE_KEY);
}

function writeCurrentChatId(chatId: string | null) {
  writeStorage(CURRENT_CHAT_STORAGE_KEY, chatId ?? "");
}

function pickModelProfileId(
  profiles: ModelProfile[],
  preferredId: string | undefined,
  previousId: string,
) {
  const storedId = readSelectedModelProfileId();
  const candidates = [preferredId, previousId, storedId].filter(Boolean);
  return candidates.find((id) => profiles.some((profile) => profile.id === id)) ?? profiles[0]?.id ?? "";
}

function formatError(error: unknown, locale: Locale) {
  if (error instanceof DOMException && error.name === "AbortError") {
    return "";
  }
  const fallback = locale === "zh" ? "请求失败，请检查后端和模型配置。" : "Request failed. Check backend and model settings.";
  return error instanceof Error ? error.message || fallback : fallback;
}

function parseSseBlock(block: string): SseEvent | null {
  const lines = block.split(/\r?\n/);
  let event = "message";
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }

  if (!dataLines.length) return null;
  const rawData = dataLines.join("\n");
  try {
    return { event, data: JSON.parse(rawData) as Record<string, unknown> };
  } catch {
    return { event, data: { text: rawData } };
  }
}

async function readSseStream(
  response: Response,
  onEvent: (event: SseEvent) => void,
) {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Streaming response body is empty.");

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const event = parseSseBlock(block);
      if (event) onEvent(event);
    }
  }

  buffer += decoder.decode();
  const event = parseSseBlock(buffer);
  if (event) onEvent(event);
}

function isPlanStatus(value: unknown): value is PlanStatus {
  return (
    value === "streaming" ||
    value === "awaiting_approval" ||
    value === "approved" ||
    value === "rejected" ||
    value === "executing" ||
    value === "error"
  );
}

function normalizePlan(metadata: ChatMessageMetadata | null | undefined): ChatMessage["plan"] {
  const plan = metadata?.plan;
  if (!plan || !isPlanStatus(plan.status)) return undefined;
  return {
    markdown: plan.markdown ?? "",
    planId: plan.planId ?? undefined,
    status: plan.status,
    taskId: plan.taskId ?? undefined,
    version: plan.version ?? 1,
  };
}

function normalizeAttachments(metadata: ChatMessageMetadata | null | undefined): Attachment[] {
  return (metadata?.attachments ?? []).map((attachment) => ({
    id: `file-${attachment.fileId}`,
    createdAt: 0,
    fileId: attachment.fileId,
    name: attachment.name,
    size: attachment.size,
    type: attachment.type,
    uploadStatus: "uploaded",
    url: getUploadedFileUrl(attachment.fileId),
  }));
}

function normalizeFilesSkill(metadata: ChatMessageMetadata | null | undefined) {
  return metadata?.filesSkill;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function normalizePythonSandboxValue(value: unknown): ChatMessage["pythonSandbox"] {
  const sandbox = asRecord(value);
  if (!sandbox || typeof sandbox.status !== "string") return undefined;
  const rawCallCount = sandbox.callCount ?? sandbox.call_count;
  const callCount =
    typeof rawCallCount === "number" && Number.isFinite(rawCallCount)
      ? Math.max(0, Math.trunc(rawCallCount))
      : 0;
  const error = typeof sandbox.error === "string" && sandbox.error ? sandbox.error : undefined;
  return {
    status: sandbox.status,
    callCount,
    ...(error ? { error } : {}),
  };
}

function normalizePythonSandbox(metadata: ChatMessageMetadata | null | undefined) {
  return normalizePythonSandboxValue(metadata?.pythonSandbox);
}

function normalizeGeneratedFilesValue(value: unknown): ChatMessage["generatedFiles"] {
  if (!Array.isArray(value)) return undefined;
  return value.flatMap((entry) => {
    const file = asRecord(entry);
    if (!file) return [];
    const fileId = file.fileId ?? file.file_id;
    const name = file.name ?? file.filename;
    if (typeof fileId !== "string" || !fileId || typeof name !== "string" || !name) {
      return [];
    }
    const rawSize = file.size;
    return [{
      fileId,
      name,
      mimeType:
        typeof (file.mimeType ?? file.mime_type) === "string"
          ? String(file.mimeType ?? file.mime_type)
          : "application/octet-stream",
      fileType:
        typeof (file.fileType ?? file.file_type) === "string"
          ? String(file.fileType ?? file.file_type)
          : "file",
      size:
        typeof rawSize === "number" && Number.isFinite(rawSize)
          ? Math.max(0, rawSize)
          : 0,
      sha256: typeof file.sha256 === "string" ? file.sha256 : "",
      status: file.status === "unavailable" ? "unavailable" as const : "ready" as const,
    }];
  });
}

function normalizeGeneratedFiles(metadata: ChatMessageMetadata | null | undefined) {
  return normalizeGeneratedFilesValue(metadata?.generatedFiles);
}

function backendMessageToChatMessage(message: BackendMessage | ChatHistoryDetail["messages"][number]): ChatMessage {
  const metadata = message.metadata ?? null;
  const plan = normalizePlan(metadata);
  const attachments = normalizeAttachments(metadata);
  const filesSkill = normalizeFilesSkill(metadata);
  const pythonSandbox = normalizePythonSandbox(metadata);
  const generatedFiles = normalizeGeneratedFiles(metadata);
  return {
    id: message.id,
    role: message.role,
    type: plan ? "plan" : message.type,
    content: message.content,
    status: message.status,
    attachments: attachments.length ? attachments : undefined,
    filesSkill,
    pythonSandbox,
    generatedFiles,
    createdAt: new Date(message.created_at).getTime(),
    planMode: Boolean(metadata?.planMode || metadata?.plainMode),
    plan,
  };
}

function mapBackendChatMessages(detail: ChatHistoryDetail): ChatMessage[] {
  return detail.messages
    .filter((message) => message.role === "user" || message.role === "assistant")
    .map(backendMessageToChatMessage);
}

function sessionHasRunning(session: ChatSession | undefined) {
  if (!session) return false;
  return session.messageIds.some((id) => session.messagesById[id]?.status === "streaming");
}

function getRunningMessageIds(session: ChatSession | undefined) {
  if (!session) return [];
  return session.messageIds.filter((id) => session.messagesById[id]?.status === "streaming");
}

function getLatestAwaitingPlan(session: ChatSession | undefined) {
  if (!session) return null;
  for (let index = session.messageIds.length - 1; index >= 0; index -= 1) {
    const messageId = session.messageIds[index];
    const message = session.messagesById[messageId];
    if (
      message?.type === "plan" &&
      message.plan?.planId &&
      message.plan.status === "awaiting_approval"
    ) {
      return { messageId, planId: message.plan.planId };
    }
  }
  return null;
}

function shouldResumePlanMode(messages: ChatMessage[]) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.type === "plan" && message.plan) {
      return message.plan.status === "awaiting_approval";
    }
  }
  return false;
}

function addMessagesToSession(session: ChatSession, messages: ChatMessage[]) {
  const messagesById = { ...session.messagesById };
  const messageIds = [...session.messageIds];
  for (const message of messages) {
    messagesById[message.id] = message;
    if (!messageIds.includes(message.id)) {
      messageIds.push(message.id);
    }
  }
  return { ...session, messageIds, messagesById, loaded: true };
}

export function AppShell() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [resultPanelOpen, setResultPanelOpen] = useState(false);
  const [promptDialogOpen, setPromptDialogOpen] = useState(false);
  const [promptDialogLoading, setPromptDialogLoading] = useState(false);
  const [promptDialogError, setPromptDialogError] = useState("");
  const [planPrompts, setPlanPrompts] = useState<PlanPrompt[]>([]);
  const [currentModel, setCurrentModel] = useState("");
  const [modelProfiles, setModelProfiles] = useState<ModelProfile[]>([]);
  const [modelLoading, setModelLoading] = useState(true);
  const [chatHistory, setChatHistory] = useState<ChatHistoryItem[]>([]);
  const [currentSessionKey, setCurrentSessionKey] = useState(DRAFT_SESSION_KEY);
  const [locale, setLocale] = useState<Locale>("zh");
  const [sessionsByKey, setSessionsByKey] = useState<Record<string, ChatSession>>({
    [DRAFT_SESSION_KEY]: createEmptySession(),
  });
  const activeSubscriptionsRef = useRef<Map<string, AbortController>>(new Map());
  const currentModelRef = useRef("");

  const currentSession = sessionsByKey[currentSessionKey] ?? createEmptySession();
  const currentChatId = currentSession.chatId;
  const currentRunning = sessionHasRunning(currentSession);

  const runningChatIds = useMemo(() => {
    const ids = new Set<string>();
    for (const chat of chatHistory) {
      if (chat.has_active_run) ids.add(chat.id);
    }
    for (const session of Object.values(sessionsByKey)) {
      if (session.chatId && sessionHasRunning(session)) {
        ids.add(session.chatId);
      }
    }
    return Array.from(ids);
  }, [chatHistory, sessionsByKey]);

  const getMessage = useCallback(
    (id: string) => currentSession.messagesById[id],
    [currentSession.messagesById],
  );

  const refreshChatHistory = useCallback(async () => {
    const chats = await fetchChatHistory();
    setChatHistory(chats);
    return chats;
  }, []);

  const updateCurrentSession = useCallback((updater: (session: ChatSession) => ChatSession) => {
    setSessionsByKey((prev) => {
      const current = prev[currentSessionKey] ?? createEmptySession();
      return { ...prev, [currentSessionKey]: updater(current) };
    });
  }, [currentSessionKey]);

  const updateSessionByChatId = useCallback(
    (chatId: string, updater: (session: ChatSession) => ChatSession) => {
      setSessionsByKey((prev) => {
        const current = prev[chatId] ?? createEmptySession(chatId);
        return { ...prev, [chatId]: updater(current) };
      });
    },
    [],
  );

  const selectModelProfile = useCallback((modelProfileId: string) => {
    currentModelRef.current = modelProfileId;
    setCurrentModel(modelProfileId);
    writeSelectedModelProfileId(modelProfileId);
  }, []);

  const selectAvailableModelProfile = useCallback(
    (profiles: ModelProfile[], preferredId?: string) => {
      const nextModelId = pickModelProfileId(profiles, preferredId, currentModelRef.current);
      selectModelProfile(nextModelId);
    },
    [selectModelProfile],
  );

  const refreshModelProfiles = useCallback(async (preferredId?: string) => {
    setModelLoading(true);
    try {
      const profiles = await fetchModelProfiles();
      setModelProfiles(profiles);
      selectAvailableModelProfile(profiles, preferredId);
      return profiles;
    } finally {
      setModelLoading(false);
    }
  }, [selectAvailableModelProfile]);

  const applyRunEvent = useCallback(
    (fallbackChatId: string, messageId: string, event: SseEvent) => {
      const chatId = typeof event.data.chat_id === "string" ? event.data.chat_id : fallbackChatId;

      if (event.event === "snapshot") {
        const metadata = (event.data.metadata ?? null) as ChatMessageMetadata | null;
        const status = typeof event.data.status === "string" ? event.data.status as MessageStatus : "streaming";
        const type = typeof event.data.type === "string" ? event.data.type as MessageType : "text";
        const content = typeof event.data.content === "string" ? event.data.content : "";
        const attachments = normalizeAttachments(metadata);
        updateSessionByChatId(chatId, (session) => {
          const current = session.messagesById[messageId];
          const nextMessage: ChatMessage = {
            ...(current ?? {
              createdAt: Date.now(),
              id: messageId,
              role: "assistant",
            }),
            content,
            status,
            type: normalizePlan(metadata) ? "plan" : type,
            attachments: attachments.length ? attachments : current?.attachments,
            filesSkill: normalizeFilesSkill(metadata),
            pythonSandbox: normalizePythonSandbox(metadata),
            generatedFiles: normalizeGeneratedFiles(metadata),
            planMode: Boolean(metadata?.planMode || metadata?.plainMode),
            plan: normalizePlan(metadata),
          };
          return addMessagesToSession(session, [nextMessage]);
        });
        return;
      }

      if (event.event === "python_sandbox_status") {
        const pythonSandbox =
          normalizePythonSandboxValue(event.data.pythonSandbox) ??
          normalizePythonSandboxValue(event.data.python_sandbox) ??
          normalizePythonSandboxValue(event.data);
        if (!pythonSandbox) return;
        updateSessionByChatId(chatId, (session) => {
          const current = session.messagesById[messageId];
          if (!current) return session;
          return addMessagesToSession(session, [
            {
              ...current,
              pythonSandbox,
            },
          ]);
        });
        return;
      }

      if (event.event === "generated_files") {
        const generatedFiles =
          normalizeGeneratedFilesValue(event.data.generatedFiles) ??
          normalizeGeneratedFilesValue(event.data.generated_files) ??
          normalizeGeneratedFilesValue(event.data.files) ??
          [];
        updateSessionByChatId(chatId, (session) => {
          const current = session.messagesById[messageId];
          if (!current) return session;
          return addMessagesToSession(session, [
            {
              ...current,
              generatedFiles,
            },
          ]);
        });
        return;
      }

      if (event.event === "files_skill_status") {
        const filesSkill =
          (event.data.filesSkill as ChatMessage["filesSkill"] | undefined) ??
          ({
            status: typeof event.data.status === "string" ? event.data.status : "running",
          } satisfies NonNullable<ChatMessage["filesSkill"]>);
        updateSessionByChatId(chatId, (session) => {
          const current = session.messagesById[messageId];
          if (!current) return session;
          return addMessagesToSession(session, [
            {
              ...current,
              filesSkill,
              status: current.status === "done" ? "done" : "streaming",
            },
          ]);
        });
        return;
      }

      if (event.event === "delta" || event.event === "plan_delta" || event.event === "execution_delta") {
        const text = typeof event.data.text === "string" ? event.data.text : "";
        if (!text) return;
        updateSessionByChatId(chatId, (session) => {
          const current = session.messagesById[messageId];
          if (!current) return session;
          const nextContent = current.content + text;
          return addMessagesToSession(session, [
            {
              ...current,
              content: nextContent,
              plan: current.plan
                ? {
                    ...current.plan,
                    markdown: nextContent,
                  }
                : current.plan,
              status: "streaming",
            },
          ]);
        });
        return;
      }

      if (event.event === "plan_created" || event.event === "approval_required") {
        updateSessionByChatId(chatId, (session) => {
          const current = session.messagesById[messageId];
          if (!current) return session;
          const planId = typeof event.data.plan_id === "string" ? event.data.plan_id : current.plan?.planId;
          const taskId = typeof event.data.task_id === "string" ? event.data.task_id : current.plan?.taskId;
          const version = typeof event.data.version === "number" ? event.data.version : current.plan?.version ?? 1;
          const status = event.event === "approval_required" ? "awaiting_approval" : current.plan?.status ?? "streaming";
          const nextSession = addMessagesToSession(session, [
            {
              ...current,
              status: event.event === "approval_required" ? "done" : current.status,
              type: "plan",
              plan: {
                markdown: current.content,
                planId,
                status,
                taskId,
                version,
              },
            },
          ]);
          return event.event === "approval_required"
            ? { ...nextSession, planMode: true }
            : nextSession;
        });
        return;
      }

      if (event.event === "error") {
        const message =
          typeof event.data.message === "string"
            ? event.data.message
            : locale === "zh"
              ? "运行失败。"
              : "Run failed.";
        updateSessionByChatId(chatId, (session) => {
          const current = session.messagesById[messageId];
          if (!current) return session;
          return addMessagesToSession(session, [
            {
              ...current,
              content: message,
              plan: current.plan
                ? {
                    ...current.plan,
                    markdown: current.content || message,
                    status: "error",
                  }
                : current.plan,
              status: "error",
              type: current.type === "plan" ? "plan" : "error",
            },
          ]);
        });
        void refreshChatHistory();
        return;
      }

      if (event.event === "done") {
        const ok = event.data.ok !== false;
        updateSessionByChatId(chatId, (session) => {
          const current = session.messagesById[messageId];
          if (!current || current.status === "error") return session;
          const nextPlan = current.plan
            ? {
                ...current.plan,
                markdown: current.content,
                status: current.plan.status === "streaming" ? "awaiting_approval" : current.plan.status,
              }
            : current.plan;
          const nextSession = addMessagesToSession(session, [
            {
              ...current,
              status: ok ? "done" : "error",
              plan: nextPlan,
            },
          ]);
          if (current.type === "execution_result") {
            return { ...nextSession, planMode: false };
          }
          if (current.type === "plan" && nextPlan?.status === "awaiting_approval") {
            return { ...nextSession, planMode: true };
          }
          return nextSession;
        });
        activeSubscriptionsRef.current.get(messageId)?.abort();
        activeSubscriptionsRef.current.delete(messageId);
        void refreshChatHistory();
      }
    },
    [locale, refreshChatHistory, updateSessionByChatId],
  );

  const subscribeToRunMessage = useCallback(
    async (chatId: string, messageId: string) => {
      if (activeSubscriptionsRef.current.has(messageId)) return;
      const controller = new AbortController();
      activeSubscriptionsRef.current.set(messageId, controller);
      try {
        const response = await openRunMessageStream(messageId, controller.signal);
        await readSseStream(response, (event) => applyRunEvent(chatId, messageId, event));
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          const message = formatError(error, locale);
          if (message) {
            applyRunEvent(chatId, messageId, {
              event: "error",
              data: { message },
            });
          }
        }
      } finally {
        activeSubscriptionsRef.current.delete(messageId);
      }
    },
    [applyRunEvent, locale],
  );

  const subscribeToStreamingMessages = useCallback(
    (chatId: string, messages: ChatMessage[]) => {
      for (const message of messages) {
        if (message.status === "streaming") {
          void subscribeToRunMessage(chatId, message.id);
        }
      }
    },
    [subscribeToRunMessage],
  );

  const loadChat = useCallback(
    async (chatId: string) => {
      const detail = await fetchChatDetail(chatId);
      const messages = mapBackendChatMessages(detail);
      setSessionsByKey((prev) => ({
        ...prev,
        [detail.id]: {
          ...createEmptySession(detail.id),
          loaded: true,
          messageIds: messages.map((message) => message.id),
          messagesById: mapMessages(messages),
          planMode: shouldResumePlanMode(messages),
        },
      }));
      setCurrentSessionKey(detail.id);
      writeCurrentChatId(detail.id);
      setMobileSidebarOpen(false);
      if (detail.model_profile_id) {
        selectModelProfile(detail.model_profile_id);
      }
      subscribeToStreamingMessages(detail.id, messages);
    },
    [selectModelProfile, subscribeToStreamingMessages],
  );

  useEffect(() => {
    const controller = new AbortController();
    fetchModelProfiles(controller.signal)
      .then((profiles) => {
        setModelProfiles(profiles);
        selectAvailableModelProfile(profiles);
      })
      .catch(() => {
        setModelProfiles([]);
        currentModelRef.current = "";
        setCurrentModel("");
      })
      .finally(() => setModelLoading(false));

    return () => controller.abort();
  }, [selectAvailableModelProfile]);

  useEffect(() => {
    const controller = new AbortController();
    fetchChatHistory(controller.signal)
      .then((chats) => {
        setChatHistory(chats);
        const storedChatId = readCurrentChatId();
        if (storedChatId && chats.some((chat) => chat.id === storedChatId)) {
          void loadChat(storedChatId);
        }
      })
      .catch(() => setChatHistory([]));

    return () => controller.abort();
  }, [loadChat]);

  useEffect(
    () => () => {
      for (const controller of activeSubscriptionsRef.current.values()) {
        controller.abort();
      }
      activeSubscriptionsRef.current.clear();
    },
    [],
  );

  const handleCreateModel = useCallback(
    async (input: ModelProfileInput) => {
      const created = await createModelProfile(input);
      await refreshModelProfiles(created.id);
      return created;
    },
    [refreshModelProfiles],
  );

  const handleUpdateModel = useCallback(
    async (id: string, input: Partial<ModelProfileInput>) => {
      const updated = await updateModelProfile(id, input);
      await refreshModelProfiles(updated.id);
      return updated;
    },
    [refreshModelProfiles],
  );

  const handleDeleteModel = useCallback(
    async (id: string) => {
      await deleteModelProfile(id);
      await refreshModelProfiles();
    },
    [refreshModelProfiles],
  );

  const handleTestModel = useCallback(async (id: string) => {
    const result = await testModelProfile(id);
    return result.response;
  }, []);

  const uploadMessageAttachments = useCallback(
    async (files: Attachment[]) => {
      const fileIds: string[] = [];
      const imageFileIds: string[] = [];
      for (const attachment of files) {
        if (attachment.fileId) {
          fileIds.push(attachment.fileId);
          if (attachment.type.startsWith("image/")) {
            imageFileIds.push(attachment.fileId);
          }
          continue;
        }
        if (!attachment.rawFile) {
          throw new Error(
            locale === "zh"
              ? "附件缺少原始文件，请重新选择图片。"
              : "Attachment file data is missing. Select the image again.",
          );
        }
        attachment.uploadStatus = "uploading";
        const uploaded = await uploadFile(attachment.rawFile);
        attachment.fileId = uploaded.file_id;
        attachment.type = uploaded.mime_type;
        attachment.uploadStatus = "uploaded";
        fileIds.push(uploaded.file_id);
        if (uploaded.file_type === "image") {
          imageFileIds.push(uploaded.file_id);
        }
      }
      return { fileIds, imageFileIds };
    },
    [locale],
  );

  const applyRunStartResponse = useCallback(
    (sourceKey: string, response: RunStartResponse) => {
      const chatId = response.chat_id;
      const incoming = [
        response.user_message ? backendMessageToChatMessage(response.user_message) : null,
        backendMessageToChatMessage(response.assistant_message),
      ].filter(Boolean) as ChatMessage[];
      const assistantMessage = incoming[incoming.length - 1];

      setSessionsByKey((prev) => {
        const source = prev[sourceKey] ?? createEmptySession();
        const target = prev[chatId] ?? createEmptySession(chatId);
        const nextPlanMode =
          assistantMessage?.type === "execution_result"
            ? false
            : assistantMessage?.type === "plan"
              ? true
              : source.planMode;
        const nextTarget = addMessagesToSession(
          {
            ...target,
            attachments: [],
            chatId,
            draftText: "",
            planMode: nextPlanMode,
          },
          incoming,
        );
        const next = { ...prev, [chatId]: nextTarget };
        if (sourceKey !== chatId && sourceKey.startsWith("draft")) {
          delete next[sourceKey];
        }
        return next;
      });
      setCurrentSessionKey(chatId);
      writeCurrentChatId(chatId);
      void subscribeToRunMessage(chatId, response.assistant_message.id);
      void refreshChatHistory();
    },
    [refreshChatHistory, subscribeToRunMessage],
  );

  const handleSend = useCallback(
    async (message: string, files: Attachment[]) => {
      const session = sessionsByKey[currentSessionKey] ?? createEmptySession();
      if (sessionHasRunning(session)) return;

      try {
        if (!currentModel) {
          throw new Error(locale === "zh" ? "请先在设置中添加并选择一个模型。" : "Add and select a model first.");
        }
        const awaitingPlan = getLatestAwaitingPlan(session);
        if (session.planMode && awaitingPlan && session.chatId) {
          const feedback =
            message.trim() ||
            (locale === "zh" ? "请根据我补充的修改意见更新计划。" : "Please revise the plan with my feedback.");
          updateSessionByChatId(session.chatId, (current) => {
            const planMessage = current.messagesById[awaitingPlan.messageId];
            return {
              ...current,
              attachments: [],
              draftText: "",
              planMode: true,
              messagesById: planMessage
                ? {
                    ...current.messagesById,
                    [awaitingPlan.messageId]: {
                      ...planMessage,
                      plan: planMessage.plan
                        ? {
                            ...planMessage.plan,
                            status: "rejected",
                          }
                        : planMessage.plan,
                    },
                  }
                : current.messagesById,
            };
          });
          const response = await startRevisePlanRun(awaitingPlan.planId, {
            plan_message_id: awaitingPlan.messageId,
            user_feedback: feedback,
          });
          applyRunStartResponse(currentSessionKey, response);
          return;
        }

        const { fileIds, imageFileIds } = await uploadMessageAttachments(files);
        const payload = {
          chat_id: session.chatId,
          file_ids: fileIds,
          image_file_ids: imageFileIds,
          message,
          model_profile_id: currentModel,
        };
        const response = session.planMode
          ? await startPlanGenerateRun(payload)
          : await startChatRun(payload);
        applyRunStartResponse(currentSessionKey, response);
      } catch (error) {
        const messageText = formatError(error, locale);
        if (messageText) window.alert(messageText);
      }
    },
    [
      applyRunStartResponse,
      currentModel,
      currentSessionKey,
      locale,
      sessionsByKey,
      updateSessionByChatId,
      uploadMessageAttachments,
    ],
  );

  const handleApprovePlan = useCallback(
    async (planMessageId: string, planId: string) => {
      const session = sessionsByKey[currentSessionKey] ?? createEmptySession();
      if (sessionHasRunning(session) || !session.chatId) return;

      updateSessionByChatId(session.chatId, (current) => {
        const message = current.messagesById[planMessageId];
        if (!message) return current;
        return addMessagesToSession(
          { ...current, planMode: false },
          [
            {
              ...message,
              plan: message.plan
                ? {
                    ...message.plan,
                    status: "approved",
                  }
                : message.plan,
            },
          ],
        );
      });

      try {
        const response = await startApprovePlanRun(planId, { plan_message_id: planMessageId });
        applyRunStartResponse(currentSessionKey, response);
      } catch (error) {
        const messageText = formatError(error, locale);
        if (messageText) window.alert(messageText);
      }
    },
    [applyRunStartResponse, currentSessionKey, locale, sessionsByKey, updateSessionByChatId],
  );

  const handleRevisePlan = useCallback(
    async (planMessageId: string, planId: string, feedback: string) => {
      const session = sessionsByKey[currentSessionKey] ?? createEmptySession();
      if (sessionHasRunning(session) || !session.chatId) return;

      updateSessionByChatId(session.chatId, (current) => {
        const message = current.messagesById[planMessageId];
        if (!message) return current;
        return addMessagesToSession(
          { ...current, planMode: true },
          [
            {
              ...message,
              plan: message.plan
                ? {
                    ...message.plan,
                    status: "rejected",
                  }
                : message.plan,
            },
          ],
        );
      });

      try {
        const response = await startRevisePlanRun(planId, {
          plan_message_id: planMessageId,
          user_feedback: feedback,
        });
        applyRunStartResponse(currentSessionKey, response);
      } catch (error) {
        const messageText = formatError(error, locale);
        if (messageText) window.alert(messageText);
      }
    },
    [applyRunStartResponse, currentSessionKey, locale, sessionsByKey, updateSessionByChatId],
  );

  const handleDeleteChat = useCallback(
    async (chatId: string) => {
      if (runningChatIds.includes(chatId)) return;

      const chat = chatHistory.find((item) => item.id === chatId);
      const title = chat?.title ?? (locale === "zh" ? "此对话" : "this chat");
      const confirmed = window.confirm(
        locale === "zh"
          ? `确定删除“${title}”吗？数据库里的对应对话和消息也会删除。`
          : `Delete "${title}"? The matching chat and messages will also be removed from the database.`,
      );
      if (!confirmed) return;

      try {
        await deleteChat(chatId);
        setChatHistory((prev) => prev.filter((item) => item.id !== chatId));
        setSessionsByKey((prev) => {
          const next = { ...prev };
          delete next[chatId];
          if (!next[DRAFT_SESSION_KEY]) next[DRAFT_SESSION_KEY] = createEmptySession();
          return next;
        });
        setMobileSidebarOpen(false);
        if (currentSessionKey === chatId) {
          setCurrentSessionKey(DRAFT_SESSION_KEY);
          writeCurrentChatId(null);
        }
      } catch (error) {
        const messageText = formatError(error, locale);
        if (messageText) window.alert(messageText);
        await refreshChatHistory();
      }
    },
    [chatHistory, currentSessionKey, locale, refreshChatHistory, runningChatIds],
  );

  const handleNewChat = useCallback(() => {
    const draftKey = `draft:${createId("chat")}`;
    setSessionsByKey((prev) => ({ ...prev, [draftKey]: createEmptySession() }));
    setCurrentSessionKey(draftKey);
    writeCurrentChatId(null);
    setMobileSidebarOpen(false);
  }, []);

  const handleSelectChat = useCallback(
    async (chatId: string) => {
      setCurrentSessionKey(chatId);
      writeCurrentChatId(chatId);
      setMobileSidebarOpen(false);

      const cached = sessionsByKey[chatId];
      if (cached?.loaded) {
        for (const messageId of getRunningMessageIds(cached)) {
          void subscribeToRunMessage(chatId, messageId);
        }
        return;
      }

      try {
        await loadChat(chatId);
      } catch {
        await refreshChatHistory();
      }
    },
    [loadChat, refreshChatHistory, sessionsByKey, subscribeToRunMessage],
  );

  const handlePromptClick = useCallback(
    (prompt: string) => {
      void handleSend(prompt, currentSession.attachments);
    },
    [currentSession.attachments, handleSend],
  );

  const handleFilesAdded = useCallback(
    (files: Attachment[]) => {
      updateCurrentSession((session) => ({
        ...session,
        attachments: [...session.attachments, ...files],
      }));
    },
    [updateCurrentSession],
  );

  const startLocalStreaming = useCallback(
    async (sessionKey: string, assistantId: string, response: string, chunkSize = 5, delay = 7) => {
      for (const chunk of chunkText(response, chunkSize)) {
        setSessionsByKey((prev) => {
          const session = prev[sessionKey];
          const message = session?.messagesById[assistantId];
          if (!session || !message || message.status !== "streaming") return prev;
          return {
            ...prev,
            [sessionKey]: addMessagesToSession(session, [
              {
                ...message,
                content: message.content + chunk,
              },
            ]),
          };
        });
        await sleep(delay);
      }
      setSessionsByKey((prev) => {
        const session = prev[sessionKey];
        const message = session?.messagesById[assistantId];
        if (!session || !message) return prev;
        return {
          ...prev,
          [sessionKey]: addMessagesToSession(session, [
            {
              ...message,
              status: "done",
            },
          ]),
        };
      });
    },
    [],
  );

  const runStressTest = useCallback(() => {
    const history = buildStressHistory(100);
    const assistantId = createId("assistant");
    const streamingMessage: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      status: "streaming",
      type: "text",
      createdAt: Date.now(),
    };
    const allMessages = [...history, streamingMessage];
    const sessionKey = `draft:${createId("stress")}`;
    setSessionsByKey((prev) => ({
      ...prev,
      [sessionKey]: {
        ...createEmptySession(),
        loaded: true,
        messageIds: allMessages.map((message) => message.id),
        messagesById: mapMessages(allMessages),
      },
    }));
    setCurrentSessionKey(sessionKey);
    writeCurrentChatId(null);
    void startLocalStreaming(sessionKey, assistantId, buildLongStressResponse(), 9, 3);
  }, [startLocalStreaming]);

  const handleOpenPlanPrompts = useCallback(async () => {
    setPromptDialogOpen(true);
    setPromptDialogLoading(true);
    setPromptDialogError("");
    try {
      const result = await fetchPlanModePrompts();
      setPlanPrompts(result.prompts);
    } catch (error) {
      setPromptDialogError(formatError(error, locale));
    } finally {
      setPromptDialogLoading(false);
    }
  }, [locale]);

  return (
    <div className="flex h-screen overflow-hidden bg-surface text-ink">
      <Sidebar
        chats={chatHistory}
        collapsed={sidebarCollapsed}
        currentChatId={currentChatId}
        locale={locale}
        navigationDisabled={false}
        onDeleteChat={handleDeleteChat}
        onNewChat={handleNewChat}
        onOpenSettings={() => setSettingsOpen(true)}
        onSelectChat={handleSelectChat}
        onToggleCollapsed={() => setSidebarCollapsed((value) => !value)}
        runningChatIds={runningChatIds}
      />

      <MobileSidebar
        chats={chatHistory}
        currentChatId={currentChatId}
        locale={locale}
        navigationDisabled={false}
        onDeleteChat={handleDeleteChat}
        onNewChat={handleNewChat}
        onOpenChange={setMobileSidebarOpen}
        onOpenSettings={() => {
          setMobileSidebarOpen(false);
          setSettingsOpen(true);
        }}
        onSelectChat={handleSelectChat}
        open={mobileSidebarOpen}
        runningChatIds={runningChatIds}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        <TopBar
          currentModel={currentModel}
          locale={locale}
          modelLoading={modelLoading}
          models={modelProfiles}
          onFilesAdded={handleFilesAdded}
          onOpenMobileSidebar={() => setMobileSidebarOpen(true)}
          onOpenPlanPrompts={handleOpenPlanPrompts}
          onOpenSettings={() => setSettingsOpen(true)}
          onRunStressTest={runStressTest}
          onToggleResultPanel={() => setResultPanelOpen((value) => !value)}
          resultPanelOpen={resultPanelOpen}
          setLocale={setLocale}
          setCurrentModel={selectModelProfile}
        />

        <ChatViewport
          conversationKey={currentSessionKey}
          getMessage={getMessage}
          locale={locale}
          messageIds={currentSession.messageIds}
          onApprovePlan={handleApprovePlan}
          onPromptClick={handlePromptClick}
          onRevisePlan={handleRevisePlan}
          streamingContent=""
          streamingMessageId={undefined}
        />

        <ChatComposer
          attachments={currentSession.attachments}
          disabled={currentRunning}
          locale={locale}
          onAttachmentsChange={(attachments) =>
            updateCurrentSession((session) => ({ ...session, attachments }))
          }
          onPlanModeChange={(planMode) =>
            updateCurrentSession((session) => ({ ...session, planMode }))
          }
          onSend={handleSend}
          onValueChange={(draftText) =>
            updateCurrentSession((session) => ({ ...session, draftText }))
          }
          planMode={currentSession.planMode}
          value={currentSession.draftText}
        />
      </main>

      <ResultPanel
        locale={locale}
        onOpenChange={setResultPanelOpen}
        open={resultPanelOpen}
      />

      <SettingsSheet
        locale={locale}
        models={modelProfiles}
        onCreateModel={handleCreateModel}
        onDeleteModel={handleDeleteModel}
        onOpenChange={setSettingsOpen}
        onSelectModel={selectModelProfile}
        onTestModel={handleTestModel}
        onUpdateModel={handleUpdateModel}
        open={settingsOpen}
        selectedModelId={currentModel}
      />

      <PlanPromptDialog
        error={promptDialogError}
        loading={promptDialogLoading}
        onOpenChange={setPromptDialogOpen}
        open={promptDialogOpen}
        prompts={planPrompts}
      />
    </div>
  );
}
