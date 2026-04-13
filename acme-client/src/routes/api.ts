import { buildAssistantPrompt } from "../lib/prompt";
import { loadEmailDetail, loadInboxItems } from "../lib/inbox";
import { runAssistantTask } from "../lib/llm";
import type { AssistRequest, AssistResponse, AssistTaskType, PromptEmailContext } from "../shared/types";

const JSON_HEADERS = {
  "Content-Type": "application/json",
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "Content-Type",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
};

const SINGLE_EMAIL_TASKS: AssistTaskType[] = ["summarize_email", "extract_actions", "draft_reply"];

export async function handleApiRequest(request: Request, pathname: string): Promise<Response | undefined> {
  const upstreamBaseUrl = resolveUpstreamBaseUrl(request);
  const inboxContext = { upstreamBaseUrl };

  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: JSON_HEADERS });
  }

  if (request.method === "GET" && pathname === "/api/inbox") {
    const inboxItems = await loadInboxItems(inboxContext);
    return json(inboxItems);
  }

  if (request.method === "GET" && pathname.startsWith("/api/inbox/")) {
    const emailId = decodeURIComponent(pathname.replace("/api/inbox/", ""));

    if (!emailId) {
      return jsonError(400, "Email id is required");
    }

    try {
      const detail = await loadEmailDetail(emailId, inboxContext);
      return json(detail);
    } catch (error) {
      return jsonError(404, toErrorMessage(error));
    }
  }

  if (request.method === "POST" && pathname === "/api/assist") {
    const body = (await request.json()) as AssistRequest;

    if (!isValidTaskType(body.taskType)) {
      return jsonError(400, "Invalid taskType");
    }

    const inboxItems = await loadInboxItems(inboxContext);
    const inboxById = new Map(inboxItems.map((item) => [item.id, item]));

    const usedEmailIds = resolveUsedEmailIds(body, inboxItems.map((item) => item.id));
    if (usedEmailIds.length === 0) {
      return jsonError(400, "No emails selected for this task");
    }

    for (const id of usedEmailIds) {
      if (!inboxById.has(id)) {
        return jsonError(400, `Unknown email id: ${id}`);
      }
    }

    const emails: PromptEmailContext[] = [];
    for (const emailId of usedEmailIds) {
      const detail = await loadEmailDetail(emailId, inboxContext);
      emails.push({
        id: detail.id,
        sender: detail.sender,
        subject: detail.subject,
        timestamp: detail.timestamp,
        ...(detail.threadId ? { threadId: detail.threadId } : {}),
        ...(detail.fromAddress ? { fromAddress: detail.fromAddress } : {}),
        ...(detail.replyTo ? { replyTo: detail.replyTo } : {}),
        ...(detail.ccAddresses ? { ccAddresses: detail.ccAddresses } : {}),
        ...(detail.date ? { date: detail.date } : {}),
        ...(detail.bodyPlain ? { bodyPlain: detail.bodyPlain } : {}),
        rawHtml: detail.rawHtml,
      });
    }

    const prompt = buildAssistantPrompt({
      taskType: body.taskType,
      userTask: body.userTask,
      emails,
    });

    try {
      const result = await runAssistantTask({ prompt });
      const payload: AssistResponse = {
        output: result.output,
        model: result.model,
        taskType: body.taskType,
        usedEmailIds,
      };

      return json(payload);
    } catch (error) {
      return jsonError(500, toErrorMessage(error));
    }
  }

  return undefined;
}

function resolveUsedEmailIds(body: AssistRequest, allInboxIds: string[]): string[] {
  if (SINGLE_EMAIL_TASKS.includes(body.taskType)) {
    if (!body.emailId) {
      return [];
    }

    return [body.emailId];
  }

  if (body.selectedEmailIds && body.selectedEmailIds.length > 0) {
    return [...new Set(body.selectedEmailIds)];
  }

  return allInboxIds;
}

function isValidTaskType(taskType: unknown): taskType is AssistTaskType {
  return (
    taskType === "summarize_email" ||
    taskType === "summarize_inbox" ||
    taskType === "extract_actions" ||
    taskType === "draft_reply"
  );
}

function json(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: JSON_HEADERS,
  });
}

function jsonError(status: number, message: string): Response {
  return json({ error: message }, status);
}

function toErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown error";
}

function resolveUpstreamBaseUrl(request: Request): string {
  const configuredBaseUrl = process.env.INBOX_UPSTREAM_BASE_URL;
  if (configuredBaseUrl && configuredBaseUrl.trim().length > 0) {
    return configuredBaseUrl.trim();
  }

  const requestUrl = new URL(request.url);
  const isHttps = requestUrl.protocol === "https:";
  const defaultPort = isHttps ? "443" : "80";
  const port = requestUrl.port || defaultPort;

  // Default to loopback so internal upstream calls do not depend on external host routing.
  return `${isHttps ? "https" : "http"}://127.0.0.1:${port}`;
}
