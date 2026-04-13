import { useEffect, useMemo, useState } from "react";
import { Clock, ListChecks, Loader2, MailCheck, Paperclip, Reply, Search, Sparkles, Star } from "lucide-react";

import { fetchEmailDetail, fetchInbox, runAssist, type AssistTaskType, type EmailDetail, type InboxItem } from "@/lib/inbox-api";

type ActionLabel = "Summarize Email" | "Summarize Inbox" | "Extract Actions" | "Draft Reply";

const assistantActions: Array<{ label: ActionLabel; taskType: AssistTaskType; userTask: string; icon: typeof Sparkles }> = [
  { icon: Sparkles, label: "Summarize Email", taskType: "summarize_email", userTask: "Summarize this email in 3 bullets" },
  // { icon: MailCheck, label: "Summarize Inbox", taskType: "summarize_inbox", userTask: "What matters in my inbox today?" },
  { icon: ListChecks, label: "Extract Actions", taskType: "extract_actions", userTask: "What do I need to do from this message?" },
  { icon: Reply, label: "Draft Reply", taskType: "draft_reply", userTask: "Draft a short professional reply" },
];

const Index = () => {
  const [inbox, setInbox] = useState<InboxItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<EmailDetail | null>(null);
  const [search, setSearch] = useState("");
  const [loadingInbox, setLoadingInbox] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [runningAction, setRunningAction] = useState(false);
  const [activeAction, setActiveAction] = useState<ActionLabel | null>("Summarize Email");
  const [assistantOutput, setAssistantOutput] = useState<string>("");
  const [assistantModel, setAssistantModel] = useState<string>("");
  const [error, setError] = useState<string>("");

  useEffect(() => {
    void loadInbox();
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setSelectedDetail(null);
      return;
    }

    void loadDetail(selectedId);
  }, [selectedId]);

  const filteredInbox = useMemo(() => {
    const query = search.trim().toLowerCase();
    const ordered = [...inbox].sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
    if (!query) {
      return ordered;
    }

    return ordered.filter((email) => {
      return (
        email.subject.toLowerCase().includes(query) ||
        email.sender.toLowerCase().includes(query) ||
        email.preview.toLowerCase().includes(query)
      );
    });
  }, [inbox, search]);

  async function loadInbox() {
    setLoadingInbox(true);
    setError("");

    try {
      const items = await fetchInbox();
      setInbox(items);
      if (items.length > 0) {
        setSelectedId(items[0]!.id);
      }
    } catch (loadError) {
      setError(toErrorMessage(loadError));
    } finally {
      setLoadingInbox(false);
    }
  }

  async function loadDetail(emailId: string) {
    setLoadingDetail(true);
    setError("");

    try {
      const detail = await fetchEmailDetail(emailId);
      setSelectedDetail(detail);
    } catch (loadError) {
      setError(toErrorMessage(loadError));
    } finally {
      setLoadingDetail(false);
    }
  }

  async function runAction(actionLabel: ActionLabel) {
    const action = assistantActions.find((item) => item.label === actionLabel);
    if (!action) {
      return;
    }

    if (!selectedId && action.taskType !== "summarize_inbox") {
      setError("Select an email first.");
      return;
    }

    setActiveAction(actionLabel);
    setRunningAction(true);
    setError("");

    try {
      const result = await runAssist({
        taskType: action.taskType,
        userTask: action.userTask,
        ...(action.taskType !== "summarize_inbox" && selectedId ? { emailId: selectedId } : {}),
      });

      setAssistantOutput(result.output);
      setAssistantModel(result.model);
    } catch (runError) {
      setError(toErrorMessage(runError));
    } finally {
      setRunningAction(false);
    }
  }

  const selected = selectedDetail;
  const selectedSender = selected ? parseSender(selected.sender, selected.fromAddress) : null;

  return (
    <div className="flex h-screen bg-background text-foreground">
      <div className="w-80 border-r border-border flex flex-col">
        <div className="p-3 border-b border-border">
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <input
              type="text"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search emails..."
              className="w-full rounded-md border border-input bg-background pl-9 pr-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {loadingInbox ? <div className="p-4 text-sm text-muted-foreground">Loading inbox...</div> : null}

          {!loadingInbox && filteredInbox.length === 0 ? <div className="p-4 text-sm text-muted-foreground">No emails found.</div> : null}

          {filteredInbox.map((email, index) => {
            const isSelected = email.id === selectedId;
            const isStarred = shouldShowStar(email, index);
            const showAttachment = shouldShowAttachmentVisual(email);
            const dateLabel = formatDateLabel(email.timestamp);

            return (
              <button
                key={email.id}
                onClick={() => setSelectedId(email.id)}
                className={`w-full text-left px-4 py-3 border-b border-border transition-colors ${
                  isSelected ? "bg-accent" : "hover:bg-muted/50"
                }`}
              >
                <div className="flex items-center justify-between mb-0.5">
                  <span className={`text-sm truncate ${isUnread(index) ? "font-semibold" : "text-muted-foreground"}`}>
                    {parseSender(email.sender, email.fromAddress).name}
                  </span>
                  <div className="flex items-center gap-1.5 ml-2 shrink-0">
                    {isStarred ? <Star className="h-3.5 w-3.5 fill-yellow-500 text-yellow-500" /> : null}
                    <span className="text-xs text-muted-foreground">{dateLabel}</span>
                  </div>
                </div>
                <div className={`text-sm truncate ${isUnread(index) ? "font-medium" : ""}`}>{email.subject}</div>
                <div className="flex items-center gap-1 mt-0.5">
                  {showAttachment ? <Paperclip className="h-3 w-3 text-muted-foreground shrink-0" /> : null}
                  <span className="text-xs text-muted-foreground truncate">{email.preview}</span>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <main className="flex-1 flex flex-col overflow-hidden">
        {selected ? (
          <>
            <div className="p-6 border-b border-border">
              <h1 className="text-xl font-semibold mb-1">{selected.subject}</h1>
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <div className="h-8 w-8 rounded-full bg-primary/10 flex items-center justify-center text-primary font-medium text-sm">
                  {selectedSender?.name.charAt(0).toUpperCase() ?? "?"}
                </div>
                <div>
                  <span className="font-medium text-foreground">{selectedSender?.name}</span>
                  <span className="mx-1">·</span>
                  <span>{selectedSender?.email ?? "n/a"}</span>
                </div>
                <div className="ml-auto flex items-center gap-1 text-muted-foreground">
                  <Clock className="h-3.5 w-3.5" />
                  <span className="text-xs">{formatDateLabel(selected.timestamp)}</span>
                </div>
              </div>
            </div>

            <div className="px-6 py-3 border-b border-border flex items-center gap-2 bg-muted/20">
              <Sparkles className="h-4 w-4 text-primary shrink-0" />
              <span className="text-xs font-medium text-muted-foreground mr-1">AI Assistant</span>
              {assistantActions.map((action) => (
                <button
                  key={action.label}
                  onClick={() => void runAction(action.label)}
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                    activeAction === action.label
                      ? "bg-primary text-primary-foreground"
                      : "bg-secondary text-secondary-foreground hover:bg-secondary/80"
                  }`}
                >
                  <action.icon className="h-3.5 w-3.5" />
                  {action.label}
                </button>
              ))}
            </div>

            <div className={`overflow-y-auto p-6 ${activeAction ? "h-[60%]" : "flex-1"}`}>
              {loadingDetail ? (
                <div className="text-sm text-muted-foreground">Loading email content...</div>
              ) : (
                <>
                  <iframe
                    className="w-full max-w-3xl h-[540px] border-0 bg-transparent"
                    title="Email preview"
                    sandbox=""
                    srcDoc={buildPreviewDocument(selected.rawHtml)}
                  />
                  {shouldShowAttachmentVisual(selected) ? (
                    <div className="mt-6 p-3 border border-border rounded-lg inline-flex items-center gap-2 text-sm text-muted-foreground bg-muted/30">
                      <Paperclip className="h-4 w-4" />
                      <span>attachment.pdf</span>
                      <span className="text-xs">(2.4 MB)</span>
                    </div>
                  ) : null}
                </>
              )}
            </div>

            {activeAction ? (
              <div className="h-[40%] border-t border-border bg-muted/10 flex flex-col">
                <div className="px-6 py-3 border-b border-border flex items-center justify-between shrink-0">
                  <div className="flex items-center gap-2">
                    <Sparkles className="h-4 w-4 text-primary" />
                    <span className="text-sm font-medium">{activeAction}</span>
                    {assistantModel ? <span className="text-xs text-muted-foreground">{assistantModel}</span> : null}
                  </div>
                  <button onClick={() => setActiveAction(null)} className="text-muted-foreground hover:text-foreground text-xs">
                    ✕
                  </button>
                </div>
                <div className="flex-1 overflow-y-auto px-6 py-4">
                  {runningAction ? (
                    <div className="flex items-center gap-2 text-muted-foreground text-sm">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      <span>Generating response...</span>
                    </div>
                  ) : (
                    <div className="rounded-lg bg-muted/40 border border-border p-4 text-sm leading-relaxed whitespace-pre-wrap">
                      {assistantOutput || "Run an AI action to see output."}
                    </div>
                  )}
                </div>
              </div>
            ) : null}
          </>
        ) : (
          <div className="p-6 text-sm text-muted-foreground">Select an email to get started.</div>
        )}
      </main>

      {error ? <div className="fixed bottom-4 right-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm">{error}</div> : null}
    </div>
  );
};

function toErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown error";
}

function shouldShowStar(email: InboxItem, index: number): boolean {
  return (hashString(email.id) + index) % 4 === 0;
}

function shouldShowAttachmentVisual(email: InboxItem): boolean {
  const haystack = `${email.subject} ${email.preview}`.toLowerCase();
  return ["invoice", "proposal", "attachment", "document", "report"].some((token) => haystack.includes(token));
}

function isUnread(index: number): boolean {
  return index < 7;
}

function parseSender(sender: string, fromAddress?: string): { name: string; email?: string } {
  const trimmedSender = sender.trim();
  const bracketMatch = trimmedSender.match(/^(.*)\s+<([^>]+)>$/);

  if (bracketMatch) {
    return {
      name: bracketMatch[1]!.trim(),
      email: bracketMatch[2]!.trim(),
    };
  }

  return {
    name: trimmedSender,
    ...(fromAddress && fromAddress.trim().length > 0 ? { email: fromAddress } : {}),
  };
}

function formatDateLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function hashString(value: string): number {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash << 5) - hash + value.charCodeAt(i);
    hash |= 0;
  }

  return Math.abs(hash);
}

const PREVIEW_STYLE_TAG = `<style>
  :root { color-scheme: light; }
  html, body {
    margin: 0;
    padding: 0;
    background: transparent;
    color: hsl(222.2 47.4% 11.2%);
    font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 14px;
    line-height: 1.6;
  }
  body { padding: 0; }
  h1, h2, h3, h4, h5, h6 {
    margin: 0 0 0.75rem;
    line-height: 1.3;
    color: hsl(222.2 47.4% 11.2%);
  }
  p, ul, ol, blockquote, pre, table {
    margin: 0 0 0.85rem;
  }
  a {
    color: hsl(222.2 47.4% 11.2%);
    text-decoration: underline;
  }
  img, svg, video, iframe {
    max-width: 100%;
    height: auto;
  }
  table {
    border-collapse: collapse;
    width: 100%;
  }
</style>`;

function buildPreviewDocument(rawHtml: string): string {
  const trimmed = rawHtml.trim();
  if (trimmed.length === 0) {
    return [
      "<!doctype html>",
      "<html>",
      "<head>",
      "<meta charset=\"utf-8\" />",
      PREVIEW_STYLE_TAG,
      "</head>",
      "<body><p>(No content)</p></body>",
      "</html>",
    ].join("");
  }

  if (containsHtmlRoot(trimmed)) {
    return injectPreviewStyle(trimmed);
  }

  return [
    "<!doctype html>",
    "<html>",
    "<head>",
    "<meta charset=\"utf-8\" />",
    PREVIEW_STYLE_TAG,
    "</head>",
    "<body>",
    trimmed,
    "</body>",
    "</html>",
  ].join("");
}

function containsHtmlRoot(value: string): boolean {
  return /<html[\s>]/i.test(value) || /<!doctype/i.test(value);
}

function injectPreviewStyle(documentHtml: string): string {
  if (/<head[\s>]/i.test(documentHtml)) {
    if (/<\/head>/i.test(documentHtml)) {
      return documentHtml.replace(/<\/head>/i, `${PREVIEW_STYLE_TAG}</head>`);
    }

    return documentHtml.replace(/<head([^>]*)>/i, `<head$1>${PREVIEW_STYLE_TAG}`);
  }

  if (/<html[\s>]/i.test(documentHtml)) {
    return documentHtml.replace(/<html([^>]*)>/i, `<html$1><head>${PREVIEW_STYLE_TAG}</head>`);
  }

  return `<head>${PREVIEW_STYLE_TAG}</head>${documentHtml}`;
}

export default Index;
