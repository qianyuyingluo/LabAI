export type StreamBuffer = {
  append: (chunk: string) => void;
  flush: () => void;
  reset: () => void;
  cancel: () => void;
};

type StreamBufferOptions = {
  minDelayMs?: number;
};

export function createStreamBuffer(
  onFlush: (chunk: string) => void,
  options: StreamBufferOptions = {},
): StreamBuffer {
  const minDelayMs = options.minDelayMs ?? 34;
  let buffer = "";
  let frameId: number | null = null;
  let timeoutId: number | null = null;
  let lastFlush = 0;

  const clearScheduled = () => {
    if (frameId !== null) {
      window.cancelAnimationFrame(frameId);
      frameId = null;
    }

    if (timeoutId !== null) {
      window.clearTimeout(timeoutId);
      timeoutId = null;
    }
  };

  const flush = () => {
    clearScheduled();

    if (!buffer) return;

    const chunk = buffer;
    buffer = "";
    lastFlush = performance.now();
    onFlush(chunk);
  };

  const requestFlush = () => {
    if (frameId !== null || timeoutId !== null) return;

    const now = performance.now();
    const wait = Math.max(0, minDelayMs - (now - lastFlush));

    const scheduleFrame = () => {
      frameId = window.requestAnimationFrame(() => {
        frameId = null;
        flush();
      });
    };

    if (wait > 0) {
      timeoutId = window.setTimeout(() => {
        timeoutId = null;
        scheduleFrame();
      }, wait);
      return;
    }

    scheduleFrame();
  };

  return {
    append(chunk) {
      buffer += chunk;
      requestFlush();
    },
    flush,
    reset() {
      buffer = "";
      clearScheduled();
    },
    cancel() {
      buffer = "";
      clearScheduled();
    },
  };
}
