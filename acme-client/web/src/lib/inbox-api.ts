export type AssistTaskType = "summarize_email" | "summarize_inbox" | "extract_actions" | "draft_reply";

export interface InboxItem {
  id: string;
  sender: string;
  subject: string;
  timestamp: string;
  preview: string;
  htmlPath: string;
  fromAddress?: string;
}

export interface EmailDetail extends InboxItem {
  rawHtml: string;
  extractedText: string;
  bodyPlain?: string;
  date?: string;
}

export interface AssistResponse {
  output: string;
  model: string;
  taskType: AssistTaskType;
  usedEmailIds: string[];
}

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export async function fetchInbox(): Promise<InboxItem[]> {
  const response = await fetch(`${API_BASE}/api/inbox`);
  return parseJson<InboxItem[]>(response);
}

export async function fetchEmailDetail(emailId: string): Promise<EmailDetail> {
  const response = await fetch(`${API_BASE}/api/inbox/${encodeURIComponent(emailId)}`);
  return parseJson<EmailDetail>(response);
}

export async function runAssist(request: {
  taskType: AssistTaskType;
  userTask: string;
  emailId?: string;
}): Promise<AssistResponse> {
  const response = await fetch(`${API_BASE}/api/assist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });

  return parseJson<AssistResponse>(response);
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(`Request failed (${response.status}): ${await response.text()}`);
  }

  return (await response.json()) as T;
}
