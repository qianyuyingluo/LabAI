"use client";

import { FormEvent, useMemo, useState } from "react";
import { Check, FlaskConical, Plus, Save, Trash2 } from "lucide-react";
import { type Locale } from "@/lib/i18n";
import type { ModelProfile, ModelProfileInput } from "@/lib/types";
import { cn } from "@/lib/utils";

type ApiSettingsFormProps = {
  locale: Locale;
  models: ModelProfile[];
  selectedModelId: string;
  onCreateModel: (input: ModelProfileInput) => Promise<ModelProfile>;
  onDeleteModel: (id: string) => Promise<void>;
  onSelectModel: (id: string) => void;
  onTestModel: (id: string) => Promise<string>;
  onUpdateModel: (
    id: string,
    input: Partial<ModelProfileInput>,
  ) => Promise<ModelProfile>;
};

type Draft = {
  name: string;
  base_url: string;
  model_name: string;
  api_key: string;
  system_prompt: string;
  supports_stream: boolean;
  supports_vision: boolean;
  supports_tools: boolean;
};

const NEW_MODEL_ID = "__new_model__";

export function ApiSettingsForm({
  locale,
  models,
  onCreateModel,
  onDeleteModel,
  onSelectModel,
  onTestModel,
  onUpdateModel,
  selectedModelId,
}: ApiSettingsFormProps) {
  const labels = getLabels(locale);
  const selectedProfile = useMemo(
    () => models.find((model) => model.id === selectedModelId) ?? models[0],
    [models, selectedModelId],
  );
  const [editingId, setEditingId] = useState(selectedProfile?.id ?? NEW_MODEL_ID);
  const editingProfile = models.find((model) => model.id === editingId) ?? selectedProfile;
  const isNew = editingId === NEW_MODEL_ID || !editingProfile;
  const editorKey = isNew ? NEW_MODEL_ID : editingProfile.id;

  const handleNew = () => {
    setEditingId(NEW_MODEL_ID);
  };

  const handleSelect = (profile: ModelProfile) => {
    onSelectModel(profile.id);
    setEditingId(profile.id);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">{labels.modelProfiles}</h3>
        <button
          className="flex h-8 items-center gap-1.5 rounded-lg border border-line px-2.5 text-xs hover:bg-panel"
          data-testid="model-new-button"
          onClick={handleNew}
          type="button"
        >
          <Plus className="h-3.5 w-3.5" />
          {labels.newModel}
        </button>
      </div>

      <div className="space-y-2">
        {models.map((model) => (
          <button
            className={cn(
              "flex w-full items-center justify-between gap-3 rounded-lg border border-line px-3 py-2 text-left text-sm hover:bg-panel",
              model.id === editingId && "border-accent bg-panel",
            )}
            key={model.id}
            onClick={() => handleSelect(model)}
            type="button"
          >
            <span className="min-w-0">
              <span className="block truncate font-medium">{model.name}</span>
              <span className="mt-0.5 block truncate text-xs text-muted">
                {model.model_name || labels.modelUnset}
              </span>
            </span>
            <span className="flex shrink-0 items-center gap-1 text-[11px] text-muted">
              {model.supports_vision && labels.vision}
              {model.has_api_key && <Check className="h-3.5 w-3.5 text-accent" />}
            </span>
          </button>
        ))}
      </div>

      <ModelProfileEditor
        key={editorKey}
        isNew={isNew}
        labels={labels}
        modelCount={models.length}
        onCreateModel={onCreateModel}
        onDeleteModel={onDeleteModel}
        onSelectModel={onSelectModel}
        onTestModel={onTestModel}
        onUpdateModel={onUpdateModel}
        profile={isNew ? undefined : editingProfile}
        setEditingId={setEditingId}
      />
    </div>
  );
}

function ModelProfileEditor({
  isNew,
  labels,
  modelCount,
  onCreateModel,
  onDeleteModel,
  onSelectModel,
  onTestModel,
  onUpdateModel,
  profile,
  setEditingId,
}: {
  isNew: boolean;
  labels: ReturnType<typeof getLabels>;
  modelCount: number;
  onCreateModel: (input: ModelProfileInput) => Promise<ModelProfile>;
  onDeleteModel: (id: string) => Promise<void>;
  onSelectModel: (id: string) => void;
  onTestModel: (id: string) => Promise<string>;
  onUpdateModel: (
    id: string,
    input: Partial<ModelProfileInput>,
  ) => Promise<ModelProfile>;
  profile?: ModelProfile;
  setEditingId: (id: string) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(profile));
  const [busy, setBusy] = useState<"save" | "delete" | "test" | null>(null);
  const [status, setStatus] = useState("");

  const updateDraft = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setStatus("");
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!draft.name.trim()) {
      setStatus(labels.nameRequired);
      return;
    }

    setBusy("save");
    try {
      if (isNew) {
        const created = await onCreateModel(toCreatePayload(draft));
        onSelectModel(created.id);
        setEditingId(created.id);
        setDraft(toDraft(created));
        setStatus(labels.saved);
      } else if (profile) {
        const updated = await onUpdateModel(profile.id, toPatchPayload(draft));
        setDraft(toDraft(updated));
        setStatus(labels.saved);
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : labels.failed);
    } finally {
      setBusy(null);
    }
  };

  const handleDelete = async () => {
    if (isNew || !profile) return;
    setBusy("delete");
    try {
      await onDeleteModel(profile.id);
      setEditingId(NEW_MODEL_ID);
      setStatus(labels.deleted);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : labels.failed);
    } finally {
      setBusy(null);
    }
  };

  const handleTest = async () => {
    if (isNew || !profile) return;
    setBusy("test");
    try {
      const response = await onTestModel(profile.id);
      setStatus(`${labels.testOk}: ${response || "OK"}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : labels.failed);
    } finally {
      setBusy(null);
    }
  };

  return (
    <form className="space-y-3" onSubmit={handleSubmit}>
      <label className="block text-sm">
        <span className="mb-1.5 block text-muted">{labels.displayName}</span>
        <input
          className="h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent"
          data-testid="model-display-name-input"
          onChange={(event) => updateDraft("name", event.target.value)}
          placeholder={labels.displayNamePlaceholder}
          value={draft.name}
        />
      </label>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="mb-1.5 block text-muted">{labels.modelName}</span>
          <input
            className="h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent"
            data-testid="model-name-input"
            onChange={(event) => updateDraft("model_name", event.target.value)}
            placeholder="gpt-4o-mini"
            value={draft.model_name}
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1.5 block text-muted">{labels.provider}</span>
          <input
            className="h-10 w-full rounded-lg border border-line bg-panel px-3 text-sm text-muted outline-none"
            disabled
            value="OpenAI compatible"
          />
        </label>
      </div>

      <label className="block text-sm">
        <span className="mb-1.5 block text-muted">{labels.apiBaseUrl}</span>
        <input
          className="h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent"
          data-testid="model-base-url-input"
          onChange={(event) => updateDraft("base_url", event.target.value)}
          placeholder="https://api.example.com/v1"
          type="url"
          value={draft.base_url}
        />
      </label>

      <label className="block text-sm">
        <span className="mb-1.5 block text-muted">
          {labels.apiKey}
          {!isNew && profile?.has_api_key ? (
            <span className="ml-2 text-xs text-accent">{labels.keySaved}</span>
          ) : null}
        </span>
        <input
          autoComplete="off"
          className="h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent"
          data-testid="model-api-key-input"
          onChange={(event) => updateDraft("api_key", event.target.value)}
          placeholder={isNew ? "sk-..." : labels.keepKey}
          type="password"
          value={draft.api_key}
        />
      </label>

      <label className="block text-sm">
        <span className="mb-1.5 block text-muted">{labels.systemPrompt}</span>
        <textarea
          className="min-h-28 w-full resize-y rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
          data-testid="model-system-prompt-input"
          onChange={(event) => updateDraft("system_prompt", event.target.value)}
          placeholder={labels.systemPromptPlaceholder}
          value={draft.system_prompt}
        />
      </label>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <label className="flex h-10 items-center justify-between rounded-lg border border-line px-3 text-sm">
          <span>{labels.stream}</span>
          <input
            checked={draft.supports_stream}
            className="h-4 w-4 accent-[rgb(var(--accent))]"
            data-testid="model-stream-checkbox"
            onChange={(event) => updateDraft("supports_stream", event.target.checked)}
            type="checkbox"
          />
        </label>
        <label className="flex h-10 items-center justify-between rounded-lg border border-line px-3 text-sm">
          <span>{labels.vision}</span>
          <input
            checked={draft.supports_vision}
            className="h-4 w-4 accent-[rgb(var(--accent))]"
            data-testid="model-vision-checkbox"
            onChange={(event) => updateDraft("supports_vision", event.target.checked)}
            type="checkbox"
          />
        </label>
        <label className="flex h-10 items-center justify-between rounded-lg border border-line px-3 text-sm">
          <span>{labels.tools}</span>
          <input
            checked={draft.supports_tools}
            className="h-4 w-4 accent-[rgb(var(--accent))]"
            data-testid="model-tools-checkbox"
            onChange={(event) => updateDraft("supports_tools", event.target.checked)}
            type="checkbox"
          />
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          className="flex h-9 items-center gap-2 rounded-lg bg-ink px-3 text-sm text-surface disabled:cursor-not-allowed disabled:opacity-60"
          data-testid="model-save-button"
          disabled={busy !== null}
          type="submit"
        >
          <Save className="h-4 w-4" />
          {busy === "save" ? labels.saving : labels.save}
        </button>
        <button
          className="flex h-9 items-center gap-2 rounded-lg border border-line px-3 text-sm hover:bg-panel disabled:cursor-not-allowed disabled:opacity-50"
          data-testid="model-test-button"
          disabled={isNew || busy !== null}
          onClick={handleTest}
          type="button"
        >
          <FlaskConical className="h-4 w-4" />
          {busy === "test" ? labels.testing : labels.test}
        </button>
        <button
          className="flex h-9 items-center gap-2 rounded-lg border border-line px-3 text-sm text-red-600 hover:bg-panel disabled:cursor-not-allowed disabled:opacity-50"
          data-testid="model-delete-button"
          disabled={isNew || busy !== null || modelCount <= 1}
          onClick={handleDelete}
          type="button"
        >
          <Trash2 className="h-4 w-4" />
          {busy === "delete" ? labels.deleting : labels.delete}
        </button>
      </div>

      {status && <p className="text-xs leading-5 text-muted">{status}</p>}
    </form>
  );
}

function toDraft(profile: ModelProfile | undefined): Draft {
  return {
    name: profile?.name ?? "",
    base_url: profile?.base_url ?? "",
    model_name: profile?.model_name ?? "",
    api_key: "",
    system_prompt: profile?.system_prompt ?? "",
    supports_stream: profile?.supports_stream ?? true,
    supports_vision: profile?.supports_vision ?? false,
    supports_tools: profile?.supports_tools ?? true,
  };
}

function toCreatePayload(draft: Draft): ModelProfileInput {
  return {
    name: draft.name.trim(),
    base_url: draft.base_url.trim() || null,
    model_name: draft.model_name.trim() || null,
    api_key: draft.api_key.trim() || null,
    system_prompt: draft.system_prompt.trim() || null,
    supports_stream: draft.supports_stream,
    supports_vision: draft.supports_vision,
    supports_tools: draft.supports_tools,
  };
}

function toPatchPayload(draft: Draft): Partial<ModelProfileInput> {
  const payload: Partial<ModelProfileInput> = {
    name: draft.name.trim(),
    base_url: draft.base_url.trim() || null,
    model_name: draft.model_name.trim() || null,
    system_prompt: draft.system_prompt.trim() || null,
    supports_stream: draft.supports_stream,
    supports_vision: draft.supports_vision,
    supports_tools: draft.supports_tools,
  };
  if (draft.api_key.trim()) {
    payload.api_key = draft.api_key.trim();
  }
  return payload;
}

function getLabels(locale: Locale) {
  if (locale === "zh") {
    return {
      apiBaseUrl: "API Base URL",
      apiKey: "API Key",
      delete: "删除",
      deleted: "已删除",
      deleting: "删除中",
      displayName: "显示名称",
      displayNamePlaceholder: "例如：我的视觉模型",
      failed: "操作失败",
      keepKey: "留空则保留已保存的 key",
      keySaved: "已保存",
      modelName: "Model Name",
      modelProfiles: "模型配置",
      modelUnset: "未填写 model",
      nameRequired: "请先填写显示名称",
      newModel: "新增模型",
      provider: "Provider",
      save: "保存",
      saved: "已保存",
      saving: "保存中",
      stream: "流式",
      systemPrompt: "系统提示",
      systemPromptPlaceholder: "例如：你是严谨的物理实验数据分析助手...",
      test: "测试",
      testing: "测试中",
      testOk: "测试通过",
      tools: "工具调用",
      vision: "视觉",
    };
  }

  return {
    apiBaseUrl: "API Base URL",
    apiKey: "API Key",
    delete: "Delete",
    deleted: "Deleted",
    deleting: "Deleting",
    displayName: "Display name",
    displayNamePlaceholder: "Example: My vision model",
    failed: "Operation failed",
    keepKey: "Leave blank to keep the saved key",
    keySaved: "saved",
    modelName: "Model Name",
    modelProfiles: "Model profiles",
    modelUnset: "model unset",
    nameRequired: "Enter a display name first",
    newModel: "New model",
    provider: "Provider",
    save: "Save",
    saved: "Saved",
    saving: "Saving",
    stream: "Streaming",
    systemPrompt: "System prompt",
    systemPromptPlaceholder: "Example: You are a rigorous physics lab data-analysis assistant...",
    test: "Test",
    testing: "Testing",
    testOk: "Test passed",
    tools: "Tools",
    vision: "Vision",
  };
}
