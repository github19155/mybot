import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Keyboard, MousePointer2, RefreshCw, Send, Unlock } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  SettingsGroup,
  SettingsRow,
  SettingsSectionTitle,
  StatusPill,
} from "@/components/settings/shared/SettingsControls";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useClient } from "@/providers/ClientProvider";

interface BrowserTakeoverState {
  owner?: "agent" | "human";
  reason?: string | null;
  url?: string | null;
  title?: string | null;
  handoff_token?: string | null;
  width?: number;
  height?: number;
}

function browserHeaders(token: string, extra?: Record<string, string>): HeadersInit {
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(extra ?? {}),
  };
}

async function jsonOrError(response: Response): Promise<BrowserTakeoverState> {
  if (!response.ok) {
    const text = (await response.text()).trim();
    throw new Error(text || `HTTP ${response.status}`);
  }
  return (await response.json()) as BrowserTakeoverState;
}

export function BrowserTakeoverPanel() {
  const { t } = useTranslation();
  const { getToken } = useClient();
  const imageRef = useRef<HTMLImageElement>(null);
  const screenshotUrlRef = useRef<string | null>(null);
  const [state, setState] = useState<BrowserTakeoverState | null>(null);
  const [screenshotUrl, setScreenshotUrl] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const handoffToken = state?.handoff_token ?? "";
  const takeoverActive = state?.owner === "human" && Boolean(handoffToken);
  const takeoverRequested = useMemo(() => {
    if (typeof window === "undefined") return false;
    const hash = window.location.hash;
    return hash.includes("takeover=1") || hash.includes("takeover=true");
  }, []);

  const refreshState = useCallback(async () => {
    try {
      const response = await fetch("/api/webui/browser-takeover/state", {
        cache: "no-store",
        credentials: "same-origin",
        headers: browserHeaders(getToken()),
      });
      const payload = await jsonOrError(response);
      setState(payload);
      setError(null);
      return payload;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return null;
    }
  }, [getToken]);

  const refreshScreenshot = useCallback(async (tokenOverride?: string) => {
    const browserToken = tokenOverride ?? handoffToken;
    if (!browserToken) return;
    try {
      const response = await fetch("/api/webui/browser-takeover/screenshot", {
        cache: "no-store",
        credentials: "same-origin",
        headers: browserHeaders(getToken(), {
          "X-Nanobot-Browser-Token": browserToken,
        }),
      });
      if (!response.ok) throw new Error((await response.text()).trim() || `HTTP ${response.status}`);
      const next = URL.createObjectURL(await response.blob());
      const previous = screenshotUrlRef.current;
      screenshotUrlRef.current = next;
      setScreenshotUrl(next);
      if (previous) URL.revokeObjectURL(previous);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [getToken, handoffToken]);

  const sendAction = useCallback(async (
    action: "click" | "type" | "key" | "scroll" | "release",
    values: Record<string, string> = {},
  ) => {
    if (!handoffToken) return;
    setBusy(true);
    try {
      const headers: Record<string, string> = {
        "X-Nanobot-Browser-Token": handoffToken,
        "X-Nanobot-Browser-Action": action,
      };
      if (values.x) headers["X-Nanobot-Browser-X"] = values.x;
      if (values.y) headers["X-Nanobot-Browser-Y"] = values.y;
      if (values.key) headers["X-Nanobot-Browser-Key"] = values.key;
      if (values.delta) headers["X-Nanobot-Browser-Delta"] = values.delta;
      if (values.text !== undefined) headers["X-Nanobot-Browser-Text"] = JSON.stringify(values.text);
      const response = await fetch("/api/webui/browser-takeover/action", {
        cache: "no-store",
        credentials: "same-origin",
        headers: browserHeaders(getToken(), headers),
      });
      if (!response.ok) throw new Error((await response.text()).trim() || `HTTP ${response.status}`);
      setError(null);
      if (action === "release") {
        setText("");
        await refreshState();
      } else {
        await new Promise((resolve) => window.setTimeout(resolve, 120));
        await refreshScreenshot();
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }, [getToken, handoffToken, refreshScreenshot, refreshState]);

  useEffect(() => {
    void refreshState();
    const timer = window.setInterval(() => void refreshState(), takeoverActive ? 1200 : 3000);
    return () => window.clearInterval(timer);
  }, [refreshState, takeoverActive]);

  useEffect(() => {
    if (!takeoverActive) return;
    void refreshScreenshot(handoffToken);
    const timer = window.setInterval(() => void refreshScreenshot(handoffToken), 900);
    return () => window.clearInterval(timer);
  }, [handoffToken, refreshScreenshot, takeoverActive]);

  useEffect(() => () => {
    if (screenshotUrlRef.current) URL.revokeObjectURL(screenshotUrlRef.current);
  }, []);

  useEffect(() => {
    if (!takeoverRequested || !takeoverActive) return;
    document.getElementById("browser-takeover-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [takeoverActive, takeoverRequested]);

  const handleScreenClick = useCallback((event: React.MouseEvent<HTMLImageElement>) => {
    if (!takeoverActive || busy) return;
    const image = imageRef.current;
    if (!image) return;
    const rect = image.getBoundingClientRect();
    const width = image.naturalWidth || state?.width || 1440;
    const height = image.naturalHeight || state?.height || 960;
    const x = Math.max(0, Math.min(width - 1, Math.round((event.clientX - rect.left) * width / rect.width)));
    const y = Math.max(0, Math.min(height - 1, Math.round((event.clientY - rect.top) * height / rect.height)));
    void sendAction("click", { x: String(x), y: String(y) });
  }, [busy, sendAction, state?.height, state?.width, takeoverActive]);

  return (
    <section id="browser-takeover-panel" className="scroll-mt-5">
      <SettingsSectionTitle>
        {t("settings.browserTakeover.title", { defaultValue: "Browser takeover" })}
      </SettingsSectionTitle>
      <SettingsGroup>
        <SettingsRow
          title={t("settings.browserTakeover.status", { defaultValue: "Control" })}
          description={t("settings.browserTakeover.description", {
            defaultValue: "When the agent pauses for a CAPTCHA, login confirmation, or 2FA, control the same Linux Chromium session here.",
          })}
        >
          <div className="flex items-center gap-2">
            <StatusPill tone={takeoverActive ? "warning" : state?.owner === "agent" ? "success" : "neutral"}>
              {takeoverActive
                ? t("settings.browserTakeover.human", { defaultValue: "Your control" })
                : state?.owner === "agent"
                  ? t("settings.browserTakeover.agent", { defaultValue: "AI control" })
                  : t("settings.browserTakeover.unavailable", { defaultValue: "Unavailable" })}
            </StatusPill>
            <Button type="button" variant="ghost" size="icon" onClick={() => void refreshState()}>
              <RefreshCw className="h-4 w-4" />
            </Button>
          </div>
        </SettingsRow>

        {state?.url ? (
          <SettingsRow title={t("settings.browserTakeover.page", { defaultValue: "Current page" })}>
            <div className="max-w-[36rem] text-right text-[12px] text-muted-foreground">
              <div className="truncate font-medium text-foreground">{state.title || state.url}</div>
              <div className="truncate">{state.url}</div>
              {state.reason ? <div className="mt-1">{state.reason}</div> : null}
            </div>
          </SettingsRow>
        ) : null}

        {takeoverActive ? (
          <div className="border-t border-border/60 p-3 sm:p-4">
            <div className="overflow-hidden rounded-xl border border-border bg-black/90">
              {screenshotUrl ? (
                <img
                  ref={imageRef}
                  src={screenshotUrl}
                  alt={t("settings.browserTakeover.screen", { defaultValue: "Remote browser screen" })}
                  draggable={false}
                  onClick={handleScreenClick}
                  className="block h-auto max-h-[62vh] w-full cursor-crosshair object-contain touch-manipulation select-none"
                />
              ) : (
                <div className="grid aspect-[3/2] place-items-center text-sm text-white/60">
                  {t("settings.browserTakeover.loadingScreen", { defaultValue: "Loading browser screen…" })}
                </div>
              )}
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1 text-[12px] text-muted-foreground">
                <MousePointer2 className="h-3.5 w-3.5" />
                {t("settings.browserTakeover.clickHint", { defaultValue: "Tap the screen to click" })}
              </span>
              <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void sendAction("scroll", { delta: "-4" })}>
                <ArrowUp className="mr-1 h-3.5 w-3.5" />
                {t("settings.browserTakeover.scrollUp", { defaultValue: "Scroll up" })}
              </Button>
              <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void sendAction("scroll", { delta: "4" })}>
                <ArrowDown className="mr-1 h-3.5 w-3.5" />
                {t("settings.browserTakeover.scrollDown", { defaultValue: "Scroll down" })}
              </Button>
            </div>

            <div className="mt-3 flex gap-2">
              <Input
                value={text}
                type="password"
                autoComplete="off"
                placeholder={t("settings.browserTakeover.typePlaceholder", { defaultValue: "Type into the focused browser field" })}
                disabled={busy}
                onChange={(event) => setText(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" || !text) return;
                  event.preventDefault();
                  void sendAction("type", { text }).then(() => setText(""));
                }}
              />
              <Button
                type="button"
                disabled={busy || !text}
                onClick={() => void sendAction("type", { text }).then(() => setText(""))}
              >
                <Send className="mr-1 h-4 w-4" />
                {t("settings.browserTakeover.send", { defaultValue: "Send" })}
              </Button>
            </div>
            <p className="mt-1.5 text-[11px] text-muted-foreground">
              {t("settings.browserTakeover.secretHint", {
                defaultValue: "Text is sent directly to the private browser sidecar and is not added to the AI conversation.",
              })}
            </p>

            <div className="mt-3 flex flex-wrap gap-2">
              <span className="inline-flex items-center gap-1 text-[12px] text-muted-foreground">
                <Keyboard className="h-3.5 w-3.5" />
              </span>
              {[
                ["Return", "Enter"],
                ["Tab", "Tab"],
                ["Escape", "Esc"],
                ["BackSpace", "Backspace"],
              ].map(([key, label]) => (
                <Button key={key} type="button" variant="outline" size="sm" disabled={busy} onClick={() => void sendAction("key", { key })}>
                  {label}
                </Button>
              ))}
              <Button
                type="button"
                variant="default"
                size="sm"
                disabled={busy}
                onClick={() => void sendAction("release")}
                className="ml-auto"
              >
                <Unlock className="mr-1 h-3.5 w-3.5" />
                {t("settings.browserTakeover.release", { defaultValue: "Done — return to AI" })}
              </Button>
            </div>
          </div>
        ) : (
          <div className="border-t border-border/60 p-4 text-[12px] text-muted-foreground">
            {t("settings.browserTakeover.waiting", {
              defaultValue: "No human takeover is active. Keep using WeChat normally; this panel becomes interactive when the bot asks for help.",
            })}
          </div>
        )}

        {error ? (
          <div className="border-t border-border/60 px-4 py-3 text-[12px] text-destructive">{error}</div>
        ) : null}
      </SettingsGroup>
    </section>
  );
}
