import { join } from "node:path";

import type { InboxItem } from "../shared/types";

const FIXTURE_ROOT = join(process.cwd(), "fixtures");
const INBOX_PATH = join(FIXTURE_ROOT, "inbox.json");

export async function readInboxItemsFromFixtures(): Promise<InboxItem[]> {
  const inboxFile = Bun.file(INBOX_PATH);
  if (!(await inboxFile.exists())) {
    throw new Error(`Inbox fixture not found at ${INBOX_PATH}`);
  }

  const inboxJson = await inboxFile.json();
  if (!Array.isArray(inboxJson)) {
    throw new Error("Inbox fixture must be a JSON array");
  }

  return inboxJson as InboxItem[];
}

export async function readRawEmailBodyFromFixtures(emailId: string): Promise<string> {
  const inboxItems = await readInboxItemsFromFixtures();
  const item = inboxItems.find((candidate) => candidate.id === emailId);

  if (!item) {
    throw new Error(`Unknown email id: ${emailId}`);
  }

  const htmlPath = join(FIXTURE_ROOT, item.htmlPath);
  const htmlFile = Bun.file(htmlPath);

  if (!(await htmlFile.exists())) {
    throw new Error(`Email HTML fixture not found at ${htmlPath}`);
  }

  return htmlFile.text();
}
