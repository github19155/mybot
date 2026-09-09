import { describe, expect, it } from "vitest";

import { mapBrowserPointer } from "@/lib/browser-takeover";

describe("mapBrowserPointer", () => {
  it("maps a fitted 16:9 image back to the full-HD framebuffer", () => {
    expect(mapBrowserPointer({
      clientX: 480,
      clientY: 270,
      elementLeft: 0,
      elementTop: 0,
      elementWidth: 960,
      elementHeight: 540,
      sourceWidth: 1920,
      sourceHeight: 1080,
    })).toEqual({ x: 960, y: 540 });
  });

  it("accounts for vertical letterboxing from object-contain", () => {
    expect(mapBrowserPointer({
      clientX: 500,
      clientY: 375,
      elementLeft: 0,
      elementTop: 0,
      elementWidth: 1000,
      elementHeight: 750,
      sourceWidth: 1920,
      sourceHeight: 1080,
    })).toEqual({ x: 960, y: 540 });
  });

  it("ignores clicks that land in object-contain letterboxing", () => {
    expect(mapBrowserPointer({
      clientX: 500,
      clientY: 20,
      elementLeft: 0,
      elementTop: 0,
      elementWidth: 1000,
      elementHeight: 750,
      sourceWidth: 1920,
      sourceHeight: 1080,
    })).toBeNull();
  });

  it("maps native 1:1 coordinates exactly", () => {
    expect(mapBrowserPointer({
      clientX: 1234,
      clientY: 567,
      elementLeft: 0,
      elementTop: 0,
      elementWidth: 1920,
      elementHeight: 1080,
      sourceWidth: 1920,
      sourceHeight: 1080,
    })).toEqual({ x: 1234, y: 567 });
  });
});
