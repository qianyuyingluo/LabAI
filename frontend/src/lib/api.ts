import type {
  AnalysisPlan,
  ChatHistoryDetail,
  ChatHistoryItem,
  ChatMessageMetadata,
  MessageRole,
  MessageStatus,
  MessageType,
  ModelProfile,
  ModelProfileInput,
  PlanPrompt,
} from "@/lib/types";

export type UploadedImage = {
  file_id: string;
  filename: string;
  mime_type: string;
  size: number;
};

export type UploadedFile = UploadedImage & {
  file_type: string;
  summary: string;
};

export type ChatStreamRequest = {
  chat_id?: string | null;
  model_profile_id?: string | null;
  message: string;
  image_file_ids?: string[];
  file_ids?: string[];
  file_summaries?: string[];
  extra_params?: Record<string, unknown>;
};

export type PlanStreamRequest = ChatStreamRequest & {
  file_summaries?: string[];
};

export type BackendMessage = {
  id: string;
  chat_id: string;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  type: MessageType;
  metadata: ChatMessageMetadata | null;
  model_profile_id: string | null;
  created_at: string;
};

export type RunStartResponse = {
  chat_id: string;
  chat?: {
    id: string;
    title: string;
    created_at: string;
    updated_at: string;
    model_profile_id: string | null;
  } | null;
  user_message?: BackendMessage;
  assistant_message: BackendMessage;
};

type ModelProfileResponse = Omit<ModelProfile, "supports_tools"> & {
  supports_tools?: boolean;
};

export class ApiError extends Error {
  code?: string;
  details?: unknown;
  status?: number;

  constructor(message: string, options?: { code?: string; details?: unknown; status?: number }) {
    super(message);
    this.name = "ApiError";
    this.code = options?.code;
    this.details = options?.details;
    this.status = options?.status;
  }
}

const API_BASE_URL = (
  process.env.NEXT_PUBLIC_BACKEND_URL || "http://127.0.0.1:8000"
).replace(/\/$/, "");

export async function fetchModelProfiles(signal?: AbortSignal) {
  const profiles = await requestJson<ModelProfileResponse[]>("/api/models", { signal });
  return profiles.map(normalizeModelProfile);
}

export async function fetchChatHistory(signal?: AbortSignal) {
  return requestJson<ChatHistoryItem[]>("/api/chats", { signal });
}

export async function fetchChatDetail(chatId: string, signal?: AbortSignal) {
  return requestJson<ChatHistoryDetail>(`/api/chats/${encodeURIComponent(chatId)}`, {
    signal,
  });
}

export async function deleteChat(chatId: string) {
  return requestJson<{ ok: boolean }>(`/api/chats/${encodeURIComponent(chatId)}`, {
    method: "DELETE",
  });
}

export async function startChatRun(input: ChatStreamRequest) {
  return requestJson<RunStartResponse>("/api/runs/chat", {
    body: JSON.stringify(input),
    method: "POST",
  });
}

export async function startPlanGenerateRun(input: PlanStreamRequest) {
  return requestJson<RunStartResponse>("/api/runs/plan/generate", {
    body: JSON.stringify(input),
    method: "POST",
  });
}

export async function startApprovePlanRun(
  planId: string,
  input: { plan_message_id?: string; extra_params?: Record<string, unknown> } = {},
) {
  return requestJson<RunStartResponse>(`/api/runs/plans/${encodeURIComponent(planId)}/approve`, {
    body: JSON.stringify(input),
    method: "POST",
  });
}

export async function startRevisePlanRun(
  planId: string,
  input: {
    plan_message_id?: string;
    user_feedback: string;
    extra_params?: Record<string, unknown>;
  },
) {
  return requestJson<RunStartResponse>(`/api/runs/plans/${encodeURIComponent(planId)}/revise`, {
    body: JSON.stringify(input),
    method: "POST",
  });
}

export async function openRunMessageStream(messageId: string, signal?: AbortSignal) {
  const response = await fetch(
    `${API_BASE_URL}/api/runs/messages/${encodeURIComponent(messageId)}/stream`,
    {
      method: "GET",
      signal,
    },
  );

  if (!response.ok) {
    await throwFromResponse(response);
  }
  return response;
}

export async function createModelProfile(input: ModelProfileInput) {
  const profile = await requestJson<ModelProfileResponse>("/api/models", {
    body: JSON.stringify(input),
    method: "POST",
  });
  return normalizeModelProfile(profile);
}

export async function updateModelProfile(id: string, input: Partial<ModelProfileInput>) {
  const profile = await requestJson<ModelProfileResponse>(`/api/models/${encodeURIComponent(id)}`, {
    body: JSON.stringify(input),
    method: "PATCH",
  });
  return normalizeModelProfile(profile);
}

export async function deleteModelProfile(id: string) {
  return requestJson<{ ok: boolean }>(`/api/models/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function testModelProfile(modelProfileId: string) {
  return requestJson<{ ok: boolean; response: string; usage?: unknown }>("/api/models/test", {
    body: JSON.stringify({ model_profile_id: modelProfileId }),
    method: "POST",
  });
}

export async function uploadImage(file: File, signal?: AbortSignal) {
  const body = new FormData();
  body.append("file", file);

  const response = await fetch(`${API_BASE_URL}/api/files/upload-image`, {
    body,
    method: "POST",
    signal,
  });
  return parseResponse<UploadedImage>(response);
}

export async function uploadFile(file: File, signal?: AbortSignal) {
  const body = new FormData();
  body.append("file", file);

  const response = await fetch(`${API_BASE_URL}/api/files/upload`, {
    body,
    method: "POST",
    signal,
  });
  return parseResponse<UploadedFile>(response);
}

export function getUploadedImageUrl(fileId: string) {
  return `${API_BASE_URL}/api/files/images/${encodeURIComponent(fileId)}`;
}

export function getUploadedFileUrl(fileId: string) {
  return `${API_BASE_URL}/api/files/${encodeURIComponent(fileId)}`;
}

export async function openChatStream(payload: ChatStreamRequest, signal?: AbortSignal) {
  const response = await fetch(`${API_BASE_URL}/api/chat/stream`, {
    body: JSON.stringify(payload),
    headers: {
      "Content-Type": "application/json",
    },
    method: "POST",
    signal,
  });

  if (!response.ok) {
    await throwFromResponse(response);
  }
  return response;
}

export async function openGeneratePlanStream(payload: PlanStreamRequest, signal?: AbortSignal) {
  const response = await fetch(`${API_BASE_URL}/api/plans/generate/stream`, {
    body: JSON.stringify(payload),
    headers: {
      "Content-Type": "application/json",
    },
    method: "POST",
    signal,
  });

  if (!response.ok) {
    await throwFromResponse(response);
  }
  return response;
}

export async function openApprovePlanStream(planId: string, signal?: AbortSignal) {
  const response = await fetch(
    `${API_BASE_URL}/api/plans/${encodeURIComponent(planId)}/approve/stream`,
    {
      method: "POST",
      signal,
    },
  );

  if (!response.ok) {
    await throwFromResponse(response);
  }
  return response;
}

export async function openRevisePlanStream(
  planId: string,
  userFeedback: string,
  signal?: AbortSignal,
) {
  const response = await fetch(
    `${API_BASE_URL}/api/plans/${encodeURIComponent(planId)}/revise/stream`,
    {
      body: JSON.stringify({ user_feedback: userFeedback }),
      headers: {
        "Content-Type": "application/json",
      },
      method: "POST",
      signal,
    },
  );

  if (!response.ok) {
    await throwFromResponse(response);
  }
  return response;
}

export async function fetchPlan(planId: string, signal?: AbortSignal) {
  return requestJson<AnalysisPlan>(`/api/plans/${encodeURIComponent(planId)}`, {
    signal,
  });
}

export async function fetchPlanModePrompts(signal?: AbortSignal) {
  return requestJson<{ prompts: PlanPrompt[] }>("/api/plans/prompts/plan", {
    signal,
  });
}

export const fetchPlainModePrompts = fetchPlanModePrompts;

async function requestJson<T>(path: string, init?: RequestInit) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  return parseResponse<T>(response);
}

function normalizeModelProfile(profile: ModelProfileResponse): ModelProfile {
  return {
    ...profile,
    supports_tools: profile.supports_tools ?? true,
  };
}

async function parseResponse<T>(response: Response) {
  if (!response.ok) {
    await throwFromResponse(response);
  }
  return (await response.json()) as T;
}

async function throwFromResponse(response: Response): Promise<never> {
  let payload: { code?: string; message?: string; details?: unknown } | undefined;
  try {
    payload = await response.json();
  } catch {
    payload = undefined;
  }

  throw new ApiError(payload?.message || `Request failed with ${response.status}`, {
    code: payload?.code,
    details: payload?.details,
    status: response.status,
  });
}
