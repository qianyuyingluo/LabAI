export type MessageRole = "user" | "assistant" | "system";
export type MessageStatus = "streaming" | "done" | "error";
export type MessageType = "text" | "plan" | "execution_result" | "error";
export type PlanStatus =
  | "streaming"
  | "awaiting_approval"
  | "approved"
  | "rejected"
  | "executing"
  | "error";

export type Attachment = {
  id: string;
  name: string;
  size: number;
  type: string;
  createdAt: number;
  rawFile?: File;
  fileId?: string;
  previewUrl?: string;
  url?: string;
  uploadStatus?: "idle" | "uploading" | "uploaded" | "error";
  error?: string;
};

export type FilesSkillMetadata = {
  status: "running" | "done" | "skipped" | "error" | string;
  files?: Array<{
    fileId: string;
    filename: string;
    fileType: string;
    mimeType: string;
    size: number;
    extractedChars: number;
    includedChars: number;
    truncated: boolean;
    error?: string;
    skillOutputChars?: number;
    skillOutput?: string;
    skillError?: string;
  }>;
  contextPreview?: string;
  totalExtractedChars?: number;
  includedChars?: number;
  truncated?: boolean;
  errors?: string[];
};

export type PythonSandboxMetadata = {
  status: "running" | "done" | "error" | "skipped" | string;
  callCount: number;
  error?: string;
};

export type GeneratedFile = {
  fileId: string;
  name: string;
  mimeType: string;
  fileType: string;
  size: number;
  sha256: string;
  status: "ready" | "unavailable";
};

export type ResultCard = {
  id: string;
  title: string;
  value: string;
  detail?: string;
};

export type ChatMessage = {
  id: string;
  role: MessageRole;
  type?: MessageType;
  content: string;
  status: MessageStatus;
  createdAt: number;
  attachments?: Attachment[];
  filesSkill?: FilesSkillMetadata;
  pythonSandbox?: PythonSandboxMetadata;
  generatedFiles?: GeneratedFile[];
  planMode?: boolean;
  plainMode?: boolean;
  plan?: {
    planId?: string;
    taskId?: string;
    version: number;
    status: PlanStatus;
    markdown: string;
  };
  resultCards?: ResultCard[];
};

export type ChatHistoryItem = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  model_profile_id: string | null;
  message_count: number;
  last_message_preview: string | null;
  has_active_run: boolean;
};

export type ChatMessageMetadata = {
  attachments?: Array<{
    fileId: string;
    name: string;
    size: number;
    type: string;
  }>;
  filesSkill?: FilesSkillMetadata;
  pythonSandbox?: PythonSandboxMetadata;
  generatedFiles?: GeneratedFile[];
  planMode?: boolean;
  plainMode?: boolean;
  plan?: {
    planId?: string | null;
    taskId?: string | null;
    version: number;
    status: PlanStatus;
    markdown: string;
  };
};

export type ChatHistoryDetail = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  model_profile_id: string | null;
  messages: Array<{
    id: string;
    chat_id: string;
    role: MessageRole;
    content: string;
    status: MessageStatus;
    type: MessageType;
    metadata: ChatMessageMetadata | null;
    model_profile_id: string | null;
    created_at: string;
  }>;
};

export type ModelProfile = {
  id: string;
  name: string;
  provider: "openai_compat" | string;
  base_url: string | null;
  model_name: string | null;
  api_key_env: string;
  has_api_key: boolean;
  system_prompt: string | null;
  supports_stream: boolean;
  supports_vision: boolean;
  supports_tools: boolean;
};

export type ModelProfileInput = {
  name: string;
  base_url?: string | null;
  model_name?: string | null;
  api_key?: string | null;
  system_prompt?: string | null;
  supports_stream: boolean;
  supports_vision: boolean;
  supports_tools: boolean;
};

export type AnalysisPlan = {
  id: string;
  task_id: string;
  version: number;
  status: PlanStatus | string;
  plan_markdown: string;
  plan_json: string | null;
  prompt_key: string;
  prompt_version_hash: string;
  model_profile_id: string | null;
  user_feedback: string | null;
  created_at: string;
  approved_at: string | null;
  rejected_at: string | null;
  task?: {
    id: string;
    chat_id: string | null;
    user_message: string;
    status: string;
    current_plan_id: string | null;
    created_at: string;
    updated_at: string;
  } | null;
};

export type PlanPrompt = {
  key: string;
  relative_path: string;
  content: string;
  content_hash: string;
};

export type PlainPrompt = PlanPrompt;
