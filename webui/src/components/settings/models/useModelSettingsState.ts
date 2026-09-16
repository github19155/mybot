import { useRef, useState } from "react";

import {
  DEFAULT_AGENT_SETTINGS_DRAFT,
  agentDraftFromPayload,
  type AgentSettingsDraft,
} from "@/components/settings/models/ModelsSettings";
import type { ProviderForm } from "@/components/settings/models/ProviderSettings";
import type { ModelSettingsRow, ProviderOAuthAuthorizationRequired, SettingsPayload } from "@/lib/types";

export function useModelSettingsState(initialSettings: SettingsPayload | null) {
  const initialForm = initialSettings
    ? agentDraftFromPayload(initialSettings)
    : DEFAULT_AGENT_SETTINGS_DRAFT;
  const [saving, setSaving] = useState(false);
  const [modelCreating, setModelCreating] = useState(false);
  const [modelIdError, setModelIdError] = useState<string | null>(null);
  const [modelConfigurationSaving, setModelConfigurationSaving] = useState(false);
  const [modelSelecting, setModelSelecting] = useState(false);
  const [imageAnalysisSaving, setImageAnalysisSaving] = useState(false);
  const [modelPendingDelete, setModelPendingDelete] =
    useState<ModelSettingsRow | null>(null);
  const modelBeforeCreateRef = useRef<string | null>(null);
  const [providerSaving, setProviderSaving] = useState<string | null>(null);
  const [providerOAuthFlow, setProviderOAuthFlow] =
    useState<ProviderOAuthAuthorizationRequired | null>(null);
  const providerOAuthFlowRef = useRef<ProviderOAuthAuthorizationRequired | null>(null);
  const [providerOAuthResponse, setProviderOAuthResponse] = useState("");
  const [providerOAuthCompleting, setProviderOAuthCompleting] = useState(false);
  const [providerOAuthDialogError, setProviderOAuthDialogError] = useState<string | null>(null);
  const [expandedProvider, setExpandedProvider] = useState<string | null>(null);
  const [providerForms, setProviderForms] = useState<Record<string, ProviderForm>>({});
  const [visibleProviderKeys, setVisibleProviderKeys] = useState<Record<string, boolean>>({});
  const [editingProviderKeys, setEditingProviderKeys] = useState<Record<string, boolean>>({});
  const [form, setForm] = useState<AgentSettingsDraft>(initialForm);
  const [editingModelId, setEditingModelId] = useState(
    initialForm.modelId,
  );
  const [promptOverrides, setPromptOverrides] = useState<
    SettingsPayload["system_prompt_overrides"]
  >(() => initialSettings?.system_prompt_overrides ?? []);
  const [promptOverridesSaving, setPromptOverridesSaving] = useState(false);
  const [roleBindingsDraft, setRoleBindingsDraft] = useState<Record<string, string | null>>({});
  const [roleBindingsSaving, setRoleBindingsSaving] = useState(false);

  return {
    editingProviderKeys,
    expandedProvider,
    form,
    imageAnalysisSaving,
    modelConfigurationSaving,
    promptOverrides,
    promptOverridesSaving,
    roleBindingsDraft,
    roleBindingsSaving,
    modelBeforeCreateRef,
    modelCreating,
    editingModelId,
    modelIdError,
    modelPendingDelete,
    modelSelecting,
    providerForms,
    providerOAuthCompleting,
    providerOAuthDialogError,
    providerOAuthFlow,
    providerOAuthFlowRef,
    providerOAuthResponse,
    providerSaving,
    saving,
    setEditingProviderKeys,
    setExpandedProvider,
    setForm,
    setImageAnalysisSaving,
    setModelConfigurationSaving,
    setPromptOverrides,
    setPromptOverridesSaving,
    setRoleBindingsDraft,
    setRoleBindingsSaving,
    setModelCreating,
    setEditingModelId,
    setModelIdError,
    setModelPendingDelete,
    setModelSelecting,
    setProviderForms,
    setProviderOAuthCompleting,
    setProviderOAuthDialogError,
    setProviderOAuthFlow,
    setProviderOAuthResponse,
    setProviderSaving,
    setSaving,
    setVisibleProviderKeys,
    visibleProviderKeys,
  };
}

export type ModelSettingsState = ReturnType<typeof useModelSettingsState>;
