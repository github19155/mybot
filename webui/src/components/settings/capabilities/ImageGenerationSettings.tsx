import type { Dispatch, SetStateAction } from "react";
import { useTranslation } from "react-i18next";

import { ProviderPicker, optionRowsWithCurrent } from "@/components/settings/shared/ModelControls";
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
import type { ImageGenerationSettingsUpdate, SettingsPayload } from "@/lib/types";

const IMAGE_ASPECT_RATIO_OPTIONS = ["1:1", "3:4", "9:16", "4:3", "16:9", "3:2", "2:3", "21:9"];
const IMAGE_SIZE_OPTIONS = ["1K", "2K", "4K", "1024x1024", "1536x1024", "1024x1536"];

export const DEFAULT_IMAGE_GENERATION_FORM: ImageGenerationSettingsUpdate = {
  enabled: false,
  modelId: "",
  defaultAspectRatio: "1:1",
  defaultImageSize: "1K",
  maxImagesPerTurn: 4,
};

export function imageGenerationFormFromPayload(payload: SettingsPayload): ImageGenerationSettingsUpdate {
  return {
    enabled: payload.image_generation.enabled,
    modelId: payload.image_generation.model_id ?? "",
    defaultAspectRatio: payload.image_generation.default_aspect_ratio,
    defaultImageSize: payload.image_generation.default_image_size,
    maxImagesPerTurn: payload.image_generation.max_images_per_turn,
  };
}

export function ImageGenerationSettings({
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
  form: ImageGenerationSettingsUpdate;
  dirty: boolean;
  saving: boolean;
  onChangeForm: Dispatch<SetStateAction<ImageGenerationSettingsUpdate>>;
  onSave: () => void;
  onOpenProviders: () => void;
  onRestart?: () => void;
  isRestarting?: boolean;
  requiresRestartPending: boolean;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const imageGenerationModels = settings.models.filter(
    (row) => row.capabilities.image_generation === true,
  );
  const selectedImageModel = imageGenerationModels.find(
    (row) => row.model_id === form.modelId,
  ) ?? null;
  const selectedProvider = selectedImageModel
    ? settings.providers.find((provider) => provider.name === selectedImageModel.provider)
    : undefined;
  const providerConfigured = Boolean(selectedImageModel && selectedProvider?.configured);
  const missingModel = form.enabled && !selectedImageModel;
  const missingCredential = Boolean(selectedImageModel) && form.enabled && !providerConfigured;
  const notAvailable = tx("settings.values.notAvailable", "Not available");
  const aspectOptions = optionRowsWithCurrent(
    IMAGE_ASPECT_RATIO_OPTIONS.map((value) => ({ name: value, label: value })),
    form.defaultAspectRatio,
  );
  const sizeOptions = optionRowsWithCurrent(
    IMAGE_SIZE_OPTIONS.map((value) => ({ name: value, label: value })),
    form.defaultImageSize,
  );

  return (
    <div className="space-y-7">
      <section>
        <SettingsSectionTitle>{tx("settings.sections.imageGeneration", "Image generation")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow title={tx("settings.rows.imageGeneration", "Image generation")}>
            <ToggleButton
              checked={form.enabled}
              onChange={(enabled) => onChangeForm((prev) => ({ ...prev, enabled }))}
              ariaLabel={tx("settings.rows.imageGeneration", "Image generation")}
              label={form.enabled ? tx("settings.values.on", "On") : tx("settings.values.off", "Off")}
            />
          </SettingsRow>
          <ReadOnlyRow
            title={tx("settings.rows.imageProvider", "Image provider")}
            value={selectedImageModel?.provider || notAvailable}
          />
          <ReadOnlyRow
            title={tx("settings.rows.imageModelValue", "Upstream model")}
            value={selectedImageModel?.model || notAvailable}
          />
          <ReadOnlyRow
            title={tx("settings.rows.imageDisplayName", "Display name")}
            value={selectedImageModel?.display_name || notAvailable}
          />
          <SettingsRow
            title={tx("settings.rows.imageProviderStatus", "Provider status")}
            description={tx("settings.help.imageProviderStatus", "Image generation reuses provider credentials from Providers.")}
          >
            <div className="flex flex-wrap items-center justify-end gap-2">
              <StatusPill tone={selectedImageModel && providerConfigured ? "success" : "neutral"}>
                {selectedImageModel && providerConfigured
                  ? tx("settings.values.configured", "Configured")
                  : selectedImageModel
                    ? tx("settings.values.notConfigured", "Not configured")
                    : tx("settings.values.notAvailable", "Not available")}
              </StatusPill>
              {selectedImageModel && !providerConfigured ? (
                <Button size="sm" variant="outline" onClick={onOpenProviders} className="rounded-full">
                  {tx("settings.image.configureProvider", "Configure provider")}
                </Button>
              ) : null}
            </div>
          </SettingsRow>
          <ReadOnlyRow
            title={tx("settings.rows.imageProviderBase", "Provider base")}
            value={selectedProvider?.api_base || selectedProvider?.default_api_base || notAvailable}
          />
        </SettingsGroup>
      </section>

      <section>
        <SettingsSectionTitle>{tx("settings.sections.imageDefaults", "Defaults")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={tx("settings.rows.imageModel", "Image model")}
            description={tx(
              "settings.help.imageModel",
              "Select an image-generation capable model from the configured model catalog.",
            )}
          >
            <select
              aria-label={tx("settings.rows.imageModel", "Image model")}
              value={form.modelId}
              onChange={(event) => onChangeForm((prev) => ({ ...prev, modelId: event.target.value }))}
              className="h-9 max-w-[360px] rounded-control border border-input bg-background px-3 text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value="">
                {tx("settings.image.selectModel", "Select image model")}
              </option>
              {imageGenerationModels.map((row) => (
                <option key={row.model_id} value={row.model_id}>
                  {row.display_name || row.model_id} — {row.provider} · {row.model}
                </option>
              ))}
            </select>
          </SettingsRow>
          <SettingsRow title={tx("settings.rows.defaultAspectRatio", "Default aspect")}>
            <ProviderPicker
              providers={aspectOptions}
              value={form.defaultAspectRatio}
              emptyLabel={tx("settings.image.selectAspect", "Select aspect")}
              onChange={(defaultAspectRatio) =>
                onChangeForm((prev) => ({ ...prev, defaultAspectRatio }))
              }
            />
          </SettingsRow>
          <SettingsRow title={tx("settings.rows.defaultImageSize", "Default size")}>
            <ProviderPicker
              providers={sizeOptions}
              value={form.defaultImageSize}
              emptyLabel={tx("settings.image.selectSize", "Select size")}
              onChange={(defaultImageSize) =>
                onChangeForm((prev) => ({ ...prev, defaultImageSize }))
              }
            />
          </SettingsRow>
          <SettingsRow title={tx("settings.rows.maxImagesPerTurn", "Max images per turn")}>
            <NumberInput
              value={form.maxImagesPerTurn}
              min={1}
              max={8}
              onChange={(maxImagesPerTurn) =>
                onChangeForm((prev) => ({ ...prev, maxImagesPerTurn }))
              }
            />
          </SettingsRow>
          <ReadOnlyRow title={tx("settings.rows.imageSaveDir", "Save directory")} value={settings.image_generation.save_dir} />
          <RestartSettingsFooter
            dirty={dirty}
            saving={saving}
            pendingRestart={requiresRestartPending}
            disabled={missingModel || missingCredential}
            message={
              missingModel
                ? tx("settings.image.missingModel", "Select an image-generation model before enabling image generation.")
                : missingCredential
                  ? tx("settings.image.missingCredential", "Configure this provider before enabling image generation.")
                  : undefined
            }
            dirtyMessage={tx("settings.status.restartAfterSaving", "Save changes, then restart when ready.")}
            pendingMessage={tx("settings.status.savedRestartApply", "Saved. Restart when ready.")}
            onSave={onSave}
            onRestart={onRestart}
            isRestarting={isRestarting}
          />
        </SettingsGroup>
      </section>
    </div>
  );
}