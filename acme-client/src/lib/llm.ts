import { EMAIL_ASSISTANT_SYSTEM_PROMPT } from "./prompt";

interface RunAssistantTaskInput {
  prompt: string;
}

interface RunAssistantTaskResult {
  output: string;
  model: string;
}

const DEFAULT_MODEL = "gpt-4.1-mini";
const DEFAULT_OPENAI_URL = "https://api.openai.com";

export async function runAssistantTask(input: RunAssistantTaskInput): Promise<RunAssistantTaskResult> {
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) {
    throw new Error("OPENAI_API_KEY is not set. Add it to your environment or .env file.");
  }

  const model = process.env.OPENAI_MODEL ?? DEFAULT_MODEL;
  const openaiUrl = process.env.OPENAI_URL ?? DEFAULT_OPENAI_URL;
  const responsesUrl = buildResponsesUrl(openaiUrl);

  let response: Response;
  try {
    response = await fetch(responsesUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model,
        input: [
          {
            role: "system",
            content: [{ type: "input_text", text: EMAIL_ASSISTANT_SYSTEM_PROMPT }],
          },
          {
            role: "user",
            content: [{ type: "input_text", text: input.prompt }],
          },
        ],
      }),
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : "Unknown network error";
    throw new Error(`Unable to reach configured OPENAI_URL. Tried ${responsesUrl}. ${reason}`);
  }

  if (!response.ok) {
    const errorBody = await response.text();
    throw new Error(`OpenAI Responses API error (${response.status}): ${errorBody}`);
  }

  const payload = (await response.json()) as {
    model?: string;
    output_text?: string;
    output?: Array<{
      content?: Array<{
        type?: string;
        text?: string;
      }>;
    }>;
  };

  const output = extractOutputText(payload);
  return {
    output,
    model: payload.model ?? model,
  };
}

function buildResponsesUrl(baseUrl: string): string {
  const normalizedBase = baseUrl.trim().replace(/\/+$/, "");
  if (normalizedBase.endsWith("/v1/responses")) {
    return normalizedBase;
  }

  if (normalizedBase.endsWith("/v1")) {
    return `${normalizedBase}/responses`;
  }

  return `${normalizedBase}/v1/responses`;
}

function extractOutputText(payload: {
  output_text?: string;
  output?: Array<{
    content?: Array<{
      type?: string;
      text?: string;
    }>;
  }>;
}): string {
  if (typeof payload.output_text === "string" && payload.output_text.trim().length > 0) {
    return payload.output_text.trim();
  }

  const textParts: string[] = [];
  for (const item of payload.output ?? []) {
    for (const contentItem of item.content ?? []) {
      if (contentItem.type === "output_text" && typeof contentItem.text === "string") {
        textParts.push(contentItem.text);
      }
    }
  }

  const joined = textParts.join("\n").trim();
  if (joined.length > 0) {
    return joined;
  }

  return "No response text returned.";
}
