import { readInboxItemsFromFixtures, readRawEmailBodyFromFixtures } from "../lib/fixture-store";

const UPSTREAM_JSON_HEADERS = {
  "Content-Type": "application/json",
  "Cache-Control": "no-store",
};

export async function handleUpstreamRequest(request: Request, pathname: string): Promise<Response | undefined> {
  if (request.method === "GET" && pathname === "/_upstream/inbox") {
    const requestId = createRequestId();

    try {
      const items = await readInboxItemsFromFixtures();
      return upstreamJson(items, 200, requestId);
    } catch (error) {
      return upstreamError(500, toErrorMessage(error), requestId);
    }
  }

  if (request.method === "GET" && pathname.startsWith("/_upstream/emails/")) {
    const requestId = createRequestId();
    const emailId = decodeURIComponent(pathname.replace("/_upstream/emails/", ""));

    if (!emailId) {
      return upstreamError(400, "Email id is required", requestId);
    }

    try {
      const rawHtml = await readRawEmailBodyFromFixtures(emailId);
      return upstreamJson({ id: emailId, rawHtml }, 200, requestId);
    } catch (error) {
      return upstreamError(404, toErrorMessage(error), requestId);
    }
  }

  return undefined;
}

function upstreamJson(payload: unknown, status: number, requestId: string): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      ...UPSTREAM_JSON_HEADERS,
      "x-request-id": requestId,
    },
  });
}

function upstreamError(status: number, errorMessage: string, requestId: string): Response {
  return upstreamJson({ error: errorMessage }, status, requestId);
}

function createRequestId(): string {
  return globalThis.crypto.randomUUID();
}

function toErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown upstream error";
}
