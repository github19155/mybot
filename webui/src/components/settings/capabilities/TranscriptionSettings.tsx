import type { Dispatch, SetStateAction } from "react";
import { useTranslation } from "react-i18next";

import {
  NumberInput,
  ReadOnlyRow,
  RestartSettingsFooter,
  SettingsGroup,
  SettingsRow,
  SettingsSectionTitle,
  StatusPill,
} from "@/components/settings/shared/SettingsControls";
import { ToggleButton } from "@/components/settings/ToggleButton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { SettingsPayload, TranscriptionSettingsUpdate } from "@/lib/types";

export const DEFAULT_TRANSCRIPTION_FORM: TranscriptionSettingsUpdate = {
  enabled: true,
  modelId: "",
  language: "",
  maxDurationSec: 120,
  maxUploadMb: 25,
};

export const DEFAULT_TRANSCRIPTION_SETTINGS: NonNullable<SettingsPayload["transcription"]> = {
  enabled: true,
  model_id: null,
  language: null,
  max_duration_sec: 120,
  max_upload_mb: 25,
};

export function transcriptionFormFromPayload(payload: SettingsPayload): TranscriptionSettingsUpdate {
  const transcription = payload.transcription ?? DEFAULT_TRANSCRIPTION_SETTINGS;
  return {
    enabled: transcription.enabled,
    modelId: transcription.model_id ?? "",
    language: transcription.language ?? "",
    maxDurationSec: transcription.max_duration_sec,
    maxUploadMb: transcription.max_upload_mb,
  };
}

export function TranscriptionSettings({
  settings,
  form,
  dirty,
  saving,
  onChangeForm,
  onSave,
  onOpenProviders,
  onRestart,
  isRestarting,
  requiresRestartPending,
}: {
  settings: SettingsPayload;
  form: TranscriptionSettingsUpdate;
  dirty: boolean;
  saving: boolean;
  onChangeForm: Dispatch<SetStateAction<TranscriptionSettingsUpdate>>;
  onSave: () => void;
  onOpenProviders: () => void;
  showBrandLogos: boolean;
  onRestart?: () => void;
  isRestarting?: boolean;
  requiresRestartPending: boolean;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const transcriptionModels = settings.models.filter(
    (row) => row.capabilities?.transcription === true,
  );
  const selectedModel = transcriptionModels.find((row) => row.model_id === form.modelId) ?? null;
  const selectedProvider = selectedModel
    ? settings.providers.find((provider) => provider.name === selectedModel.provider)
    : null;
  const providerConfigured = selectedProvider?.configured === true;

  return (
    <section>
      <SettingsSectionTitle>{tx("settings.sections.voiceInput", "Voice input")}</SettingsSectionTitle>
      <SettingsGroup>
        <SettingsRow
          title={tx("settings.rows.transcription", "Transcription")}
          description={tx("settings.help.transcription", "Transcribe microphone input before sending it. Chat channel voice messages use the same settings.")}
        >
          <ToggleButton
            checked={form.enabled}
            onChange={(enabled) => onChangeForm((prev) => ({ ...prev, enabled }))}
            ariaLabel={tx("settings.rows.transcription", "Transcription")}
            label={form.enabled ? tx("settings.values.on", "On") : tx("settings.values.off", "Off")}
          />
        </SettingsRow>
        <SettingsRow
          title={tx("settings.rows.transcriptionModel", "Transcription model")}
          description={tx(
            "settings.help.transcriptionModel",
            "Choose a configured model that supports transcription.",
          )}
        >
          <select
            aria-label={tx("settings.rows.transcriptionModel", "Transcription model")}
            value={form.modelId}
            onChange={(event) => onChangeForm((prev) => ({ ...prev, modelId: event.target.value }))}
            className="h-9 max-w-[360px] rounded-control border border-input bg-background px-3 text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="">{tx("settings.voice.selectModel", "Select transcription model")}</option>
            {transcriptionModels.map((row) => (
              <option key={row.model_id} value={row.model_id}>
                {row.display_name || row.model_id} — {row.provider} · {row.model}
              </option>
            ))}
          </select>
        </SettingsRow>
        <ReadOnlyRow
          title={tx("settings.rows.transcriptionProvider", "Provider")}
          value={selectedModel?.provider ?? tx("settings.values.notAvailable", "Not available")}
        />
        <ReadOnlyRow
          title={tx("settings.rows.transcriptionUpstreamModel", "Upstream model")}
          value={selectedModel?.model ?? tx("settings.values.notAvailable", "Not available")}
        />
        <ReadOnlyRow
          title={tx("settings.rows.transcriptionDisplayName", "Display name")}
          value={selectedModel?.display_name ?? tx("settings.values.notAvailable", "Not available")}
        />
        <SettingsRow
          title={tx("settings.rows.transcriptionProviderStatus", "Provider status")}
          description={tx("settings.help.transcriptionProviderStatus", "API keys stay under providers, not in transcription settings.")}
        >
          <div className="flex flex-wrap items-center justify-end gap-2">
            <StatusPill tone={providerConfigured ? "success" : "neutral"}>
              {providerConfigured
                ? tx("settings.values.configured", "Configured")
                : tx("settings.values.notConfigured", "Not configured")}
            </StatusPill>
            {selectedModel && !providerConfigured ? (
              <Button size="sm" variant="outline" onClick={onOpenProviders} className="rounded-full">
                {tx("settings.voice.configureProvider", "Configure provider")}
              </Button>
            ) : null}
          </div>
        </SettingsRow>
        <SettingsRow
          title={tx("settings.rows.transcriptionLanguage", "Language")}
          description={tx("settings.help.transcriptionLanguage", "Optional ISO-639 hint such as en, zh, ja, or ko.")}
        >
          <Input
            value={form.language}
            onChange={(event) => onChangeForm((prev) => ({ ...prev, language: event.target.value }))}
            placeholder={tx("settings.voice.languageAuto", "Auto")}
            className="h-8 w-[min(180px,60vw)] rounded-full text-[13px]"
          />
        </SettingsRow>
        <SettingsRow title={tx("settings.rows.voiceLimits", "Limits")}>
          <div className="flex flex-wrap justify-end gap-2">
            <NumberInput
              value={form.maxDurationSec}
              min={1}
              max={600}
              suffix="s"
              onChange={(maxDurationSec) => onChangeForm((prev) => ({ ...prev, maxDurationSec }))}
            />
            <NumberInput
              value={form.maxUploadMb}
              min={1}
              max={100}
              suffix="MB"
              onChange={(maxUploadMb) => onChangeForm((prev) => ({ ...prev, maxUploadMb }))}
            />
          </div>
        </SettingsRow>
        <RestartSettingsFooter
          dirty={dirty}
          saving={saving}
          pendingRestart={requiresRestartPending}
          dirtyMessage={tx("settings.status.restartAfterSaving", "Save changes, then restart when ready.")}
          pendingMessage={tx("settings.status.savedRestartApply", "Saved. Restart when ready.")}
          onSave={onSave}
          onRestart={onRestart}
          isRestarting={isRestarting}
        />
      </SettingsGroup>
    </section>
  );
}
