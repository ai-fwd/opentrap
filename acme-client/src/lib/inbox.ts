import type { EmailDetail, InboxItem } from "../shared/types";
import { extractTextFromHtml } from "./html";

interface InboxRequestContext {
  upstreamBaseUrl: string;
}

export async function loadInboxItems(context: InboxRequestContext): Promise<InboxItem[]> {
  const inboxUrl = buildUpstreamUrl(context.upstreamBaseUrl, "/_upstream/inbox");
  const response = await fetch(inboxUrl, {
    method: "GET",
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    throw new Error(await buildTransportError("inbox list", response));
  }

  const payload = (await response.json()) as unknown;
  if (!Array.isArray(payload)) {
    throw new Error("Upstream inbox response is not a JSON array");
  }

  return payload as InboxItem[];
}

export async function loadRawEmailBody(emailId: string, context: InboxRequestContext): Promise<string> {
  const emailUrl = buildUpstreamUrl(context.upstreamBaseUrl, `/_upstream/emails/${encodeURIComponent(emailId)}`);
  const response = await fetch(emailUrl, {
    method: "GET",
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    throw new Error(await buildTransportError(`email ${emailId}`, response));
  }

  const payload = (await response.json()) as { rawHtml?: unknown };
  if (typeof payload.rawHtml !== "string") {
    throw new Error("Upstream email response missing rawHtml");
  }

  return payload.rawHtml;
}

export async function loadEmailDetail(emailId: string, context: InboxRequestContext): Promise<EmailDetail> {
  const inboxItems = await loadInboxItems(context);
  const item = inboxItems.find((candidate) => candidate.id === emailId);

  if (!item) {
    throw new Error(`Unknown email id: ${emailId}`);
  }

  const rawHtml = await loadRawEmailBody(emailId, context);
  return buildEmailDetailFromInboxItem(item, rawHtml);
}

export function buildEmailDetailFromInboxItem(item: InboxItem, rawHtml: string): EmailDetail {
  const extractedText = extractTextFromHtml(rawHtml);
  return {
    ...item,
    rawHtml,
    extractedText,
  };
}

async function buildTransportError(resource: string, response: Response): Promise<string> {
  const requestId = response.headers.get("x-request-id");
  const rawBody = (await response.text()).trim();
  let upstreamError = rawBody.length > 0 ? rawBody : "Unknown upstream error";

  try {
    const parsed = JSON.parse(rawBody) as { error?: unknown };
    if (typeof parsed.error === "string" && parsed.error.trim().length > 0) {
      upstreamError = parsed.error;
    }
  } catch {
    // Keep raw body text when upstream body is not JSON.
  }

  const requestIdPart = requestId ? ` request_id=${requestId}` : "";
  return `Failed to fetch ${resource} from upstream (${response.status}). ${upstreamError}${requestIdPart}`;
}

function buildUpstreamUrl(baseUrl: string, path: string): string {
  const normalizedBase = baseUrl.replace(/\/+$/, "");
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${normalizedBase}${normalizedPath}`;
}
