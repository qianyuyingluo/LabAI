"use client";

import {
  ComponentPropsWithoutRef,
  isValidElement,
  memo,
  ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Check, Copy } from "lucide-react";
import rehypeKatex from "rehype-katex";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { Streamdown } from "streamdown";
import type { Locale } from "@/lib/i18n";

type StreamingMarkdownProps = {
  content: string;
  emptyLabel?: string;
  isStreaming?: boolean;
  locale?: Locale;
};

type PreProps = ComponentPropsWithoutRef<"pre"> & {
  node?: unknown;
};

function extractText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }
  if (Array.isArray(node)) {
    return node.map(extractText).join("");
  }
  if (isValidElement<{ children?: ReactNode }>(node)) {
    return extractText(node.props.children);
  }
  return "";
}

async function copyText(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.left = "-9999px";
  textarea.style.position = "fixed";
  textarea.style.top = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

function CopyablePre({
  children,
  locale = "zh",
  ...props
}: PreProps & { locale?: Locale }) {
  const [copied, setCopied] = useState(false);
  const timeoutRef = useRef<number | undefined>(undefined);
  const codeText = useMemo(() => extractText(children).replace(/\n$/, ""), [children]);
  const preProps = { ...props };
  delete preProps.node;
  const labels =
    locale === "zh"
      ? { copied: "已复制", copy: "复制代码" }
      : { copied: "Copied", copy: "Copy code" };

  useEffect(
    () => () => {
      if (timeoutRef.current) window.clearTimeout(timeoutRef.current);
    },
    [],
  );

  const handleCopy = async () => {
    if (!codeText) return;
    await copyText(codeText);
    setCopied(true);
    if (timeoutRef.current) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = window.setTimeout(() => setCopied(false), 1200);
  };

  return (
    <div className="copyable-code-block group">
      <button
        aria-label={copied ? labels.copied : labels.copy}
        className="absolute right-2 top-2 z-10 flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-muted opacity-0 shadow-sm transition hover:bg-panel hover:text-ink focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent group-hover:opacity-100"
        disabled={!codeText}
        onClick={handleCopy}
        title={copied ? labels.copied : labels.copy}
        type="button"
      >
        {copied ? <Check className="h-4 w-4 text-accent" /> : <Copy className="h-4 w-4" />}
      </button>
      <pre {...preProps}>{children}</pre>
    </div>
  );
}

const MemoizedMarkdown = memo(
  function MemoizedMarkdown({
    content,
    locale = "zh",
  }: {
    content: string;
    locale?: Locale;
  }) {
    const components = useMemo(
      () => ({
        pre: (props: PreProps) => <CopyablePre {...props} locale={locale} />,
      }),
      [locale],
    );
    const rendered = useMemo(
      () => (
        <div className="markdown-content">
          <ReactMarkdown
            components={components}
            rehypePlugins={[rehypeKatex]}
            remarkPlugins={[remarkGfm, remarkMath]}
          >
            {content}
          </ReactMarkdown>
        </div>
      ),
      [components, content],
    );

    return rendered;
  },
  (prev, next) => prev.content === next.content && prev.locale === next.locale,
);

export const StreamingMarkdown = memo(function StreamingMarkdown({
  content,
  emptyLabel = "Preparing analysis...",
  isStreaming = false,
  locale = "zh",
}: StreamingMarkdownProps) {
  const components = useMemo(
    () => ({
      pre: (props: PreProps) => <CopyablePre {...props} locale={locale} />,
    }),
    [locale],
  );

  if (!content) {
    return <span className="text-muted">{emptyLabel}</span>;
  }

  if (isStreaming) {
    return (
      <Streamdown
        className="markdown-content"
        components={components}
        controls={{
          code: false,
          table: false,
          mermaid: false,
        }}
        isAnimating
        mode="streaming"
        parseIncompleteMarkdown
      >
        {content}
      </Streamdown>
    );
  }

  return <MemoizedMarkdown content={content} locale={locale} />;
});
