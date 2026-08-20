// Generate README screenshots by driving the running web app with headless Chromium.
//
//   npm i -D playwright && npx playwright install chromium
//   WEB_URL=http://localhost:8080 OUT=docs/screenshots node scripts/screenshots.mjs
//
// Captures: customer chat (verified + grounded answer + sources), admin conversation
// detail (transcript + tool table), and the evaluations / tool-activity / providers tabs.

import { chromium } from "playwright";

const BASE = process.env.WEB_URL || "http://localhost:8080";
const OUT = process.env.OUT || "docs/screenshots";

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 });
const page = await ctx.newPage();
page.setDefaultTimeout(15000);
const shot = async (name) => { await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true }); console.log("saved", name); };

// ---- Customer view: greet -> pick demo customer -> verify -> ask a grounded question
await page.goto(BASE, { waitUntil: "networkidle" });
const input = page.getByPlaceholder("Type your message…");
await input.fill("Hi, I have a question about my auto policy.");
await page.getByRole("button", { name: "Send" }).click();
await page.waitForTimeout(1500);
try { await page.getByRole("combobox").first().selectOption({ label: /Maria Alvarez/ }); }
catch { await page.getByRole("combobox").first().selectOption({ index: 1 }); }
await page.waitForTimeout(400);
await page.getByRole("button", { name: /^Verify/ }).click();
await page.waitForTimeout(1500);
await input.fill("What is my collision deductible on AUTO-10024?");
await page.getByRole("button", { name: "Send" }).click();
try { await page.getByText(/\$?500/).first().waitFor({ timeout: 12000 }); } catch { await page.waitForTimeout(4000); }
await page.waitForTimeout(1200);
await shot("customer-chat");

// ---- Admin view: conversation detail + tabs
await page.goto(`${BASE}/admin`, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
try { await page.locator("table tbody tr, .conversation-row, li").first().click({ timeout: 4000 }); await page.waitForTimeout(1000); } catch {}
await shot("admin-dashboard");
for (const [name, file] of [[/Evaluations/, "admin-evaluations"], [/Tool Activity/, "admin-tool-activity"], [/Providers/, "admin-providers"]]) {
  try { await page.getByRole("button", { name }).first().click(); await page.waitForTimeout(1000); await shot(file); } catch (e) { console.log("skip", file, String(e).slice(0, 60)); }
}

await browser.close();
console.log("done");
