"use client";

import { RefObject, useCallback, useEffect, useRef, useState } from "react";

export function useStickToBottom(
  scrollRef: RefObject<HTMLElement | null>,
  bottomRef: RefObject<HTMLElement | null>,
) {
  const [isAtBottom, setIsAtBottom] = useState(true);
  const shouldStickRef = useRef(true);
  const frameRef = useRef<number | null>(null);

  useEffect(() => {
    const scrollEl = scrollRef.current;
    const bottomEl = bottomRef.current;

    if (!scrollEl || !bottomEl) return;

    const updateStickState = () => {
      const distance =
        scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight;
      const nearBottom = distance < 120;
      shouldStickRef.current = nearBottom;
      setIsAtBottom(nearBottom);
    };

    const observer = new IntersectionObserver(
      ([entry]) => {
        setIsAtBottom(entry.isIntersecting || shouldStickRef.current);
      },
      {
        root: scrollEl,
        threshold: 0.01,
      },
    );

    updateStickState();
    scrollEl.addEventListener("scroll", updateStickState, { passive: true });
    observer.observe(bottomEl);

    return () => {
      scrollEl.removeEventListener("scroll", updateStickState);
      observer.disconnect();
    };
  }, [bottomRef, scrollRef]);

  const scrollToBottom = useCallback(
    (force = false) => {
      const scrollEl = scrollRef.current;
      if (!scrollEl) return;
      if (!force && !shouldStickRef.current) return;
      if (frameRef.current !== null) return;

      frameRef.current = window.requestAnimationFrame(() => {
        frameRef.current = null;
        scrollEl.scrollTo({
          top: scrollEl.scrollHeight,
          behavior: "auto",
        });
      });
    },
    [scrollRef],
  );

  useEffect(() => {
    return () => {
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
      }
    };
  }, []);

  return {
    isAtBottom,
    scrollToBottom,
    shouldStickRef,
  };
}
