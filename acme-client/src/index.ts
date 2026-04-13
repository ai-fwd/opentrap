export type {
  AssistRequest,
  AssistResponse,
  AssistTaskType,
  EmailDetail,
  InboxItem,
  PromptEmailContext,
} from "./shared/types";

export { extractTextFromHtml } from "./lib/html";
export { loadInboxItems, loadRawEmailBody, loadEmailDetail } from "./lib/inbox";
export { EMAIL_ASSISTANT_SYSTEM_PROMPT, buildAssistantPrompt } from "./lib/prompt";
export { runAssistantTask } from "./lib/llm";
