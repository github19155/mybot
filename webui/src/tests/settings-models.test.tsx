import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { SettingsPayload } from "@/lib/types";
import { requestMutationMock, jsonResponse, settingsPayload, renderSettingsView, openPopover, installSettingsViewTestHooks } from "@/tests/settings-test-utils";


function settingsPayloadWithBackup(): {
  payload: SettingsPayload;
  backupModel: SettingsPayload["models"][number];
} {
  const base = settingsPayload();
  const backupModel = {
    ...base.models[0],
    model_id: "backup",
    display_name: "Backup",
    is_default: false,
    provider: "anthropic",
    model: "anthropic/claude-sonnet-4",
  };
  return {
    backupModel,
    payload: {
      ...base,
      models: [base.models[0], backupModel],
      providers: [
        {
          name: "openai",
          label: "OpenAI",
          configured: true,
        },
        {
          name: "anthropic",
          label: "Anthropic",
          configured: true,
        },
      ],
    },
  };
}

async function toggleModelConfigurationEditor(name = "Primary") {
  const row = await screen.findByTestId(`model-configuration-row-${name}`);
  fireEvent.click(within(row).getAllByRole("button")[0]);
}

describe("Settings models", () => {
  installSettingsViewTestHooks();

  it("saves role bindings without discarding a selection changed during the request", async () => {
    const payload: SettingsPayload = {
      ...settingsPayload(),
      system_prompt_overrides: [],
      max_concurrent_subagents: 16,
      subagent_roles: [
        { name: "coder", description: "Implement changes", permissions: "read-write-exec", model_id: null },
      ],
    };
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    let completeSave!: (payload: SettingsPayload) => void;
    requestMutationMock.mockImplementationOnce(() => new Promise<SettingsPayload>((resolve) => {
      completeSave = resolve;
    }));
    renderSettingsView({ initialSection: "models", initialSettings: payload });
    const binding = screen.getByRole("combobox", { name: "Model for coder" });
    fireEvent.change(binding, { target: { value: "primary" } });
    fireEvent.click(screen.getByRole("button", { name: "Save role bindings" }));
    fireEvent.change(binding, { target: { value: "" } });
    const savedPayload: SettingsPayload = {
      ...payload,
      subagent_roles: [{ ...payload.subagent_roles![0], model_id: "primary" }],
    };
    completeSave(savedPayload);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Save role bindings" })).toBeEnabled();
    });
    expect(binding).toHaveValue("");
    expect(requestMutationMock).toHaveBeenCalledWith(
      "settings.subagent_roles.update", { bindings: { coder: "primary" } }, 20_000,
    );

    requestMutationMock.mockRejectedValueOnce(new Error("Model configuration is unavailable"));
    fireEvent.click(screen.getByRole("button", { name: "Save role bindings" }));
    await screen.findByText("Model configuration is unavailable");
    expect(binding).toHaveValue("");
    requestMutationMock.mockResolvedValueOnce(payload);
    fireEvent.click(screen.getByRole("button", { name: "Save role bindings" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Save role bindings" })).toBeDisabled();
    });
    expect(requestMutationMock).toHaveBeenLastCalledWith(
      "settings.subagent_roles.update", { bindings: { coder: null } }, 20_000,
    );
    expect(screen.queryByText("Model configuration is unavailable")).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Model for coder" })).toHaveValue("");
  });



  it("marks a model as vision-capable in the editor", async () => {
    const payload = settingsPayload();
    payload.providers = [{ name: "openai", label: "OpenAI", configured: true }];
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    requestMutationMock.mockResolvedValueOnce({
      ...payload,
      models: [
        {
          ...payload.models[0],
          capabilities: { ...payload.models[0].capabilities, vision: true },
        },
      ],
    });

    renderSettingsView({ initialSection: "models", initialSettings: payload });
    await toggleModelConfigurationEditor();
    fireEvent.click(screen.getByRole("button", { name: /Advanced options/ }));

    const visionCheckbox = screen.getByRole("checkbox", {
      name: "This model supports native image input",
    });
    expect(visionCheckbox).not.toBeChecked();
    fireEvent.click(visionCheckbox);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(requestMutationMock).toHaveBeenCalledWith(
        "settings.model_configuration.update",
        { model_id: "primary", capabilities: { vision: true } },
        20_000,
      ),
    );
  });

  it("selects a vision model for image analysis fallback", async () => {
    const base = settingsPayload();
    const visionModel = {
      ...base.models[0],
      model_id: "vision",
      display_name: "Vision",
      is_default: false,
      model: "openai/gpt-4o",
      capabilities: { ...base.models[0].capabilities, vision: true },
    };
    const payload: SettingsPayload = {
      ...base,
      agent: { ...base.agent, image_analysis_model_id: null },
      models: [...base.models, visionModel],
    };
    const updatedPayload: SettingsPayload = {
      ...payload,
      agent: { ...payload.agent, image_analysis_model_id: "vision" },
      image_analysis: {
        enabled: true,
        model_id: "vision",
        max_image_mb: 10,
        max_images: 4,
      },
    };
    requestMutationMock.mockResolvedValueOnce(updatedPayload);

    renderSettingsView({ initialSection: "models", initialSettings: payload });
    const fallback = screen.getByRole("combobox", { name: "Vision model" });
    expect(fallback).toHaveValue("");
    fireEvent.change(fallback, { target: { value: "vision" } });

    await waitFor(() =>
      expect(requestMutationMock).toHaveBeenCalledWith(
        "settings.agent.update",
        { image_analysis_model_id: "vision" },
        20_000,
      ),
    );
  });

  it("uses the model ID as the canonical identity", async () => {
    const payload = settingsPayload();
    payload.models[0] = {
      ...payload.models[0],
      model_id: "openai",
      display_name: "minimax",
    };
    payload.agent.model_id = "openai";
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));

    renderSettingsView({ initialSection: "models", initialSettings: payload });

    await toggleModelConfigurationEditor("minimax");

    const editor = screen.getByTestId("model-configuration-editor");
    const modelIdInput = within(editor).getByRole("textbox", { name: "Model ID" });
    expect(modelIdInput).toHaveValue("openai");
    expect(modelIdInput).toBeDisabled();
    expect(within(editor).getByRole("textbox", { name: "Display name" })).toHaveValue(
      "minimax",
    );
  });

  it("keeps generation parameters collapsed until advanced options are opened", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/settings") return jsonResponse(settingsPayload());
        if (url === "/api/settings/cli-apps") {
          return jsonResponse({ apps: [], installed_count: 0 });
        }
        if (url === "/api/settings/mcp-presets") {
          return jsonResponse({ presets: [], installed_count: 0 });
        }
        return { ok: false, status: 404, json: async () => ({}) } as Response;
      }),
    );

    renderSettingsView({ initialSection: "models" });

    expect(screen.queryByTestId("model-configuration-editor")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Advanced options/ })).not.toBeInTheDocument();
    await toggleModelConfigurationEditor();
    const advanced = await screen.findByRole("button", { name: /Advanced options/ });
    expect(screen.queryByText("Context window")).not.toBeInTheDocument();
    expect(screen.queryByText("Temperature")).not.toBeInTheDocument();

    fireEvent.click(advanced);

    expect(await screen.findByText("Context window")).toBeInTheDocument();
    expect(screen.getByText("Temperature")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "64K" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "200K" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "256K" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "500K" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "1M" })).toBeInTheDocument();
    const reasoningEffort = screen.getByLabelText("Reasoning effort");
    expect(reasoningEffort).toHaveProperty("type", "text");
    fireEvent.change(reasoningEffort, { target: { value: "provider-native-mode" } });
    expect(reasoningEffort).toHaveValue("provider-native-mode");
  });

  it("expands the model editor directly below the selected row", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/settings") return jsonResponse(settingsPayload());
        if (url === "/api/settings/cli-apps") {
          return jsonResponse({ apps: [], installed_count: 0 });
        }
        if (url === "/api/settings/mcp-presets") {
          return jsonResponse({ presets: [], installed_count: 0 });
        }
        return { ok: false, status: 404, json: async () => ({}) } as Response;
      }),
    );

    renderSettingsView({ initialSection: "models" });

    const row = await screen.findByTestId("model-configuration-row-Primary");
    const trigger = within(row).getAllByRole("button")[0];
    expect(screen.queryByTestId("model-configuration-editor")).not.toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(trigger);

    const editor = screen.getByTestId("model-configuration-editor");
    expect(trigger).toHaveAttribute("aria-pressed", "true");
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(trigger).toHaveAttribute("aria-controls", "model-configuration-editor");
    expect(row.parentElement).toHaveAttribute("role", "listitem");
    expect(row.parentElement?.parentElement).toHaveAttribute("role", "list");
    expect(row.nextElementSibling).toBe(editor);
    expect(editor).toHaveClass(
      "slide-in-from-top-1",
      "lg:max-w-6xl",
      "rounded-floating",
    );
    expect(within(editor).getByRole("textbox", { name: "Model ID" })).toHaveValue(
      "primary",
    );
    const deleteButton = within(editor).getByRole("button", { name: "Delete" });
    expect(deleteButton).toBeDisabled();
    expect(deleteButton).toHaveAttribute("aria-describedby", "model-configuration-delete-hint");
    expect(
      within(editor).getByText(
        "This model is the default or still in use; remove its references before deleting it.",
      ),
    ).toBeInTheDocument();

    fireEvent.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("model-configuration-editor")).not.toBeInTheDocument();
  });

  it("shows an inline error when a new model ID already exists", async () => {
    const { payload } = settingsPayloadWithBackup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}) }) as Response),
    );

    renderSettingsView({ initialSection: "models", initialSettings: payload });

    fireEvent.click(screen.getByRole("button", { name: "New model" }));
    const nameInput = screen.getByRole("textbox", { name: "Model ID" });
    fireEvent.change(nameInput, { target: { value: "primary" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Display name" }), {
      target: { value: "Writer" },
    });
    await openPopover(screen.getByRole("button", { name: "Select model" }));
    const modelSearch = await screen.findByRole("combobox", {
      name: "Search or type model ID",
    });
    fireEvent.change(modelSearch, { target: { value: "openai/gpt-4o-mini" } });
    fireEvent.keyDown(modelSearch, { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(requestMutationMock).not.toHaveBeenCalled();
    expect(nameInput).toHaveAttribute("aria-invalid", "true");
    expect(nameInput).toHaveAttribute("aria-describedby", "model-id-error");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "A model with this ID already exists.",
    );
    expect(nameInput.parentElement).toHaveClass(
      "animate-[model-id-shake_180ms_ease-in-out]",
    );

    fireEvent.change(nameInput, { target: { value: "writer" } });
    expect(nameInput).toHaveAttribute("aria-invalid", "false");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the active model row in the overview summary", async () => {
    const payload = settingsPayload();
    payload.providers = [{ name: "openai", label: "OpenAI", configured: true }];
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));

    renderSettingsView({ initialSection: "overview", initialSettings: payload });

    expect(await screen.findByText("Primary")).toBeInTheDocument();
    expect(screen.getByText("openai · openai/gpt-4o")).toBeInTheDocument();
  });

  it("marks the current model as unconfigured when its provider needs setup", async () => {
    const payload: SettingsPayload = {
      ...settingsPayload(),
      agent: {
        ...settingsPayload().agent,
        model: "openai-codex/gpt-5.1-codex",
        provider: "openai_codex",
        has_api_key: false,
      },
      models: [
        {
          ...settingsPayload().models[0],
          model: "openai-codex/gpt-5.1-codex",
          provider: "openai_codex",
        },
      ],
      providers: [
        {
          name: "openai_codex",
          label: "OpenAI Codex",
          configured: false,
          auth_type: "oauth",
          api_key_required: false,
          api_key_hint: null,
          api_base: null,
          default_api_base: null,
          oauth_account: null,
          oauth_expires_at: null,
          oauth_login_supported: true,
        },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/settings") return jsonResponse(payload);
        if (url === "/api/settings/cli-apps") {
          return jsonResponse({ apps: [], installed_count: 0 });
        }
        if (url === "/api/settings/mcp-presets") {
          return jsonResponse({ presets: [], installed_count: 0 });
        }
        return { ok: false, status: 404, json: async () => ({}) } as Response;
      }),
    );

    renderSettingsView({ initialSection: "models" });

    expect(await screen.findByText("Provider setup required")).toBeInTheDocument();
    await toggleModelConfigurationEditor();
    expect(screen.getAllByText(/Sign in before saving/).length).toBeGreaterThan(0);
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });

  it("keeps unsigned OAuth providers out of the active provider picker", async () => {
    const payload: SettingsPayload = {
      ...settingsPayload(),
      agent: {
        ...settingsPayload().agent,
        model: "deepseek-chat",
        provider: "deepseek",
      },
      models: [
        {
          ...settingsPayload().models[0],
          model: "deepseek-chat",
          provider: "deepseek",
        },
      ],
      providers: [
        {
          name: "deepseek",
          label: "DeepSeek",
          configured: true,
          auth_type: "api_key",
          api_key_required: true,
          api_key_hint: "sk-...",
          api_base: "https://api.deepseek.com",
          default_api_base: "https://api.deepseek.com",
        },
        {
          name: "openai_codex",
          label: "OpenAI Codex",
          configured: false,
          auth_type: "oauth",
          api_key_required: false,
          api_key_hint: null,
          api_base: null,
          default_api_base: null,
          oauth_account: null,
          oauth_expires_at: null,
          oauth_login_supported: true,
        },
        {
          name: "github_copilot",
          label: "GitHub Copilot",
          configured: false,
          auth_type: "oauth",
          api_key_required: false,
          api_key_hint: null,
          api_base: null,
          default_api_base: "https://api.githubcopilot.com",
          oauth_account: null,
          oauth_expires_at: null,
          oauth_login_supported: true,
        },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/settings") return jsonResponse(payload);
        if (url === "/api/settings/cli-apps") {
          return jsonResponse({ apps: [], installed_count: 0 });
        }
        if (url === "/api/settings/mcp-presets") {
          return jsonResponse({ presets: [], installed_count: 0 });
        }
        return { ok: false, status: 404, json: async () => ({}) } as Response;
      }),
    );

    renderSettingsView({ initialSection: "models" });

    await toggleModelConfigurationEditor();
    const deepseekButtons = await screen.findAllByRole("button", { name: /DeepSeek/ });
    const providerPicker = deepseekButtons.find(
      (button) => button.getAttribute("aria-haspopup") === "menu",
    );
    if (!providerPicker) throw new Error("provider picker was not found");
    fireEvent.pointerDown(providerPicker);

    expect(await screen.findByRole("menuitem", { name: /DeepSeek/ })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /OpenAI Codex/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /GitHub Copilot/ })).not.toBeInTheDocument();
  });

  it("does not fetch model lists for unsigned OAuth providers", async () => {
    const payload: SettingsPayload = {
      ...settingsPayload(),
      agent: {
        ...settingsPayload().agent,
        model: "",
        provider: "openai_codex",
      },
      models: [
        {
          ...settingsPayload().models[0],
          model: "",
          provider: "openai_codex",
        },
      ],
      providers: [
        {
          name: "openai_codex",
          label: "OpenAI Codex",
          configured: false,
          auth_type: "oauth",
          api_key_required: false,
          api_key_hint: null,
          api_base: null,
          default_api_base: null,
          oauth_account: null,
          oauth_expires_at: null,
          oauth_login_supported: true,
        },
        {
          name: "github_copilot",
          label: "GitHub Copilot",
          configured: false,
          auth_type: "oauth",
          api_key_required: false,
          api_key_hint: null,
          api_base: null,
          default_api_base: "https://api.githubcopilot.com",
          oauth_account: null,
          oauth_expires_at: null,
          oauth_login_supported: true,
        },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings") return jsonResponse(payload);
      if (url === "/api/settings/cli-apps") {
        return jsonResponse({ apps: [], installed_count: 0 });
      }
      if (url === "/api/settings/mcp-presets") {
        return jsonResponse({ presets: [], installed_count: 0 });
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderSettingsView({ initialSection: "models" });

    await toggleModelConfigurationEditor();
    await openPopover(await screen.findByRole("button", { name: /Select model/i }));
    expect(
      await screen.findByText("Configure this provider before loading models."),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).startsWith("/api/settings/provider-models"),
      ),
    ).toBe(false);
  });

  it("prefills manual model ids for configured OAuth providers", async () => {
    const payload: SettingsPayload = {
      ...settingsPayload(),
      agent: {
        ...settingsPayload().agent,
        model: "open-codex/gpt-5.5",
        provider: "openai_codex",
      },
      models: [
        {
          ...settingsPayload().models[0],
          model: "open-codex/gpt-5.5",
          provider: "openai_codex",
        },
      ],
      providers: [
        {
          name: "openai_codex",
          label: "OpenAI Codex",
          configured: true,
          auth_type: "oauth",
          api_key_required: false,
          api_key_hint: null,
          api_base: null,
          default_api_base: null,
          oauth_account: "acct-test",
          oauth_expires_at: null,
          oauth_login_supported: true,
        },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings") return jsonResponse(payload);
      if (url === "/api/settings/cli-apps") {
        return jsonResponse({ apps: [], installed_count: 0 });
      }
      if (url === "/api/settings/mcp-presets") {
        return jsonResponse({ presets: [], installed_count: 0 });
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderSettingsView({ initialSection: "models" });

    await toggleModelConfigurationEditor();
    const modelButtons = await screen.findAllByRole("button", { name: /open-codex\/gpt-5\.5/i });
    await openPopover(modelButtons[modelButtons.length - 1]);
    const input = (await screen.findByPlaceholderText("Search or type model ID")) as HTMLInputElement;
    expect(input.value).toBe("open-codex/gpt-5.5");

    fireEvent.change(input, { target: { value: "openai-codex/gpt-5.5" } });
    expect(await screen.findByText("“openai-codex/gpt-5.5”")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).startsWith("/api/settings/provider-models"),
      ),
    ).toBe(false);
  });

  it("defers the OrcaRouter catalog until the user searches", async () => {
    const base = settingsPayload();
    const payload: SettingsPayload = {
      ...base,
      agent: {
        ...base.agent,
        model: "orcarouter/auto",
        provider: "orcarouter",
      },
      models: [
        {
          ...base.models[0],
          model: "orcarouter/auto",
          provider: "orcarouter",
        },
      ],
      providers: [
        {
          name: "orcarouter",
          label: "OrcaRouter",
          configured: true,
          auth_type: "api_key",
          api_key_required: true,
          api_key_hint: "sk-o••••test",
          api_base: null,
          default_api_base: "https://api.orcarouter.ai/v1",
          model_catalog: "catalog",
        },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings") return jsonResponse(payload);
      if (url === "/api/settings/cli-apps") {
        return jsonResponse({ apps: [], installed_count: 0 });
      }
      if (url === "/api/settings/mcp-presets") {
        return jsonResponse({ presets: [], installed_count: 0 });
      }
      if (url === "/api/settings/provider-models?provider=orcarouter") {
        return jsonResponse({
          provider: "orcarouter",
          label: "OrcaRouter",
          status: "available",
          catalog_kind: "catalog",
          models: [
            { id: "orcarouter/auto", owned_by: "orcarouter" },
            { id: "anthropic/claude-sonnet-4.6", owned_by: "anthropic" },
          ],
          model_count: 2,
          fetched_at: 1,
        });
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderSettingsView({ initialSection: "models" });

    await toggleModelConfigurationEditor();
    const modelButtons = await screen.findAllByRole("button", { name: /orcarouter\/auto/i });
    await openPopover(modelButtons[modelButtons.length - 1]);
    expect(await screen.findByText("Search provider catalog to choose a model.")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).startsWith("/api/settings/provider-models"),
      ),
    ).toBe(false);

    fireEvent.change(screen.getByPlaceholderText("Search or type model ID"), {
      target: { value: "cl" },
    });

    await screen.findByText("anthropic/claude-sonnet-4.6");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/provider-models?provider=orcarouter",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("loads curated models for configured OAuth providers", async () => {
    const base = settingsPayload();
    const payload: SettingsPayload = {
      ...base,
      agent: {
        ...base.agent,
        model: "openai-codex/gpt-5.5",
        provider: "openai_codex",
      },
      models: [
        {
          ...base.models[0],
          model: "openai-codex/gpt-5.5",
          provider: "openai_codex",
        },
      ],
      providers: [
        {
          name: "openai_codex",
          label: "OpenAI Codex",
          configured: true,
          auth_type: "oauth",
          api_key_required: false,
          api_key_hint: null,
          api_base: null,
          default_api_base: "https://chatgpt.com/backend-api",
          model_catalog: "builtin",
          oauth_account: "acct-test",
          oauth_expires_at: null,
          oauth_login_supported: true,
        },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings/provider-models?provider=openai_codex") {
        return jsonResponse({
          provider: "openai_codex",
          label: "OpenAI Codex",
          status: "available",
          catalog_kind: "builtin",
          models: [
            {
              id: "openai-codex/gpt-5.6-sol",
              label: "GPT-5.6-Sol",
              description: "Latest frontier agentic coding model.",
              owned_by: "OpenAI Codex",
              context_window: 372000,
            },
          ],
          model_count: 1,
          fetched_at: 1,
        });
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderSettingsView({ initialSection: "models", initialSettings: payload });

    await toggleModelConfigurationEditor();
    const modelButtons = await screen.findAllByRole("button", {
      name: /openai-codex\/gpt-5\.5/i,
    });
    await openPopover(modelButtons[modelButtons.length - 1]);

    expect(await screen.findByText("GPT-5.6-Sol")).toBeInTheDocument();
    expect(screen.getByText(/Latest frontier agentic coding model\./)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/provider-models?provider=openai_codex",
      expect.objectContaining({ headers: { Authorization: "Bearer tok" } }),
    );
  });

  it("loads hybrid online models for configured OAuth providers", async () => {
    const base = settingsPayload();
    const payload: SettingsPayload = {
      ...base,
      agent: {
        ...base.agent,
        model: "xai-grok/grok-4.5",
        provider: "xai_grok",
      },
      models: [
        {
          ...base.models[0],
          model: "xai-grok/grok-4.5",
          provider: "xai_grok",
        },
      ],
      providers: [
        {
          name: "xai_grok",
          label: "xAI Grok",
          configured: true,
          auth_type: "oauth",
          api_key_required: false,
          api_key_hint: null,
          api_base: null,
          default_api_base: "https://cli-chat-proxy.grok.com/v1",
          model_catalog: "hybrid",
          oauth_account: "acct-test",
          oauth_expires_at: null,
          oauth_login_supported: true,
        },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings/provider-models?provider=xai_grok") {
        return jsonResponse({
          provider: "xai_grok",
          label: "xAI Grok",
          status: "available",
          catalog_kind: "hybrid",
          source: "remote",
          models: [
            {
              id: "xai-grok/grok-4.6",
              label: "Grok 4.6",
              description: "Latest frontier model",
              owned_by: "xAI",
              context_window: 500_000,
            },
            {
              id: "xai-grok/grok-4.5",
              label: "Grok 4.5",
              owned_by: "xAI",
              context_window: 500_000,
            },
          ],
          model_count: 2,
          fetched_at: 1,
        });
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    renderSettingsView({ initialSection: "models", initialSettings: payload });

    await toggleModelConfigurationEditor();
    const modelButtons = await screen.findAllByRole("button", {
      name: /xai-grok\/grok-4\.5/i,
    });
    await openPopover(modelButtons[modelButtons.length - 1]);

    expect(await screen.findByText("Grok 4.6")).toBeInTheDocument();
    expect(screen.getByText(/Latest frontier model/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/provider-models?provider=xai_grok",
      expect.objectContaining({ headers: { Authorization: "Bearer tok" } }),
    );
  });

  it("creates models in the inline editor and can cancel without opening a dialog", async () => {
    const payload = settingsPayload();
    payload.providers = [{ name: "openai", label: "OpenAI", configured: true }];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/settings") return jsonResponse(payload);
        if (url === "/api/settings/cli-apps") {
          return jsonResponse({ apps: [], installed_count: 0 });
        }
        if (url === "/api/settings/mcp-presets") {
          return jsonResponse({ presets: [], installed_count: 0 });
        }
        return { ok: false, status: 404, json: async () => ({}) } as Response;
      }),
    );

    renderSettingsView({ initialSection: "models" });

    const createButton = await screen.findByRole("button", { name: "New model" });
    expect(createButton).toHaveClass("w-full");
    fireEvent.click(createButton);

    expect(screen.queryByRole("dialog", { name: "New model" })).not.toBeInTheDocument();
    expect(screen.getByTestId("model-configuration-editor")).toHaveClass("mt-3", "mb-3");
    expect(screen.getByRole("textbox", { name: "Model ID" })).toHaveValue("");
    expect(screen.queryByText("Temperature")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Advanced options/ }));
    expect(screen.getByText("Temperature")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByTestId("model-configuration-editor")).not.toBeInTheDocument();
    expect(document.body.style.pointerEvents).not.toBe("none");

    fireEvent.click(screen.getByRole("button", { name: "New model" }));
    const nameInput = await screen.findByRole("textbox", { name: "Model ID" });
    expect(nameInput).toHaveValue("");
    expect(nameInput).toHaveAttribute("placeholder", "e.g. fast");

    await openPopover(screen.getByRole("button", { name: "Select model" }));
    const modelSearch = await screen.findByRole("combobox", {
      name: "Search or type model ID",
    });
    fireEvent.change(modelSearch, { target: { value: "openai/gpt-4o-mini" } });
    fireEvent.keyDown(modelSearch, { key: "Enter" });

    expect(nameInput).toHaveValue("gpt-4o-mini");
    fireEvent.change(screen.getByRole("textbox", { name: "Display name" }), {
      target: { value: "Writer" },
    });
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();

    fireEvent.change(nameInput, { target: { value: "writer" } });
    await openPopover(screen.getByRole("button", { name: /openai\/gpt-4o-mini/ }));
    const nextModelSearch = await screen.findByRole("combobox", {
      name: "Search or type model ID",
    });
    fireEvent.change(nextModelSearch, { target: { value: "openai/gpt-4.1-mini" } });
    fireEvent.keyDown(nextModelSearch, { key: "Enter" });
    expect(nameInput).toHaveValue("writer");
  });

  it("loads provider models and lets users choose one without typing the id manually", async () => {
    const payload: SettingsPayload = {
      ...settingsPayload(),
      agent: {
        ...settingsPayload().agent,
        model: "deepseek-chat",
        provider: "deepseek",
      },
      models: [
        {
          ...settingsPayload().models[0],
          model: "deepseek-chat",
          provider: "deepseek",
        },
      ],
      providers: [
        {
          name: "deepseek",
          label: "DeepSeek",
          configured: true,
          auth_type: "api_key",
          api_key_required: true,
          api_key_hint: "sk-...",
          api_base: "https://api.deepseek.com",
          default_api_base: "https://api.deepseek.com",
        },
      ],
    };
    const updatedPayload: SettingsPayload = {
      ...payload,
      agent: {
        ...payload.agent,
        model: "deepseek-reasoner",
      },
      models: [
        {
          ...payload.models[0],
          model: "deepseek-reasoner",
          generation_defaults: {
            ...payload.models[0].generation_defaults,
            temperature: 0.4,
            reasoning_effort: "provider-native-mode",
          },
        },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings") return jsonResponse(payload);
      if (url === "/api/settings/cli-apps") {
        return jsonResponse({ apps: [], installed_count: 0 });
      }
      if (url === "/api/settings/mcp-presets") {
        return jsonResponse({ presets: [], installed_count: 0 });
      }
      if (url === "/api/settings/provider-models?provider=deepseek") {
        return jsonResponse({
          provider: "deepseek",
          label: "DeepSeek",
          status: "available",
          catalog_kind: "official",
          models: [
            { id: "deepseek-chat", owned_by: "deepseek", context_window: 65536 },
            { id: "deepseek-reasoner", owned_by: "deepseek", context_window: 65536 },
          ],
          model_count: 2,
          fetched_at: 1,
        });
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    requestMutationMock.mockResolvedValueOnce(updatedPayload);

    renderSettingsView({ initialSection: "models" });

    await toggleModelConfigurationEditor();
    const modelButtons = await screen.findAllByRole("button", { name: /deepseek-chat/ });
    await openPopover(modelButtons[modelButtons.length - 1]);
    await screen.findByText("deepseek-reasoner");
    fireEvent.click(screen.getAllByText("deepseek-reasoner")[0]);
    fireEvent.click(screen.getByRole("button", { name: /Advanced options/ }));
    fireEvent.change(screen.getByLabelText("Temperature"), {
      target: { value: "0.4" },
    });
    fireEvent.change(screen.getByLabelText("Reasoning effort"), {
      target: { value: "provider-native-mode" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/settings/provider-models?provider=deepseek",
        expect.objectContaining({
          headers: { Authorization: "Bearer tok" },
        }),
      ),
    );
    await waitFor(() => {
      const saveCall = requestMutationMock.mock.calls.find(([action]) =>
        action === "settings.model_configuration.update",
      );
      expect(saveCall).toBeDefined();
      expect(saveCall?.[1]).toEqual({
        model_id: "primary",
        model: "deepseek-reasoner",
        generation_defaults: {
          temperature: 0.4,
          reasoning_effort: "provider-native-mode",
        },
      });
    });
  });
});