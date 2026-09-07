import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { usePageVisibility } from "@/hooks/usePageVisibility";
import { useSubagentFleet } from "@/hooks/useSubagentFleet";
import type { SubagentSummary, SubagentUsage } from "@/lib/types";
import { cn } from "@/lib/utils";

function phaseBadgeClass(phase: string): string {
  if (phase === "queued") {
    return "bg-muted text-muted-foreground";
  }
  if (phase === "done") {
    return "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
  }
  if (phase === "error") {
    return "bg-destructive/10 text-destructive";
  }
  // Initializing / awaiting_tools / tools_completed / final_response.
  return "bg-sky-500/10 text-sky-700 dark:text-sky-300";
}

function stateBadgeClass(state: string): string {
  if (state === "queued") return "bg-muted text-muted-foreground";
  if (state === "failed") return "bg-destructive/10 text-destructive";
  if (state === "stopped") return "bg-amber-500/10 text-amber-700 dark:text-amber-300";
  if (state === "completed") return "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
  return "bg-sky-500/10 text-sky-700 dark:text-sky-300";
}

function formatElapsed(startedAtMs: number, now: number): string {
  const totalSeconds = Math.max(0, Math.round((now - startedAtMs) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

function formatTokens(value: number, locale: string): string {
  return new Intl.NumberFormat(locale, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

function formatUsage(usage: SubagentUsage, locale: string): string {
  const inTokens = formatTokens(usage.input_tokens, locale);
  const outTokens = formatTokens(usage.output_tokens, locale);
  return `${inTokens} in · ${outTokens} out`;
}

function PhaseBadge({ phase, compact }: { phase: string; compact?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[10.5px] font-medium",
        compact ? "bg-muted text-muted-foreground" : phaseBadgeClass(phase),
      )}
      data-testid="fleet-phase"
    >
      {phase}
    </span>
  );
}

function StateBadge({ state }: { state: string }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[10.5px] font-medium",
        stateBadgeClass(state),
      )}
      data-testid="fleet-state"
    >
      {state}
    </span>
  );
}

function SummaryCard({
  subagent,
  now,
  showFinished,
}: {
  subagent: SubagentSummary;
  now: number;
  showFinished: boolean;
}) {
  const { t, i18n } = useTranslation();
  const latestToolEvent = subagent.tool_events?.[subagent.tool_events.length - 1];

  return (
    <div
      className="flex min-w-0 flex-col gap-1 px-3 py-2"
      data-testid={showFinished ? "fleet-finished-card" : "fleet-card"}
    >
      <div className="flex min-w-0 items-center gap-1.5">
        <span className="min-w-0 truncate text-[12.5px] font-medium text-sidebar-foreground">
          {subagent.label}
        </span>
        <StateBadge state={subagent.state} />
        <PhaseBadge phase={subagent.phase} compact={showFinished} />
        {subagent.iteration ? (
          <span className="shrink-0 text-[11px] text-muted-foreground">
            {t("fleet.iteration", { defaultValue: "iter {{n}}", n: subagent.iteration })}
          </span>
        ) : null}
      </div>
      <div className="flex min-w-0 items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground">
        {subagent.role ? <span className="shrink-0">{subagent.role}</span> : null}
        {subagent.model ? <span className="min-w-0 truncate" title={subagent.model}>{subagent.model}</span> : null}
        {subagent.thinking ? <span className="shrink-0">think:{subagent.thinking}</span> : null}
        {subagent.started_at_ms ? (
          <span className="shrink-0">
            {t("fleet.elapsed", { defaultValue: "{{time}}", time: formatElapsed(subagent.started_at_ms, now) })}
          </span>
        ) : null}
        {latestToolEvent?.name ? (
          <span className="min-w-0 truncate" title={latestToolEvent.summary}>
            {latestToolEvent.name}
            {latestToolEvent.summary ? ` · ${latestToolEvent.summary}` : ""}
          </span>
        ) : null}
        {subagent.usage ? (
          <span className="shrink-0">{formatUsage(subagent.usage, i18n.language)}</span>
        ) : null}
        {subagent.origin?.channel ? (
          <span className="shrink-0 rounded-full bg-sidebar-accent px-1.5 py-px text-[10px] font-medium text-sidebar-foreground/80">
            {subagent.origin.channel}
          </span>
        ) : null}
      </div>
    </div>
  );
}

export function FleetView({ token }: { token: string }) {
  const { t } = useTranslation();
  const pageVisible = usePageVisibility();
  const payload = useSubagentFleet(token);
  const [now, setNow] = useState(() => Date.now());
  const [finishedOpen, setFinishedOpen] = useState(false);

  useEffect(() => {
    if (!pageVisible) return undefined;
    const interval = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, [pageVisible]);

  if (!payload) return null;

  const running = payload.subagents.filter((subagent) => ["queued", "running"].includes(subagent.state));
  const finished = payload.subagents.filter((subagent) => !["queued", "running"].includes(subagent.state));

  return (
    <section
      aria-label={t("fleet.title", { defaultValue: "Subagents" })}
      data-testid="fleet-view"
      className="flex max-h-[45vh] min-h-0 flex-col overflow-y-auto border-t border-sidebar-border/60 bg-sidebar/40"
    >
      <div className="flex items-center gap-1.5 px-3 pb-1 pt-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        <span>{t("fleet.title", { defaultValue: "Subagents" })}</span>
        {running.length > 0 ? (
          <span className="rounded-full bg-sky-500/10 px-1.5 text-[10px] font-medium text-sky-600 dark:text-sky-300">
            {running.length}
          </span>
        ) : null}
      </div>

      {running.length === 0 && finished.length === 0 ? (
        <div
          data-testid="fleet-empty"
          className="px-3 pb-2 text-[11.5px] text-muted-foreground"
        >
          {t("fleet.empty", { defaultValue: "No subagents running" })}
        </div>
      ) : null}

      {running.map((subagent) => (
        <SummaryCard key={subagent.task_id} subagent={subagent} now={now} showFinished={false} />
      ))}

      {finished.length > 0 ? (
        <>
          <button
            type="button"
            data-testid="fleet-finished-toggle"
            onClick={() => setFinishedOpen((open) => !open)}
            className="flex w-full items-center gap-1 px-3 py-1.5 text-left text-[11px] font-medium text-muted-foreground hover:text-sidebar-foreground"
          >
            {finishedOpen ? (
              <ChevronDown className="h-3 w-3" aria-hidden />
            ) : (
              <ChevronRight className="h-3 w-3" aria-hidden />
            )}
            <span>
              {t("fleet.finished", { defaultValue: "Finished {{count}}", count: finished.length })}
            </span>
          </button>
          {finishedOpen
            ? finished.map((subagent) => (
                <SummaryCard key={subagent.task_id} subagent={subagent} now={now} showFinished />
              ))
            : null}
        </>
      ) : null}
    </section>
  );
}
