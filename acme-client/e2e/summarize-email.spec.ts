import { expect, test } from "@playwright/test";

const PLACEHOLDER_TEXT = "Run an AI action to see output.";

test("selects an email and shows a generated summary", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByPlaceholder("Search emails...")).toBeVisible();

  const summarizeButton = page.getByRole("button", { name: "Summarize Email", exact: true }).first();
  await summarizeButton.click();

  const loadingIndicator = page.getByText("Generating response...");
  await loadingIndicator.waitFor({ state: "visible", timeout: 30_000 }).catch(() => undefined);
  await expect(loadingIndicator).toBeHidden({ timeout: 240_000 });

  await expect(page.getByText(PLACEHOLDER_TEXT)).toBeHidden({ timeout: 240_000 });

  const outputPanel = page.locator("div.whitespace-pre-wrap").last();
  await expect(outputPanel).toBeVisible();

  const outputText = (await outputPanel.textContent())?.trim() ?? "";
  expect(outputText.length).toBeGreaterThan(0);
  expect(outputText).not.toBe(PLACEHOLDER_TEXT);
});
