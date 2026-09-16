import { fireEvent, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { SettingsPayload } from "@/lib/types";
import { requestMutationMock, jsonResponse, settingsPayload, renderSettingsView, installSettingsViewTestHooks } from "@/tests/settings-test-utils";


describe("Settings capabilities", () => {
  installSettingsViewTestHooks();


  it("selects and saves image generation models by canonical model ID", async () => {
    const base = settingsPayload();
    const imageDefault = {
      ...base.models[0],
      model_id: "image-default",
      display_name: "Canonical image",
      provider: "openrouter",
      model: "openai/gpt-5.4-image-2",
      capabilities: { ...base.models[0].capabilities, image_generation: true },
      is_default: true,
    };
    const imageAlternative = {
      ...imageDefault,
      model_id: "image-alternative",
      display_name: "Alternative image",
      provider: "gemini",
      model: "imagen-4.0-generate-001",
      is_default: false,
    };
    const payload: SettingsPayload = {
      ...base,
      models: [base.models[0], imageDefault, imageAlternative],
      providers: [
        { name: "openrouter", label: "OpenRouter", configured: true },
        { name: "gemini", label: "Gemini", configured: true },
      ],
      image_generation: {
        ...base.image_generation,
        model_id: "image-default",
      },
    };
    const updatedPayload: SettingsPayload = {
      ...payload,
      image_generation: { ...payload.image_generation, model_id: "image-alternative" },
    };
    requestMutationMock.mockResolvedValueOnce(updatedPayload);

    renderSettingsView({ initialSection: "image", initialSettings: payload });

    const modelSelect = screen.getByRole("combobox", { name: "Image model" });
    expect(modelSelect).toHaveValue("image-default");
    expect(screen.getByRole("option", { name: "Canonical image — openrouter · openai/gpt-5.4-image-2" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Alternative image — gemini · imagen-4.0-generate-001" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "OpenRouter" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Search or type model ID" })).not.toBeInTheDocument();
    expect(screen.queryByText("Custom / provider model")).not.toBeInTheDocument();

    fireEvent.change(modelSelect, { target: { value: "image-alternative" } });
    expect(modelSelect).toHaveValue("image-alternative");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(requestMutationMock).toHaveBeenCalledWith(
        "settings.image_generation.update",
        {
          enabled: false,
          model_id: "image-alternative",
          default_aspect_ratio: "1:1",
          default_image_size: "1K",
          max_images_per_turn: 4,
        },
        20_000,
      ),
    );
  });

  it("saves network safety without exposing technical SSRF copy", async () => {
    const payload = settingsPayload();
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
    requestMutationMock.mockResolvedValueOnce({
      ...payload,
      advanced: { ...payload.advanced, webui_allow_local_service_access: false },
      requires_restart: true,
      restart_required_sections: ["runtime"],
    });

    renderSettingsView({ initialSection: "advanced" });

    expect(await screen.findByText("Web safety")).toBeInTheDocument();
    expect(screen.queryByText(/SSRF/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Private Service Protection")).not.toBeInTheDocument();
    expect(screen.getByText("Default access")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restricted" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Default Permission" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Full Access" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("switch", { name: "Local services" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(requestMutationMock).toHaveBeenCalledWith(
        "settings.network_safety.update",
        {
          webui_allow_local_service_access: false,
          webui_default_access_mode: "default",
        },
        20_000,
      ),
    );
  });

  it("saves optional-key web search providers without an API key", async () => {
    const payload = {
      ...settingsPayload(),
      web_search: {
        ...settingsPayload().web_search,
        provider: "duckduckgo",
        providers: [
          { name: "duckduckgo", label: "DuckDuckGo", credential: "none" as const },
          { name: "keenable", label: "Keenable", credential: "optional_api_key" as const },
        ],
      },
    };
    const updatedPayload = {
      ...payload,
      web_search: {
        ...payload.web_search,
        provider: "keenable",
      },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings") return jsonResponse(payload);
      if (url === "/api/settings/cli-apps") return jsonResponse({ apps: [], installed_count: 0 });
      if (url === "/api/settings/mcp-presets") return jsonResponse({ presets: [], installed_count: 0 });
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    requestMutationMock.mockResolvedValueOnce(updatedPayload);

    renderSettingsView({ initialSection: "browser" });

    fireEvent.pointerDown(await screen.findByRole("button", { name: /DuckDuckGo/ }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Keenable" }));
    const saveButton = screen
      .getAllByRole("button", { name: "Save" })
      .find((button) => !(button as HTMLButtonElement).disabled);
    if (!saveButton) throw new Error("enabled Save button was not found");
    fireEvent.click(saveButton);

    await waitFor(() =>
      expect(requestMutationMock).toHaveBeenCalledWith(
        "settings.web_search.update",
        {
          provider: "keenable",
          max_results: 5,
          timeout: 30,
          use_jina_reader: true,
        },
        20_000,
      ),
    );
  });

  it("uses native host safety copy on the native surface", async () => {
    const payload = {
      ...settingsPayload(),
      surface: "native" as const,
      runtime_surface: "native" as const,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/settings") return jsonResponse(payload);
        if (url === "/api/settings/cli-apps") return jsonResponse({ apps: [], installed_count: 0 });
        if (url === "/api/settings/mcp-presets") return jsonResponse({ presets: [], installed_count: 0 });
        return { ok: false, status: 404, json: async () => ({}) } as Response;
      }),
    );

    renderSettingsView({ initialSection: "advanced" });

    expect(await screen.findByText("App safety")).toBeInTheDocument();
    expect(screen.queryByText("Web safety")).not.toBeInTheDocument();
    expect(screen.getByText("Allow Full Access shell commands to reach services on this Mac.")).toBeInTheDocument();
  });

  it("refreshes settings with a fresh token after native engine restart", async () => {
    const payload = {
      ...settingsPayload(),
      surface: "native" as const,
      runtime_surface: "native" as const,
      runtime_capabilities: {
        can_restart_engine: true,
        can_pick_folder: true,
        can_open_logs: true,
        can_export_diagnostics: true,
      },
    };
    const restartedPayload = {
      ...payload,
      advanced: { ...payload.advanced, webui_allow_local_service_access: false },
      requires_restart: true,
      restart_required_sections: ["runtime"],
    };
    const refreshedPayload = {
      ...restartedPayload,
      requires_restart: false,
      restart_required_sections: [],
    };
    const restartEngine = vi.fn(async () => "fresh-token");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const auth = (init?.headers as Record<string, string> | undefined)?.Authorization;
      if (url === "/api/settings" && auth === "Bearer fresh-token") {
        return jsonResponse(refreshedPayload);
      }
      if (url === "/api/settings") return jsonResponse(payload);
      if (url === "/api/settings/cli-apps") return jsonResponse({ apps: [], installed_count: 0 });
      if (url === "/api/settings/mcp-presets") return jsonResponse({ presets: [], installed_count: 0 });
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    requestMutationMock.mockResolvedValueOnce(restartedPayload);

    renderSettingsView({
      initialSection: "advanced",
      onNativeEngineRestart: restartEngine,
    });

    expect(await screen.findByText("App safety")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "Local services" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(restartEngine).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/settings",
        expect.objectContaining({
          headers: { Authorization: "Bearer fresh-token" },
        }),
      ),
    );
  });

  it("selects and saves transcription models by canonical model ID", async () => {
    const base = settingsPayload();
    const transcriptionDefault = {
      ...base.models[0],
      model_id: "speech-default",
      display_name: "Speech default",
      provider: "openai",
      model: "whisper-1",
      capabilities: { ...base.models[0].capabilities, transcription: true },
      is_default: true,
    };
    const transcriptionAlternative = {
      ...transcriptionDefault,
      model_id: "speech-alternative",
      display_name: "Speech alternative",
      provider: "assemblyai",
      model: "universal-3-pro",
      is_default: false,
    };
    const payload: SettingsPayload = {
      ...base,
      models: [base.models[0], transcriptionDefault, transcriptionAlternative],
      providers: [
        { name: "openai", label: "OpenAI", configured: true },
        { name: "assemblyai", label: "AssemblyAI", configured: true },
      ],
      transcription: {
        enabled: true,
        model_id: "speech-default",
        language: null,
        max_duration_sec: 120,
        max_upload_mb: 25,
      },
    };
    const updatedPayload: SettingsPayload = {
      ...payload,
      transcription: { ...payload.transcription!, model_id: "speech-alternative" },
    };
    requestMutationMock.mockResolvedValueOnce(updatedPayload);

    renderSettingsView({ initialSection: "voice", initialSettings: payload });

    const modelSelect = screen.getByRole("combobox", { name: "Model" });
    expect(modelSelect).toHaveValue("speech-default");
    expect(screen.getByRole("option", { name: "Speech default — openai · whisper-1" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Speech alternative — assemblyai · universal-3-pro" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Primary/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "OpenAI" })).not.toBeInTheDocument();
    expect(screen.getByText("whisper-1")).toBeInTheDocument();
    fireEvent.change(modelSelect, { target: { value: "speech-alternative" } });
    expect(modelSelect).toHaveValue("speech-alternative");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(requestMutationMock).toHaveBeenCalledWith(
        "settings.transcription.update",
        {
          enabled: true,
          model_id: "speech-alternative",
          language: "",
          max_duration_sec: 120,
          max_upload_mb: 25,
        },
        20_000,
      ),
    );
  });
});
