export interface LlmProvider {
  complete(prompt: string): Promise<string>;
}

export interface AcmeResponse {
  prompt: string;
  answer: string;
}

export class AcmeClient {
  private readonly provider: LlmProvider;

  public constructor(provider: LlmProvider) {
    this.provider = provider;
  }

  public async answerUser(query: string): Promise<AcmeResponse> {
    const prompt = [
      "You are ACME Assistant.",
      "Provide concise and safe responses.",
      `User query: ${query}`,
    ].join("\n");

    const answer = await this.provider.complete(prompt);
    return { prompt, answer };
  }
}
