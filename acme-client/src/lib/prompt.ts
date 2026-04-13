import type { AssistTaskType, PromptEmailContext } from "../shared/types";

export const EMAIL_ASSISTANT_SYSTEM_PROMPT = [
  "You are Acme Inbox Assistant for a startup founder/operator.",
  "You summarize, prioritize, and draft short professional replies.",
  "Be concise, practical, and faithful to the provided email content.",
  "If something is uncertain, say what is missing instead of inventing details.",
].join(" ");

interface BuildPromptInput {
  taskType: AssistTaskType;
  userTask: string;
  emails: PromptEmailContext[];
}

export function buildAssistantPrompt(input: BuildPromptInput): string {
  const taskLine = input.userTask.trim().length > 0 ? input.userTask.trim() : defaultTaskForType(input.taskType);

  const emailBlocks = input.emails
    .map((email, index) => {
      const threadLine = email.threadId ? `Thread: ${email.threadId}` : "Thread: none";
      const fromAddressLine = email.fromAddress ? `from_address: ${email.fromAddress}` : undefined;
      const replyToLine = email.replyTo ? `reply_to: ${email.replyTo}` : undefined;
      const ccLine = email.ccAddresses ? `cc_addresses: ${email.ccAddresses}` : undefined;
      const dateLine = email.date ? `date: ${email.date}` : undefined;
      const bodyPlainLine = email.bodyPlain ? `body_plain: ${email.bodyPlain}` : undefined;

      return [
        `Email ${index + 1}`,
        `ID: ${email.id}`,
        `Sender: ${email.sender}`,
        `Subject: ${email.subject}`,
        `Timestamp: ${email.timestamp}`,
        threadLine,
        fromAddressLine,
        replyToLine,
        ccLine,
        dateLine,
        bodyPlainLine,
        "Raw HTML body (preserve context):",
        "```html",
        email.rawHtml,
        "```",
      ]
        .filter((line): line is string => typeof line === "string" && line.length > 0)
        .join("\n");
    })
    .join("\n\n");

  return [
    `Task type: ${input.taskType}`,
    `User task: ${taskLine}`,
    "",
    "Email context:",
    emailBlocks,
    "",
    "Return a short response in plain text.",
  ].join("\n");
}

function defaultTaskForType(taskType: AssistTaskType): string {
  switch (taskType) {
    case "summarize_email":
      return "Summarize this email in 3 bullets.";
    case "summarize_inbox":
      return "What matters in my inbox today?";
    case "extract_actions":
      return "What do I need to do from this message?";
    case "draft_reply":
      return "Draft a short professional reply.";
    default:
      return "Help with this inbox.";
  }
}
