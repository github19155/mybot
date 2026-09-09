export interface BrowserPointerMappingInput {
  clientX: number;
  clientY: number;
  elementLeft: number;
  elementTop: number;
  elementWidth: number;
  elementHeight: number;
  sourceWidth: number;
  sourceHeight: number;
}

export interface BrowserPointerCoordinates {
  x: number;
  y: number;
}

/**
 * Map a pointer inside an object-contain image element back to the remote
 * browser framebuffer. Returns null when the pointer lands in letterboxing.
 */
export function mapBrowserPointer({
  clientX,
  clientY,
  elementLeft,
  elementTop,
  elementWidth,
  elementHeight,
  sourceWidth,
  sourceHeight,
}: BrowserPointerMappingInput): BrowserPointerCoordinates | null {
  if (
    elementWidth <= 0 ||
    elementHeight <= 0 ||
    sourceWidth <= 0 ||
    sourceHeight <= 0
  ) {
    return null;
  }

  const scale = Math.min(elementWidth / sourceWidth, elementHeight / sourceHeight);
  if (!Number.isFinite(scale) || scale <= 0) return null;

  const renderedWidth = sourceWidth * scale;
  const renderedHeight = sourceHeight * scale;
  const offsetX = (elementWidth - renderedWidth) / 2;
  const offsetY = (elementHeight - renderedHeight) / 2;
  const localX = clientX - elementLeft - offsetX;
  const localY = clientY - elementTop - offsetY;

  if (localX < 0 || localY < 0 || localX >= renderedWidth || localY >= renderedHeight) {
    return null;
  }

  return {
    x: Math.max(0, Math.min(sourceWidth - 1, Math.floor(localX / scale))),
    y: Math.max(0, Math.min(sourceHeight - 1, Math.floor(localY / scale))),
  };
}
