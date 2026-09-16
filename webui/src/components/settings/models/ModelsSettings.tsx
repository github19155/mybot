import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  Plus,
  Trash2,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  UpstreamModelPicker,
  ProviderPicker,
  ProviderPickerIcon,
  formatContextWindow,
  formatModelContextWindow,
  normalizeContextWindowTokens,
  settingsProviderConfigured,
} from "@/components/settings/shared/ModelControls";
import {
  SettingsGroup,
  SettingsRow,
  SettingsSectionTitle,
  SettingsStatusMessage,
  StatusPill,
} from "@/components/settings/shared/SettingsControls";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { cn } from "@/lib/utils";
import type { ModelSettingsRow, SettingsPayload } from "@/lib/types";

export interface AgentSettingsDraft {
  /** Canonical model_id of the row being edited; empty while creating a new model. */
  modelId: string;
  /** Human-facing display name (never an identity/selector). */
  displayName: string;
  provider: string;
  /** Upstream model string sent to the provider API; display data only. */
  model: string;
  maxTokens: number;
  contextWindowTokens: number;
  temperature: number;
  reasoningEffort: string;
  supportsVision: boolean;
  supportsImageGeneration: boolean;
  timezone: string;
  toolHintMaxLength: number;
}

const CONTEXT_WINDOW_TOKEN_OPTIONS = [65_536, 200_000, 262_144, 500_000, 1_048_576] as const;

const MODEL_ID_PATTERN = /^[a-z][a-z0-9_-]{0,63}$/;

export function isValidModelId(value: string): boolean {
  return MODEL_ID_PATTERN.test(value);
}

function modelCapability(model: ModelSettingsRow | null | undefined, key: "vision" | "image_generation"): boolean {
  return model?.capabilities?.[key] === true;
}

/** The canonical model_id currently selected as the instance default. */
function modelSettingsValue(payload: SettingsPayload): string {
  return payload.models.find((row) => row.is_default)?.model_id ?? "";
}

/** Suggest a canonical model_id slug from an upstream model string. */
function suggestedModelId(
  upstreamModel: string,
  models: Pick<ModelSettingsRow, "model_id">[],
): string {
  const leaf = upstreamModel.trim().split("/").filter(Boolean).at(-1) ?? "";
  let base = leaf
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
  if (base === "default") base = "model";
  if (base && !/^[a-z]/.test(base)) base = `m-${base}`;
  if (!base) return "";

  const existing = new Set(models.map((row) => row.model_id.toLowerCase()));
  if (!existing.has(base)) return base;

  for (let index = 2; ; index += 1) {
    const suffix = `-${index}`;
    const candidate = `${base.slice(0, 64 - suffix.length)}${suffix}`;
    if (!existing.has(candidate.toLowerCase())) return candidate;
  }
}

export const DEFAULT_AGENT_SETTINGS_DRAFT: AgentSettingsDraft = {
  modelId: "",
  displayName: "",
  provider: "",
  model: "",
  maxTokens: 8192,
  contextWindowTokens: 200_000,
  temperature: 0.1,
  reasoningEffort: "",
  supportsVision: false,
  supportsImageGeneration: false,
  timezone: "UTC",
  toolHintMaxLength: 40,
};

export function agentDraftFromPayload(
  payload: SettingsPayload,
  preferredModelId?: string,
): AgentSettingsDraft {
  const activeModelId = preferredModelId ?? modelSettingsValue(payload);
  const activeModel = payload.models.find((row) => row.model_id === activeModelId) ?? null;
  const agent = payload.agent;
  return {
    modelId: activeModel?.model_id ?? agent.model_id ?? "",
    displayName: activeModel?.display_name ?? agent.display_name ?? "",
    provider: activeModel?.provider ?? agent.provider ?? "",
    model: activeModel?.model ?? agent.model ?? "",
    maxTokens: activeModel?.generation_defaults?.max_tokens ?? 8192,
    contextWindowTokens: normalizeContextWindowTokens(
      activeModel?.context_window_tokens ?? agent.context_window_tokens,
    ),
    temperature: activeModel?.generation_defaults?.temperature ?? 0.1,
    reasoningEffort: activeModel?.generation_defaults?.reasoning_effort ?? "",
    supportsVision: modelCapability(activeModel, "vision"),
    supportsImageGeneration: modelCapability(activeModel, "image_generation"),
    timezone: agent.timezone,
    toolHintMaxLength: agent.tool_hint_max_length,
  };
}

export function ModelDeleteDialog({
  model,
  deleting,
  onOpenChange,
  onConfirm,
}: {
  model: ModelSettingsRow | null;
  deleting: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    t(key, { defaultValue: fallback, ...(values ?? {}) });
  const label = model?.display_name || model?.model_id || "";
  return (
    <Dialog open={model !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[440px]">
        <DialogHeader className="text-left">
          <DialogTitle>
            {tx("settings.models.deleteModelTitle", "Delete model?")}
          </DialogTitle>
          <DialogDescription className="leading-5">
            {tx(
              "settings.models.deleteModelHelp",
              "This removes the model “{{name}}”. Provider credentials are not affected.",
              { name: label },
            )}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            disabled={deleting}
            onClick={() => onOpenChange(false)}
          >
            {tx("settings.actions.cancel", "Cancel")}
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={deleting}
            onClick={onConfirm}
          >
            {deleting ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : null}
            {deleting
              ? tx("settings.actions.deleting", "Deleting...")
              : tx("settings.actions.delete", "Delete")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ModelsSettings({
  token,
  form,
  setForm,
  editingModelId,
  modelIdError,
  settings,
  dirty,
  creating,
  creatingSaving,
  promptOverrides,
  promptOverridesSaving,
  roleBindingsDraft,
  roleBindingsSaving,
  setRoleBindingsDraft,
  onSaveRoleBindings,
  saving,
  selectionSaving,
  showBrandLogos,
  providerSaving,
  onSelectActiveModel,
  onProviderOAuthLogin,
  onSave,
  onSavePromptOverrides,
  onSaveImageAnalysisModel,
  imageAnalysisSaving,
  onBeginCreate,
  onCancelCreate,
  onClearModelIdError,
  onSelectModel,
  onDeleteModel,
}: {
  token: string;
  form: AgentSettingsDraft;
  setForm: Dispatch<SetStateAction<AgentSettingsDraft>>;
  editingModelId: string;
  modelIdError: string | null;
  settings: SettingsPayload;
  dirty: boolean;
  creating: boolean;
  creatingSaving: boolean;
  promptOverrides: SettingsPayload["system_prompt_overrides"];
  promptOverridesSaving: boolean;
  roleBindingsDraft: Record<string, string | null>;
  roleBindingsSaving: boolean;
  setRoleBindingsDraft: Dispatch<SetStateAction<Record<string, string | null>>>;
  onSaveRoleBindings: () => void;
  saving: boolean;
  selectionSaving: boolean;
  showBrandLogos: boolean;
  providerSaving: string | null;
  onSelectActiveModel: (modelId: string) => void;
  onProviderOAuthLogin: (provider: string) => void;
  onSave: () => void;
  onSavePromptOverrides: (overrides: SettingsPayload["system_prompt_overrides"]) => void;
  onSaveImageAnalysisModel: (modelId: string | null) => void;
  imageAnalysisSaving: boolean;
  onBeginCreate: () => void;
  onCancelCreate: () => void;
  onClearModelIdError: () => void;
  onSelectModel: (modelId: string) => void;
  onDeleteModel: (model: ModelSettingsRow) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    t(key, { defaultValue: fallback, ...(values ?? {}) });
  const [editorOpen, setEditorOpen] = useState(false);
  const modelIdInputRef = useRef<HTMLInputElement>(null);
  const suggestedModelIdRef = useRef<string | null>(null);
  const [editorRowKey, setEditorRowKey] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [promptDraft, setPromptDraft] = useState<SettingsPayload["system_prompt_overrides"]>(
    promptOverrides,
  );

  useEffect(() => {
    setPromptDraft(promptOverrides);
  }, [promptOverrides]);
  useEffect(() => {
    if (modelIdError) modelIdInputRef.current?.focus();
  }, [modelIdError]);
  useEffect(() => {
    if (!creating) suggestedModelIdRef.current = null;
  }, [creating]);
  const modelRows = [...settings.models].sort(
    (left, right) => Number(right.is_default) - Number(left.is_default),
  );
  const visionModels = settings.models.filter((model) => model.capabilities.vision === true);
  const roles = settings.subagent_roles ?? [];
  const roleBindingsDirty = roles.some(
    (role) => role.name in roleBindingsDraft && roleBindingsDraft[role.name] !== role.model_id,
  );
  const modelRowsByKey = new Map(modelRows.map((row) => [row.model_id, row]));
  const modelConfigurationRows = modelRows.map((row) => ({
    key: `model:${row.model_id}`,
    name: row.display_name || row.model_id,
    modelId: row.model_id,
    model: row,
  }));
  const selectedModelConfiguration = modelRowsByKey.get(editingModelId) ?? null;
  const activeModelConfigurationRowKey =
    editorRowKey ??
    modelConfigurationRows.find((row) => row.modelId === selectedModelConfiguration?.model_id)?.key ??
    null;
  useEffect(() => {
    setAdvancedOpen(false);
  }, [editorOpen, selectedModelConfiguration?.model_id]);

  const configuredProviders = settings.providers.filter((provider) => provider.configured);
  const selectedProvider = settings.providers.find((provider) => provider.name === form.provider);
  const selectableProviders = uniqueProviders([
    ...configuredProviders,
    ...(selectedProvider ? [selectedProvider] : []),
  ]);
  const providerOptions = selectableProviders;
  const providerValue = providerOptions.some((provider) => provider.name === form.provider)
    ? form.provider
    : "";
  const selectedProviderNeedsSignIn =
    selectedProvider?.auth_type === "oauth" && !selectedProvider.configured;
  const selectedProviderSigningIn = providerSaving === selectedProvider?.name;
  const selectedProviderConfigured = settingsProviderConfigured(settings, form.provider);
  const modelFieldsMissing =
    !form.modelId.trim() ||
    !isValidModelId(form.modelId) ||
    !form.displayName.trim() ||
    !form.model.trim() ||
    !form.provider.trim() ||
    form.maxTokens <= 0 ||
    form.temperature < 0 ||
    form.temperature > 2;
  const selectedModelConfigurationReferenced = Boolean(
    selectedModelConfiguration && (
      selectedModelConfiguration.is_default ||
      (selectedModelConfiguration.usages?.length ?? 0) > 0
    ),
  );
  const selectionBusy = selectionSaving || saving;
  const selectModelConfiguration = (row: ModelSettingsRow, rowKey: string) => {
    const toggleCurrentModelConfiguration =
      !creating &&
      selectedModelConfiguration?.model_id === row.model_id &&
      activeModelConfigurationRowKey === rowKey;
    onSelectModel(row.model_id);
    if (toggleCurrentModelConfiguration) {
      setEditorOpen((open) => !open);
      return;
    }
    setForm(agentDraftFromPayload(settings, row.model_id));
    setEditorRowKey(rowKey);
    setEditorOpen(true);
  };

  const renderModelConfigurationEditor = () => (
    <div
      id="model-configuration-editor"
      data-testid="model-configuration-editor"
      className={cn(
        "mx-3 mb-3 divide-y divide-border/45 overflow-hidden rounded-floating border border-border/45 bg-background/80 shadow-sm motion-reduce:animate-none animate-in fade-in-0 slide-in-from-top-1 duration-200 sm:mx-5 lg:mx-auto lg:w-[calc(100%-2.5rem)] lg:max-w-6xl",
        creating && "mt-3",
      )}
    >
      {creating ? (
        <div className="flex min-h-[52px] items-center px-4 py-3 sm:px-5">
          <span className="text-[13px] font-semibold text-foreground/85">
            {tx("settings.models.newModel", "New model")}
          </span>
        </div>
      ) : null}
      <SettingsRow
        title={tx("settings.models.modelId", "Model ID")}
        description={tx(
          "settings.models.modelIdHelp",
          "Stable canonical ID used by /model and model settings. Lowercase letters, digits, “-” and “_”; fixed after creation.",
        )}
      >
        <div
          className={cn(
            "w-[min(280px,70vw)] motion-reduce:animate-none",
            modelIdError && "animate-[model-id-shake_180ms_ease-in-out]",
          )}
        >
          <Input
            ref={modelIdInputRef}
            autoFocus={creating}
            disabled={!creating}
            aria-label={tx("settings.models.modelId", "Model ID")}
            aria-invalid={Boolean(modelIdError)}
            aria-describedby={modelIdError ? "model-id-error" : undefined}
            value={form.modelId}
            placeholder={tx("settings.models.modelIdPlaceholder", "e.g. fast")}
            autoCapitalize="none"
            spellCheck={false}
            onChange={(event) => {
              suggestedModelIdRef.current = null;
              onClearModelIdError();
              setForm((prev) => ({ ...prev, modelId: event.target.value }));
            }}
            className={cn(
              "h-8 rounded-full text-[13px]",
              modelIdError &&
                "border-destructive/70 focus-visible:border-destructive focus-visible:ring-destructive/25",
            )}
          />
          {modelIdError ? (
            <p
              id="model-id-error"
              role="alert"
              className="mt-1.5 px-1 text-[12px] leading-4 text-destructive"
            >
              {modelIdError}
            </p>
          ) : null}
        </div>
      </SettingsRow>
      <SettingsRow
        title={tx("settings.models.displayName", "Display name")}
        description={tx(
          "settings.models.displayNameHelp",
          "Human-facing label. Never an identity or selector.",
        )}
      >
        <Input
          aria-label={tx("settings.models.displayName", "Display name")}
          value={form.displayName}
          placeholder={tx("settings.models.displayNamePlaceholder", "e.g. Fast writing")}
          onChange={(event) => {
            setForm((prev) => ({ ...prev, displayName: event.target.value }));
          }}
          className="h-8 w-[min(280px,70vw)] rounded-full text-[13px]"
        />
      </SettingsRow>
      <SettingsRow title={t("settings.rows.provider")}>
        <ProviderPicker
          providers={providerOptions}
          value={providerValue}
          emptyLabel={t("settings.byok.noConfiguredProviders")}
          showProviderLogos={showBrandLogos}
          onChange={(provider) => {
            const providerChanged = provider !== form.provider;
            const clearSuggestedId =
              creating &&
              providerChanged &&
              suggestedModelIdRef.current !== null &&
              form.modelId === suggestedModelIdRef.current;
            if (clearSuggestedId) suggestedModelIdRef.current = null;
            setForm((prev) => ({
              ...prev,
              provider,
              model: provider === prev.provider ? prev.model : "",
              modelId: clearSuggestedId ? "" : prev.modelId,
            }));
          }}
        />
      </SettingsRow>
      {selectedProviderNeedsSignIn ? (
        <SettingsRow
          title={tx("settings.oauth.signInRequired", "Sign in required")}
          description={tx(
            "settings.oauth.signInBeforeSaving",
            "Sign in before saving this provider in the model.",
          )}
        >
          <Button
            size="sm"
            variant="outline"
            onClick={() => selectedProvider && onProviderOAuthLogin(selectedProvider.name)}
            disabled={!selectedProvider?.oauth_login_supported || selectedProviderSigningIn}
            className="rounded-full"
          >
            {selectedProviderSigningIn ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : null}
            {selectedProviderSigningIn
              ? tx("settings.oauth.signingIn", "Signing in...")
              : tx("settings.oauth.signIn", "Sign in")}
          </Button>
        </SettingsRow>
      ) : null}
      <SettingsRow title={t("settings.rows.model")}>
        <UpstreamModelPicker
          token={token}
          settings={settings}
          provider={form.provider}
          value={form.model}
          showProviderLogos={showBrandLogos}
          onChange={(model) => {
            const canSuggestId =
              creating &&
              (!form.modelId.trim() || form.modelId === suggestedModelIdRef.current);
            const suggestion = canSuggestId
              ? suggestedModelId(model, settings.models)
              : "";
            if (canSuggestId) suggestedModelIdRef.current = suggestion;
            setForm((prev) => ({
              ...prev,
              model,
              modelId: canSuggestId ? suggestion : prev.modelId,
            }));
          }}
        />
      </SettingsRow>
      <button
        type="button"
        aria-expanded={advancedOpen}
        onClick={() => setAdvancedOpen((value) => !value)}
        className="flex min-h-[62px] w-full items-center justify-between gap-4 px-4 py-3.5 text-left transition-colors hover:bg-muted/30 sm:px-5"
      >
        <span>
          <span className="block text-[14px] font-medium text-foreground">
            {tx("settings.models.advancedOptions", "Advanced options")}
          </span>
          <span className="mt-0.5 block text-[12px] text-muted-foreground">
            {tx(
              "settings.models.advancedSummary",
              "Context {{context}} · Max {{max}} tokens",
              {
                context: formatModelContextWindow(form.contextWindowTokens),
                max: formatContextWindow(form.maxTokens),
              },
            )}
          </span>
        </span>
        <ChevronDown
          className={cn(
            "h-4 w-4 shrink-0 text-muted-foreground transition-transform",
            advancedOpen && "rotate-180",
          )}
          aria-hidden
        />
      </button>
      {advancedOpen ? (
        <div className="bg-muted/12 px-4 py-4 sm:px-5">
          <ModelAdvancedFields
            maxTokens={form.maxTokens}
            contextWindowTokens={form.contextWindowTokens}
            temperature={form.temperature}
            reasoningEffort={form.reasoningEffort}
            supportsVision={form.supportsVision}
            supportsImageGeneration={form.supportsImageGeneration}
            onChange={(value) => setForm((prev) => ({ ...prev, ...value }))}
          />
        </div>
      ) : null}
      <div className="flex min-h-[58px] flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
        {creating ? (
          <Button
            size="sm"
            variant="ghost"
            className="self-start rounded-full text-muted-foreground"
            disabled={creatingSaving}
            onClick={() => {
              setEditorOpen(false);
              onCancelCreate();
            }}
          >
            {tx("settings.actions.cancel", "Cancel")}
          </Button>
        ) : selectedModelConfiguration ? (
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <Button
              size="sm"
              variant="ghost"
              className="rounded-full text-muted-foreground hover:text-destructive"
              disabled={selectedModelConfigurationReferenced || saving || selectionSaving}
              aria-describedby={
                selectedModelConfigurationReferenced ? "model-configuration-delete-hint" : undefined
              }
              onClick={() => onDeleteModel(selectedModelConfiguration)}
            >
              <Trash2 className="mr-1.5 h-3.5 w-3.5" aria-hidden />
              {tx("settings.actions.delete", "Delete")}
            </Button>
            {selectedModelConfigurationReferenced ? (
              <span
                id="model-configuration-delete-hint"
                className="text-[11px] leading-4 text-muted-foreground"
              >
                {tx(
                  "settings.models.removeBeforeDelete",
                  "This model is the default or still in use; remove its references before deleting it.",
                )}
              </span>
            ) : null}
          </div>
        ) : null}
        <div className="flex items-center justify-end gap-3">
          <Button
            size="sm"
            variant="outline"
            className="rounded-full"
            disabled={
              (!creating && !dirty) ||
              !selectedProviderConfigured ||
              modelFieldsMissing ||
              saving ||
              selectionSaving
            }
            onClick={onSave}
          >
            {saving || creatingSaving
              ? tx("settings.actions.saving", "Saving...")
              : tx("settings.actions.save", "Save")}
          </Button>
        </div>
      </div>
    </div>
  );

return (
    <div className="space-y-7">
      <section>
        <SettingsSectionTitle>
          {tx("settings.models.models", "Models")}
        </SettingsSectionTitle>
        <SettingsGroup>
          <div role="list" className="divide-y divide-border/45">
            {modelConfigurationRows.map(({ key, name, modelId, model }) => {
              const provider = modelProviderKey(model, settings);
              const modelConfigured = settingsProviderConfigured(settings, model.provider);
              const isSelected = editorOpen && !creating && activeModelConfigurationRowKey === key && selectedModelConfiguration?.model_id === modelId;
              return (
                <div key={key} role="listitem">
                  <div data-testid={`model-configuration-row-${name}`} className={cn("group flex min-h-[76px] items-center gap-3 px-4 py-3 transition-colors sm:px-5", "hover:bg-muted/25", isSelected && "bg-muted/45 hover:bg-muted/45")}>
                    <button type="button" aria-pressed={selectedModelConfiguration?.model_id === modelId} aria-expanded={isSelected} aria-controls={isSelected ? "model-configuration-editor" : undefined} onClick={() => selectModelConfiguration(model, key)} className="flex min-w-0 flex-1 items-center gap-3 rounded-control text-left outline-none focus-visible:ring-2 focus-visible:ring-ring">
                      <ProviderPickerIcon provider={provider} showBrandLogos={showBrandLogos} unconfigured={!modelConfigured} />
                      <span className="min-w-0 flex-1"><span className="flex min-w-0 flex-wrap items-center gap-2"><span className="truncate text-[14px] font-medium text-foreground">{name}</span>{model.is_default ? (<StatusPill tone="success">{tx("settings.models.active", "Default")}</StatusPill>) : null}{!modelConfigured ? (<span className="text-[11px] font-medium text-amber-700 dark:text-amber-300">{tx("settings.models.providerSetupRequired", "Provider setup required")}</span>) : null}</span><span className="mt-0.5 block truncate text-[12px] text-muted-foreground">{model.model_id} · {model.model}</span></span>
                      <ChevronRight className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", isSelected && "rotate-90")} aria-hidden />
                    </button>
                    <Button type="button" size="sm" variant={model.is_default ? "secondary" : "outline"} className="shrink-0 rounded-full" disabled={selectionBusy || model.is_default || !modelConfigured} onClick={() => onSelectActiveModel(model.model_id)}>{model.is_default ? tx("settings.models.active", "Default") : tx("settings.models.useModel", "Use")}</Button>
                  </div>
                  {isSelected ? renderModelConfigurationEditor() : null}
                </div>
              );
            })}
          </div>
          {!creating ? (<button type="button" className="flex min-h-[58px] w-full items-center justify-between gap-3 px-4 py-3 text-left outline-none transition-colors hover:bg-muted/30 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 sm:px-5" disabled={selectionBusy} onClick={() => { setEditorRowKey(null); setEditorOpen(true); onBeginCreate(); }}><span className="inline-flex items-center text-[13px] font-medium"><Plus className="mr-1.5 h-3.5 w-3.5" aria-hidden />{tx("settings.models.newModel", "New model")}</span>{selectionSaving ? (<SettingsStatusMessage><span className="inline-flex items-center gap-1.5"><Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />{tx("settings.actions.saving", "Saving...")}</span></SettingsStatusMessage>) : null}</button>) : null}
          {creating && editorOpen ? renderModelConfigurationEditor() : null}
        </SettingsGroup>
      </section>
      <section>
        <SettingsSectionTitle>
          {tx("settings.models.imageAnalysis.title", "Image analysis fallback")}
        </SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={tx("settings.models.imageAnalysis.model", "Vision model")}
            description={tx(
              "settings.models.imageAnalysis.help",
              "Used by the image_analyze tool when the active model cannot see images. Mark vision-capable models in Advanced options.",
            )}
          >
            <div className="flex flex-wrap items-center gap-2">
              <select
                aria-label={tx("settings.models.imageAnalysis.model", "Vision model")}
                value={settings.agent.image_analysis_model_id ?? ""}
                disabled={imageAnalysisSaving}
                onChange={(event) =>
                  onSaveImageAnalysisModel(event.target.value || null)
                }
                className="h-9 max-w-full rounded-control border border-input bg-background px-3 text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <option value="">
                  {tx("settings.models.imageAnalysis.perRequest", "Choose per request")}
                </option>
                {visionModels.map((model) => (
                  <option key={model.model_id} value={model.model_id}>
                    {model.is_default ? tx("settings.values.default", "Default") : (model.display_name || model.model_id)}
                    {" — "}
                    {model.model}
                  </option>
                ))}
              </select>
              {imageAnalysisSaving ? (
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-label={tx("settings.actions.saving", "Saving...")} />
              ) : null}
            </div>
          </SettingsRow>
        </SettingsGroup>
      </section>
      {roles.length > 0 ? (
        <section>
          <SettingsSectionTitle>
            {tx("settings.models.subagentRoles.title", "Subagent roles")}
          </SettingsSectionTitle>
          <SettingsGroup>
            <SettingsRow
              title={tx("settings.models.subagentRoles.concurrency", "Concurrent background tasks")}
              description={tx(
                "settings.models.subagentRoles.concurrencyHelp",
                "Up to {{count}} tasks run at once; additional tasks queue. Inherited roles use the parent task's model.",
                { count: settings.max_concurrent_subagents ?? 16 },
              )}
            >
              <StatusPill tone="neutral">{settings.max_concurrent_subagents ?? 16}</StatusPill>
            </SettingsRow>
            {roles.map((role) => (
              <SettingsRow
                key={role.name}
                title={tx(`settings.models.subagentRoles.names.${role.name}`, role.name)}
                description={tx(`settings.models.subagentRoles.descriptions.${role.name}`, role.description)}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPill tone="neutral">
                    {tx(`settings.models.subagentRoles.permissions.${role.permissions}`, role.permissions)}
                  </StatusPill>
                  <select
                    aria-label={tx("settings.models.subagentRoles.modelLabel", "Model for {{role}}", {
                      role: tx(`settings.models.subagentRoles.names.${role.name}`, role.name),
                    })}
                    value={(role.name in roleBindingsDraft ? roleBindingsDraft[role.name] : role.model_id) ?? ""}
                    onChange={(event) => {
                      const modelId = event.target.value || null;
                      setRoleBindingsDraft((current) => ({ ...current, [role.name]: modelId }));
                    }}
                    className="h-9 max-w-full rounded-control border border-input bg-background px-3 text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <option value="">
                      {tx("settings.models.subagentRoles.inherit", "Inherit parent model")}
                    </option>
                    {modelRows.map((row) => (
                      <option key={row.model_id} value={row.model_id}>{row.display_name || row.model_id}</option>
                    ))}
                  </select>
                </div>
              </SettingsRow>
            ))}
            <div className="flex justify-end px-4 py-3 sm:px-5">
              <Button
                type="button"
                aria-label={tx("settings.models.subagentRoles.save", "Save role bindings")}
                disabled={!roleBindingsDirty || roleBindingsSaving}
                onClick={onSaveRoleBindings}
              >
                {roleBindingsSaving ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
                {roleBindingsSaving
                  ? tx("settings.models.subagentRoles.saving", "Saving role bindings...")
                  : tx("settings.models.subagentRoles.save", "Save role bindings")}
              </Button>
            </div>
          </SettingsGroup>
        </section>
      ) : null}
      <section>
        <SettingsSectionTitle>
          {tx("settings.models.promptOverrides", "System prompt overrides")}
        </SettingsSectionTitle>
        <SettingsGroup>
          {promptDraft.map((row, index) => (
            <div
              key={index}
              className="flex flex-col gap-2 border-b border-border px-4 py-3 last:border-b-0 sm:px-5"
            >
              <Input
                value={row.prompt}
                onChange={(event) =>
                  setPromptDraft(
                    promptDraft.map((item, i) =>
                      i === index ? { ...item, prompt: event.target.value } : item,
                    ),
                  )
                }
                placeholder={tx("settings.models.promptOverridePlaceholder", "Custom system prompt")}
              />
              <div className="flex items-center gap-2">
                <Input
                  value={row.model_ids.join(", ")}
                  onChange={(event) =>
                    setPromptDraft(
                      promptDraft.map((item, i) =>
                        i === index
                          ? {
                              ...item,
                              model_ids: event.target.value
                                .split(",")
                                .map((id) => id.trim())
                                .filter(Boolean),
                            }
                          : item,
                      ),
                    )
                  }
                  placeholder={tx(
                    "settings.models.promptOverrideModelsPlaceholder",
                    "Model IDs, comma-separated",
                  )}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={tx("settings.actions.delete", "Delete")}
                  onClick={() => setPromptDraft(promptDraft.filter((_, i) => i !== index))}
                >
                  <Trash2 className="h-4 w-4" aria-hidden />
                </Button>
              </div>
            </div>
          ))}
          <div className="flex items-center justify-between gap-3 px-4 py-3 sm:px-5">
            <Button
              type="button"
              variant="ghost"
              disabled={promptOverridesSaving}
              onClick={() => setPromptDraft([...promptDraft, { prompt: "", model_ids: [] }])}
            >
              <Plus className="mr-1.5 h-3.5 w-3.5" aria-hidden />
              {tx("settings.models.addPromptOverride", "Add override")}
            </Button>
            <Button
              type="button"
              aria-label={`${tx("settings.actions.save", "Save")} ${tx(
                "settings.models.promptOverrides",
                "System prompt overrides",
              )}`}
              disabled={
                promptOverridesSaving
                || JSON.stringify(promptDraft) === JSON.stringify(promptOverrides)
              }
              onClick={() =>
                onSavePromptOverrides(
                  promptDraft.filter((row) => row.prompt.trim() && row.model_ids.length > 0),
                )
              }
            >
              {promptOverridesSaving ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : null}
              {promptOverridesSaving
                ? tx("settings.actions.saving", "Saving...")
                : tx("settings.actions.save", "Save")}
            </Button>
          </div>
        </SettingsGroup>
      </section>
    </div>
  );
}

function ModelAdvancedFields({
  maxTokens,
  contextWindowTokens,
  temperature,
  reasoningEffort,
  supportsVision,
  supportsImageGeneration,
  onChange,
}: {
  maxTokens: number;
  contextWindowTokens: number;
  temperature: number;
  reasoningEffort: string;
  supportsVision: boolean;
  supportsImageGeneration: boolean;
  onChange: (
    value: Partial<
      Pick<
        AgentSettingsDraft,
        | "maxTokens"
        | "contextWindowTokens"
        | "temperature"
        | "reasoningEffort"
        | "supportsVision"
        | "supportsImageGeneration"
      >
    >,
  ) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const contextWindowOptions = Array.from(
    new Set([...CONTEXT_WINDOW_TOKEN_OPTIONS, contextWindowTokens]),
  ).sort((left, right) => left - right);
  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-[12px] font-medium text-muted-foreground">
            {tx("settings.models.maxTokens", "Max output tokens")}
          </span>
          <Input
            type="number"
            min={1}
            step={1}
            value={maxTokens}
            onChange={(event) => {
              const value = Number(event.target.value);
              if (Number.isFinite(value)) onChange({ maxTokens: value });
            }}
            className="h-9 text-[13px]"
          />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-[12px] font-medium text-muted-foreground">
            {tx("settings.models.temperature", "Temperature")}
          </span>
          <Input
            type="number"
            min={0}
            max={2}
            step={0.1}
            value={temperature}
            onChange={(event) => {
              const value = Number(event.target.value);
              if (Number.isFinite(value)) onChange({ temperature: value });
            }}
            className="h-9 text-[13px]"
          />
        </label>
      </div>
      <div>
        <span className="mb-2 block text-[12px] font-medium text-muted-foreground">
          {tx("settings.rows.contextWindow", "Context window")}
        </span>
        <SegmentedControl
          value={String(contextWindowTokens)}
          options={contextWindowOptions.map((tokens) => ({
            value: String(tokens),
            label: formatModelContextWindow(tokens),
          }))}
          onChange={(value) =>
            onChange({ contextWindowTokens: normalizeContextWindowTokens(Number(value)) })
          }
        />
      </div>
      <div className="space-y-2">
        <label className="flex items-center gap-2 text-[13px] text-foreground">
          <input
            type="checkbox"
            checked={supportsVision}
            onChange={(event) => onChange({ supportsVision: event.target.checked })}
            className="h-4 w-4 rounded border-input"
          />
          <span>
            {tx("settings.models.supportsVision", "This model supports native image input")}
          </span>
        </label>
        <label className="flex items-center gap-2 text-[13px] text-foreground">
          <input
            type="checkbox"
            checked={supportsImageGeneration}
            onChange={(event) => onChange({ supportsImageGeneration: event.target.checked })}
            className="h-4 w-4 rounded border-input"
          />
          <span>
            {tx("settings.models.supportsImageGeneration", "This model supports image generation")}
          </span>
        </label>
      </div>
      <label className="block">
        <span className="mb-1.5 block text-[12px] font-medium text-muted-foreground">
          {tx("settings.models.reasoningEffort", "Reasoning effort")}
        </span>
        <Input
          value={reasoningEffort}
          onChange={(event) => onChange({ reasoningEffort: event.target.value })}
          placeholder={tx("settings.values.default", "Default")}
          autoCapitalize="none"
          spellCheck={false}
          className="h-9 text-[13px]"
        />
      </label>
    </div>
  );
}

function uniqueProviders(
  providers: SettingsPayload["providers"],
): SettingsPayload["providers"] {
  const seen = new Set<string>();
  return providers.filter((provider) => {
    if (seen.has(provider.name)) return false;
    seen.add(provider.name);
    return true;
  });
}

function modelProviderKey(
  preset: ModelSettingsRow,
  settings: SettingsPayload,
  options: { draftProvider?: string } = {},
): string {
  const provider = options.draftProvider ?? preset.provider;
  return provider || settings.agent.provider;
}
