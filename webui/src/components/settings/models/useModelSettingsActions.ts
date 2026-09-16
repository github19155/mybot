import { useCallback, type Dispatch, type SetStateAction } from "react";
import type { TFunction } from "i18next";

import type {
  ApplySettingsPayload,
  MaybeRestartHostEngine,
  PendingRestartSections,
} from "@/components/settings/contracts";
import { agentDraftFromPayload, isValidModelId } from "@/components/settings/models/ModelsSettings";
import {
  CUSTOM_PROVIDER_CREATION_KEY,
  providerFormFromRow,
  type CustomProviderDraft,
} from "@/components/settings/models/ProviderSettings";
import type { ModelSettingsState } from "@/components/settings/models/useModelSettingsState";
import { normalizeContextWindowTokens } from "@/components/settings/shared/ModelControls";
import {
  ApiError,
  completeProviderOAuth,
  createModelConfiguration,
  createProviderSettings,
  deleteModelConfiguration,
  loginProviderOAuth,
  logoutProviderOAuth,
  updateModelConfiguration,
  updateSettings,
  updateSystemPromptOverrides,
  updateSubagentRoles,
  updateProviderSettings,
} from "@/lib/api";
import type { NanobotClient } from "@/lib/nanobot-client";
import type {
  ModelSettingsRow,
  ProviderOAuthAuthorizationRequired,
  ProviderOAuthCompletionResult,
  ProviderOAuthLoginResult,
  ProviderOAuthPending,
  ProviderSettingsUpdate,
  SettingsPayload,
} from "@/lib/types";

function isProviderOAuthAuthorizationRequired(
  payload: ProviderOAuthLoginResult,
): payload is ProviderOAuthAuthorizationRequired {
  return (payload as ProviderOAuthAuthorizationRequired).status === "authorization_required";
}

function isProviderOAuthPending(
  payload: ProviderOAuthCompletionResult,
): payload is ProviderOAuthPending {
  return (payload as ProviderOAuthPending).status === "pending";
}

function modelSupportsImageGeneration(model: ModelSettingsRow | null | undefined): boolean {
  return model?.capabilities?.image_generation === true;
}

interface ModelSettingsActionsOptions {
  state: ModelSettingsState;
  settings: SettingsPayload | null;
  client: NanobotClient;
  t: TFunction;
  applyPayload: ApplySettingsPayload;
  maybeRestartHostEngine: MaybeRestartHostEngine;
  setPendingRestartSections: Dispatch<SetStateAction<PendingRestartSections>>;
  setError: Dispatch<SetStateAction<string | null>>;
  remoteBrowserAccess: boolean;
  closeProviderOAuthFlow: () => void;
  installCapabilities: (names: string[]) => Promise<boolean>;
  modelDirty: boolean;
  configuredModelProviderOptions: Array<{ name: string; label: string }>;
}

export function useModelSettingsActions({
  state,
  settings,
  client,
  t,
  applyPayload,
  maybeRestartHostEngine,
  setPendingRestartSections,
  setError,
  remoteBrowserAccess,
  closeProviderOAuthFlow,
  installCapabilities,
  modelDirty,
  configuredModelProviderOptions,
}: ModelSettingsActionsOptions) {
  const {
    expandedProvider,
    form,
    modelSelecting,
    modelConfigurationSaving,
    promptOverrides,
    promptOverridesSaving,
    roleBindingsDraft,
    roleBindingsSaving,
    imageAnalysisSaving,
    modelBeforeCreateRef,
    modelCreating,
    editingModelId,
    modelPendingDelete,
    providerForms,
    providerOAuthCompleting,
    providerOAuthFlowRef,
    providerOAuthResponse,
    providerSaving,
    saving,
    setEditingProviderKeys,
    setExpandedProvider,
    setForm,
    setImageAnalysisSaving,
    setModelSelecting,
    setModelConfigurationSaving,
    setPromptOverrides,
    setPromptOverridesSaving,
    setRoleBindingsDraft,
    setRoleBindingsSaving,
    setModelCreating,
    setEditingModelId,
    setModelIdError,
    setModelPendingDelete,
    setProviderForms,
    setProviderOAuthCompleting,
    setProviderOAuthDialogError,
    setProviderOAuthFlow,
    setProviderOAuthResponse,
    setProviderSaving,
    setSaving,
    setVisibleProviderKeys,
    visibleProviderKeys,
  } = state;

  const modelIdConflict = (modelId: string, currentId?: string) => {
    const normalized = modelId.toLowerCase();
    return settings?.models.some(
      (row) =>
        row.model_id !== currentId &&
        row.model_id.toLowerCase() === normalized,
    ) ?? false;
  };

  const showModelIdConflict = () => {
    setModelIdError(
      t("settings.models.modelIdDuplicate", {
        defaultValue: "A model with this ID already exists.",
      }),
    );
    setError(null);
  };

  const handleModelSaveError = (reason: unknown) => {
    if (reason instanceof ApiError && reason.status === 409) {
      showModelIdConflict();
      return;
    }
    setError((reason as Error).message);
  };

  const saveModelSettings = async () => {
    if (
      !settings ||
      saving ||
      modelSelecting ||
      modelConfigurationSaving
    ) {
      return;
    }

    if (modelCreating) {
      const modelId = form.modelId.trim();
      const displayName = form.displayName.trim();
      const provider = form.provider.trim();
      const model = form.model.trim();
      if (
        !modelId ||
        !isValidModelId(modelId) ||
        !displayName ||
        !provider ||
        !model ||
        form.maxTokens <= 0 ||
        form.contextWindowTokens <= 0 ||
        form.temperature < 0 ||
        form.temperature > 2
      ) {
        return;
      }
      if (modelIdConflict(modelId)) {
        showModelIdConflict();
        return;
      }
      setModelIdError(null);
      setModelConfigurationSaving(true);
      try {
        const payload = await createModelConfiguration(client, {
          modelId,
          displayName,
          provider,
          model,
          maxTokens: form.maxTokens,
          contextWindowTokens: form.contextWindowTokens,
          temperature: form.temperature,
          reasoningEffort: form.reasoningEffort || null,
          supportsVision: form.supportsVision,
          supportsImageGeneration: form.supportsImageGeneration,
        });
        const createdModelId = payload.created_model_id;
        applyPayload(payload);
        if (createdModelId) {
          setForm(agentDraftFromPayload(payload, createdModelId));
          setEditingModelId(createdModelId);
        }

        let finalPayload = payload;
        if (createdModelId && payload.agent.model_id !== createdModelId) {
          finalPayload = await updateSettings(client, { modelId: createdModelId });
          applyPayload(finalPayload);
        }
        if (createdModelId) {
          setForm(agentDraftFromPayload(finalPayload, createdModelId));
          setEditingModelId(createdModelId);
        }
        modelBeforeCreateRef.current = null;
        setModelIdError(null);
        setError(null);
      } catch (err) {
        handleModelSaveError(err);
      } finally {
        setModelConfigurationSaving(false);
      }
      return;
    }

    if (!modelDirty) return;
    const selectedModel = settings.models.find(
      (row) => row.model_id === editingModelId,
    );
    if (!selectedModel) return;
    const reasoningEffort = form.reasoningEffort || null;
    setSaving(true);
    try {
      const payload = await updateModelConfiguration(client, {
        modelId: selectedModel.model_id,
        displayName:
          form.displayName !== selectedModel.display_name ? form.displayName : undefined,
        model: form.model !== selectedModel.model ? form.model : undefined,
        provider: form.provider !== selectedModel.provider ? form.provider : undefined,
        maxTokens:
          form.maxTokens !== selectedModel.generation_defaults.max_tokens ? form.maxTokens : undefined,
        contextWindowTokens:
          form.contextWindowTokens !==
          normalizeContextWindowTokens(selectedModel.context_window_tokens)
            ? form.contextWindowTokens
            : undefined,
        temperature:
          form.temperature !== selectedModel.generation_defaults.temperature
            ? form.temperature
            : undefined,
        reasoningEffort:
          reasoningEffort !== selectedModel.generation_defaults.reasoning_effort
            ? reasoningEffort
            : undefined,
        supportsVision:
          form.supportsVision !== (selectedModel.capabilities.vision === true)
            ? form.supportsVision
            : undefined,
        supportsImageGeneration:
          form.supportsImageGeneration !== modelSupportsImageGeneration(selectedModel)
            ? form.supportsImageGeneration
            : undefined,
      });
      applyPayload(payload);
      setForm(agentDraftFromPayload(payload, selectedModel.model_id));
      setEditingModelId(selectedModel.model_id);
      setModelIdError(null);
      setError(null);
    } catch (err) {
      handleModelSaveError(err);
    } finally {
      setSaving(false);
    }
  };

  const beginModelCreation = () => {
    if (!settings || saving || modelSelecting || modelConfigurationSaving) return;
    const defaultModel = settings.models.find((row) => row.is_default) ?? null;
    const currentProvider = defaultModel?.provider ?? settings.agent.provider;
    const provider =
      configuredModelProviderOptions.find((option) => option.name === currentProvider)?.name ??
      configuredModelProviderOptions[0]?.name ??
      "";
    modelBeforeCreateRef.current = editingModelId;
    setModelIdError(null);
    setForm((prev) => ({
      ...prev,
      modelId: "",
      displayName: "",
      provider,
      model: "",
      maxTokens: defaultModel?.generation_defaults?.max_tokens ?? settings.agent.generation_defaults.max_tokens,
      contextWindowTokens: normalizeContextWindowTokens(
        defaultModel?.context_window_tokens ?? settings.agent.context_window_tokens,
      ),
      temperature: defaultModel?.generation_defaults?.temperature ?? settings.agent.generation_defaults.temperature,
      reasoningEffort: defaultModel?.generation_defaults?.reasoning_effort ?? "",
      supportsVision: false,
      supportsImageGeneration: false,
    }));
    setModelCreating(true);
  };

  const cancelModelCreation = () => {
    if (!settings || modelConfigurationSaving) return;
    const previousModelId = modelBeforeCreateRef.current;
    setModelCreating(false);
    setModelIdError(null);
    setForm(agentDraftFromPayload(settings, previousModelId ?? undefined));
    setEditingModelId(previousModelId ?? agentDraftFromPayload(settings).modelId);
    modelBeforeCreateRef.current = null;
  };

  const saveImageAnalysisModel = async (modelId: string | null) => {
    if (!settings || imageAnalysisSaving) return;
    setImageAnalysisSaving(true);
    try {
      const payload = await updateSettings(client, {
        imageAnalysisModelId: modelId,
      });
      applyPayload(payload, { preserveAgentForm: true });
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setImageAnalysisSaving(false);
    }
  };

  const selectActiveModel = async (modelId: string) => {
    if (!settings || saving || modelSelecting || modelConfigurationSaving) return;
    if (settings.agent.model_id === modelId) return;
    setModelSelecting(true);
    try {
      const payload = await updateSettings(client, { modelId });
      applyPayload(payload, { preserveAgentForm: true });
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setModelSelecting(false);
    }
  };

  const savePromptOverrides = async (
    nextOverrides: SettingsPayload["system_prompt_overrides"],
  ) => {
    if (!settings || promptOverridesSaving || saving) return;
    const previous = promptOverrides;
    setPromptOverrides(nextOverrides);
    setPromptOverridesSaving(true);
    try {
      const payload = await updateSystemPromptOverrides(client, nextOverrides);
      applyPayload(payload, { preserveAgentForm: true });
      setError(null);
    } catch (err) {
      setPromptOverrides(previous);
      setError((err as Error).message);
    } finally {
      setPromptOverridesSaving(false);
    }
  };

  const saveRoleBindings = async () => {
    if (!settings || roleBindingsSaving) return;
    const bindings = Object.fromEntries(
      (settings.subagent_roles ?? [])
        .filter((role) => role.name in roleBindingsDraft && roleBindingsDraft[role.name] !== role.model_id)
        .map((role) => [role.name, roleBindingsDraft[role.name]]),
    );
    if (!Object.keys(bindings).length) return;
    setRoleBindingsSaving(true);
    try {
      const payload = await updateSubagentRoles(client, bindings);
      applyPayload(payload, { preserveAgentForm: true });
      setRoleBindingsDraft((current) => Object.fromEntries(
        Object.entries(current).filter(([name, modelId]) => !(name in bindings) || modelId !== bindings[name]),
      ));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setRoleBindingsSaving(false);
    }
  };


  const handleDeleteModelConfiguration = async () => {
    if (
      !modelPendingDelete ||
      saving ||
      modelSelecting ||
      modelConfigurationSaving
    ) {
      return;
    }
    setSaving(true);
    try {
      const payload = await deleteModelConfiguration(client, modelPendingDelete.model_id);
      applyPayload(payload);
      setModelPendingDelete(null);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const saveProvider = async (providerName: string) => {
    if (providerSaving) return;
    const provider = settings?.providers.find((item) => item.name === providerName);
    if (!provider) return;
    const isOauthProvider = provider.auth_type === "oauth";
    const providerForm = providerForms[providerName] ?? providerFormFromRow(provider);
    const apiKey = providerForm.apiKey.trim();
    const apiKeyRequired = provider.api_key_required ?? true;
    if (!isOauthProvider && !provider.configured && apiKeyRequired && !apiKey) {
      setError(t("settings.byok.apiKeyRequired"));
      return;
    }
    setProviderSaving(providerName);
    try {
      const supportName = providerName === "bedrock"
        ? "bedrock"
        : providerName === "azure_openai"
          ? "azure"
          : null;
      if (supportName && !(await installCapabilities([supportName]))) return;
      const update: ProviderSettingsUpdate = { provider: providerName };
      if (!isOauthProvider) {
        update.apiKey = apiKey || undefined;
        update.apiBase = providerForm.apiBase.trim();
        if (provider.is_custom) update.displayName = providerForm.displayName.trim();
      }
      for (const field of provider.advanced_fields ?? []) {
        if (field === "api_type") update.apiType = providerForm.apiType;
        if (field === "proxy") update.proxy = providerForm.proxy.trim();
        if (field === "extra_headers") {
          update.extraHeaders = providerForm.extraHeaders.trim();
        }
        if (field === "extra_body") update.extraBody = providerForm.extraBody.trim();
        if (field === "extra_query") update.extraQuery = providerForm.extraQuery.trim();
        if (field === "thinking_style") {
          update.thinkingStyle = providerForm.thinkingStyle.trim();
        }
        if (field === "region") update.region = providerForm.region.trim();
        if (field === "profile") update.profile = providerForm.profile.trim();
      }
      const payload = await updateProviderSettings(client, update);
      applyPayload(payload);
      if (payload.requires_restart) {
        setPendingRestartSections((prev) => ({ ...prev, image: true }));
      }
      await maybeRestartHostEngine(payload);
      setProviderForms((prev) => ({
        ...prev,
        [providerName]: {
          ...providerForm,
          displayName: providerForm.displayName.trim(),
          apiKey: "",
          apiBase: providerForm.apiBase.trim(),
          proxy: providerForm.proxy.trim(),
          thinkingStyle: providerForm.thinkingStyle.trim(),
          region: providerForm.region.trim(),
          profile: providerForm.profile.trim(),
        },
      }));
      setVisibleProviderKeys((prev) => ({ ...prev, [providerName]: false }));
      setEditingProviderKeys((prev) => ({ ...prev, [providerName]: false }));
      if (!isOauthProvider) setExpandedProvider(null);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setProviderSaving(null);
    }
  };

  const createCustomProvider = async (draft: CustomProviderDraft): Promise<boolean> => {
    if (providerSaving) return false;
    setProviderSaving(CUSTOM_PROVIDER_CREATION_KEY);
    try {
      const payload = await createProviderSettings(client, {
        name: draft.name.trim(),
        apiKey: draft.apiKey.trim() || undefined,
        apiBase: draft.apiBase.trim(),
        proxy: draft.proxy.trim(),
        extraHeaders: draft.extraHeaders.trim(),
        extraBody: draft.extraBody.trim(),
        extraQuery: draft.extraQuery.trim(),
        thinkingStyle: draft.thinkingStyle.trim(),
      });
      applyPayload(payload);
      setExpandedProvider(null);
      setError(null);
      return true;
    } catch (err) {
      setError((err as Error).message);
      return false;
    } finally {
      setProviderSaving(null);
    }
  };

  const runProviderOAuth = async (providerName: string, action: "login" | "logout") => {
    if (providerSaving) return;
    let popup: Window | null = null;
    if (
      action === "login"
      && providerName === "xai_grok"
      && !remoteBrowserAccess
    ) {
      try {
        popup = window.open("about:blank", "_blank");
        if (popup) popup.opener = null;
      } catch {
        popup = null;
      }
    }
    setProviderSaving(providerName);
    try {
      const payload =
        action === "login"
          ? await loginProviderOAuth(
              client,
              providerName,
              providerName === "openai_codex" && remoteBrowserAccess,
            )
          : await logoutProviderOAuth(client, providerName);
      if (isProviderOAuthAuthorizationRequired(payload)) {
        try {
          if (popup && !popup.closed) popup.location.href = payload.authorization_url;
        } catch {
          // The dialog keeps the authorization link available when the popup was closed.
        }
        providerOAuthFlowRef.current = payload;
        setProviderOAuthFlow(payload);
        setProviderOAuthResponse("");
        setProviderOAuthDialogError(null);
        setExpandedProvider(providerName);
        setError(null);
        return;
      }
      popup?.close();
      closeProviderOAuthFlow();
      applyPayload(payload);
      setExpandedProvider(providerName);
      setError(null);
    } catch (err) {
      popup?.close();
      setError((err as Error).message);
    } finally {
      setProviderSaving(null);
    }
  };

  const completeProviderOAuthResponse = async () => {
    const flow = providerOAuthFlowRef.current;
    const authorizationResponse = providerOAuthResponse.trim();
    if (!flow || !authorizationResponse || providerOAuthCompleting) return;
    setProviderOAuthCompleting(true);
    setProviderOAuthDialogError(null);
    try {
      const payload = await completeProviderOAuth(
        client,
        flow.provider,
        flow.flow_id,
        authorizationResponse,
      );
      if (providerOAuthFlowRef.current?.flow_id !== flow.flow_id) return;
      if (isProviderOAuthPending(payload)) return;
      applyPayload(payload);
      setExpandedProvider(flow.provider);
      setError(null);
      closeProviderOAuthFlow();
    } catch (err) {
      if (providerOAuthFlowRef.current?.flow_id === flow.flow_id) {
        setProviderOAuthDialogError((err as Error).message);
      }
    } finally {
      setProviderOAuthCompleting(false);
    }
  };

  const resetProviderDraft = useCallback((providerName: string) => {
    const provider = settings?.providers.find((item) => item.name === providerName);
    if (!provider) return;
    setProviderForms((prev) => ({
      ...prev,
      [providerName]: providerFormFromRow(provider),
    }));
    setVisibleProviderKeys((prev) => ({ ...prev, [providerName]: false }));
    setEditingProviderKeys((prev) => ({ ...prev, [providerName]: false }));
  }, [settings]);

  const handleToggleProvider = useCallback((providerName: string) => {
    if (expandedProvider) resetProviderDraft(expandedProvider);
    setExpandedProvider(expandedProvider === providerName ? null : providerName);
  }, [expandedProvider, resetProviderDraft]);

  const toggleProviderKeyVisibility = (providerName: string) => {
    const isVisible = visibleProviderKeys[providerName];
    setVisibleProviderKeys((prev) => ({ ...prev, [providerName]: !isVisible }));
  };

  const toggleProviderKeyEditing = (providerName: string) => {
    setEditingProviderKeys((prev) => {
      const nextEditing = !prev[providerName];
      if (!nextEditing) {
        setProviderForms((forms) => ({
          ...forms,
          [providerName]: {
            ...(forms[providerName] ?? providerFormFromRow(
              settings?.providers.find((provider) => provider.name === providerName) ?? {
                name: providerName,
                label: providerName,
                configured: false,
              },
            )),
            apiKey: "",
          },
        }));
        setVisibleProviderKeys((visible) => ({ ...visible, [providerName]: false }));
      }
      return { ...prev, [providerName]: nextEditing };
    });
  };

  return {
    beginModelCreation,
    cancelModelCreation,
    selectActiveModel,
    savePromptOverrides,
    saveRoleBindings,
    completeProviderOAuthResponse,
    createCustomProvider,
    handleDeleteModelConfiguration,
    handleToggleProvider,
    resetProviderDraft,
    runProviderOAuth,
    saveImageAnalysisModel,
    saveModelSettings,
    saveProvider,
    toggleProviderKeyEditing,
    toggleProviderKeyVisibility,
  };
}
