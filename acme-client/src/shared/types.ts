export type AssistTaskType =
  | "summarize_email"
  | "summarize_inbox"
  | "extract_actions"
  | "draft_reply";

export interface InboxItem {
  id: string;
  sender: string;
  subject: string;
  timestamp: string;
  preview: string;
  htmlPath: string;
  threadId?: string;
  fromAddress?: string;
  replyTo?: string;
  ccAddresses?: string;
  date?: string;
  bodyPlain?: string;
}

export interface EmailDetail extends InboxItem {
  rawHtml: string;
  extractedText: string;
}

export interface AssistRequest {
  taskType: AssistTaskType;
  userTask: string;
  emailId?: string;
  selectedEmailIds?: string[];
}

export interface AssistResponse {
  output: string;
  model: string;
  taskType: AssistTaskType;
  usedEmailIds: string[];
}

export interface PromptEmailContext {
  id: string;
  sender: string;
  subject: string;
  timestamp: string;
  threadId?: string;
  fromAddress?: string;
  replyTo?: string;
  ccAddresses?: string;
  date?: string;
  bodyPlain?: string;
  rawHtml: string;
}
