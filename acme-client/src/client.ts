import type { AssistRequest, AssistResponse, EmailDetail, InboxItem } from "./shared/types";

export class AcmeApiClient {
  private readonly baseUrl: string;

  public constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  public async getInbox(): Promise<InboxItem[]> {
    const response = await fetch(`${this.baseUrl}/api/inbox`);
    return this.readJson<InboxItem[]>(response);
  }

  public async getEmail(id: string): Promise<EmailDetail> {
    const response = await fetch(`${this.baseUrl}/api/inbox/${encodeURIComponent(id)}`);
    return this.readJson<EmailDetail>(response);
  }

  public async assist(request: AssistRequest): Promise<AssistResponse> {
    const response = await fetch(`${this.baseUrl}/api/assist`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });

    return this.readJson<AssistResponse>(response);
  }

  private async readJson<T>(response: Response): Promise<T> {
    if (!response.ok) {
      throw new Error(await response.text());
    }

    return (await response.json()) as T;
  }
}
