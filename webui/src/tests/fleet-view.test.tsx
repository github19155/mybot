import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FleetView } from "@/components/FleetView";
import { setAppLanguage } from "@/i18n";
import { fetchSubagents } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchSubagents: vi.fn(),
  };
});

function runningSubagent() {
  return {
    task_id: "task-1",
    label: "Refactor auth",
    state: "running" as const,
    phase: "tools_completed",
    iteration: 2,
    started_at_ms: Date.now() - 30_000,
    origin: { channel: "weixin" },
    usage: { input_tokens: 1_234, output_tokens: 340, total_tokens: 1_574 },
    tool_events: [{ name: "read_file", summary: "src/a.py" }],
  };
}

describe("FleetView", () => {
  beforeEach(async () => {
    await setAppLanguage("en");
    vi.mocked(fetchSubagents).mockReset();
  });

  it("shows the empty state when no subagents exist", async () => {
    vi.mocked(fetchSubagents).mockResolvedValue({ subagents: [] });

    render(<FleetView token="tok" />);

    expect(await screen.findByTestId("fleet-empty")).toBeInTheDocument();
    expect(screen.getByTestId("fleet-view")).toBeInTheDocument();
  });

  it("renders a running card with phase, iteration, elapsed, tool event, usage and origin", async () => {
    vi.mocked(fetchSubagents).mockResolvedValue({ subagents: [runningSubagent()] });

    render(<FleetView token="tok" />);

    expect(await screen.findByText("Refactor auth")).toBeInTheDocument();
    expect(screen.getByTestId("fleet-card")).toBeInTheDocument();
    expect(screen.getByText("tools_completed")).toBeInTheDocument();
    expect(screen.getByText("iter 2")).toBeInTheDocument();
    expect(screen.getByText("30s")).toBeInTheDocument();
    expect(screen.getByText("read_file · src/a.py")).toBeInTheDocument();
    expect(screen.getByText("1.2K in · 340 out")).toBeInTheDocument();
    expect(screen.getByText("weixin")).toBeInTheDocument();
  });

  it("polls every 2 seconds and picks up newly spawned subagents", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(fetchSubagents)
        .mockResolvedValueOnce({ subagents: [] })
        .mockResolvedValueOnce({ subagents: [runningSubagent()] });

      render(<FleetView token="tok" />);

      await act(async () => {
        await Promise.resolve();
      });
      expect(screen.getByTestId("fleet-empty")).toBeInTheDocument();

      await act(async () => {
        vi.advanceTimersByTime(2000);
        await Promise.resolve();
      });

      expect(screen.getByText("Refactor auth")).toBeInTheDocument();
      expect(fetchSubagents).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("collapses finished subagents by default and expands on toggle", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchSubagents).mockResolvedValue({
      subagents: [{
        task_id: "task-2",
        label: "Summarize output",
        state: "finished" as const,
        phase: "done",
        started_at_ms: Date.now() - 120_000,
        ended_at_ms: Date.now() - 60_000,
        error: null,
        usage: { input_tokens: 100, output_tokens: 10, total_tokens: 110 },
      }],
    });

    render(<FleetView token="tok" />);

    const toggle = await screen.findByTestId("fleet-finished-toggle");
    expect(toggle).toHaveTextContent("Finished 1");
    expect(screen.queryByTestId("fleet-finished-card")).not.toBeInTheDocument();

    await user.click(toggle);

    expect(screen.getByTestId("fleet-finished-card")).toBeInTheDocument();
    expect(screen.getByText("Summarize output")).toBeInTheDocument();
  });

  it("keeps rendering while running and finished subagents coexist", async () => {
    vi.mocked(fetchSubagents).mockResolvedValue({
      subagents: [runningSubagent(), {
        task_id: "task-3",
        label: "Old task",
        state: "finished" as const,
        phase: "error",
        started_at_ms: Date.now() - 300_000,
        ended_at_ms: Date.now() - 240_000,
        error: "boom",
        usage: null,
      }],
    });

    render(<FleetView token="tok" />);

    expect(await screen.findByTestId("fleet-card")).toBeInTheDocument();
    expect(screen.getByTestId("fleet-finished-toggle")).toBeInTheDocument();
    expect(screen.queryByTestId("fleet-finished-card")).not.toBeInTheDocument();
  });
});