import type { ChatMessage } from "@/lib/types";

export function estimateMessageHeight(message?: ChatMessage) {
  if (!message) return 128;

  const base = message.role === "user" ? 80 : 108;
  const divisor = message.role === "user" ? 80 : 90;
  const contentLines = Math.ceil(message.content.length / divisor) * 24;
  const codeBonus = (message.content.match(/```/g)?.length ?? 0) * 90;
  const tableBonus = message.content.includes("|") ? 72 : 0;
  const attachmentBonus = message.attachments?.length ? 46 : 0;
  const sandboxBonus = message.pythonSandbox
    ? 54 + Math.ceil((message.pythonSandbox.error?.length ?? 0) / 90) * 20
    : 0;
  const generatedFiles = message.generatedFiles ?? [];
  const generatedFileCount = generatedFiles.length;
  const generatedImageCount = generatedFiles.filter(
    (file) =>
      file.status === "ready" &&
      file.fileType.toLowerCase() === "image" &&
      file.mimeType.toLowerCase().startsWith("image/"),
  ).length;
  const generatedFilesBonus = generatedFileCount
    ? 52 + generatedImageCount * 218 + (generatedFileCount - generatedImageCount) * 58
    : 0;

  return Math.max(
    88,
    base +
      contentLines +
      codeBonus +
      tableBonus +
      attachmentBonus +
      sandboxBonus +
      generatedFilesBonus,
  );
}
