import { describe, expect, it } from "vitest";

import { webSearchProviderAcceptsBaseUrl } from "@/components/settings/capabilities/WebSettings";

describe("web search provider settings", () => {
  it("allows an optional custom base URL for Tavily", () => {
    expect(
      webSearchProviderAcceptsBaseUrl({
        name: "tavily",
        label: "Tavily",
        credential: "api_key",
      }),
    ).toBe(true);
  });

  it("keeps base URL support for base-url providers only otherwise", () => {
    expect(
      webSearchProviderAcceptsBaseUrl({
        name: "searxng",
        label: "SearXNG",
        credential: "base_url",
      }),
    ).toBe(true);
    expect(
      webSearchProviderAcceptsBaseUrl({
        name: "brave",
        label: "Brave Search",
        credential: "api_key",
      }),
    ).toBe(false);
  });
});
