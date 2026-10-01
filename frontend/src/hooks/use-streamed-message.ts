"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createStreamBuffer, type StreamBuffer } from "@/lib/stream-buffer";

export function useStreamedMessage() {
  const [content, setContent] = useState("");
  const contentRef = useRef("");
  const bufferRef = useRef<StreamBuffer | null>(null);

  useEffect(() => {
    bufferRef.current = createStreamBuffer((chunk) => {
      contentRef.current += chunk;
      setContent(contentRef.current);
    });

    return () => {
      bufferRef.current?.cancel();
      bufferRef.current = null;
    };
  }, []);

  const appendToken = useCallback((token: string) => {
    bufferRef.current?.append(token);
  }, []);

  const reset = useCallback(() => {
    bufferRef.current?.reset();
    contentRef.current = "";
    setContent("");
  }, []);

  const finalFlush = useCallback(() => {
    bufferRef.current?.flush();
    return contentRef.current;
  }, []);

  return {
    appendToken,
    content,
    contentRef,
    finalFlush,
    reset,
  };
}
