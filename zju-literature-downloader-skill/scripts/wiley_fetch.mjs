#!/usr/bin/env node
// wiley_fetch.mjs — Wiley 全系(pdfdirect)通用 DOI 下载。
//
// 与手拼 URL 的差异(2026-10-21 实测):doi.org 会 302 到**期刊自己的子域**
// (advanced.onlinelibrary.wiley.com / sid.onlinelibrary.wiley.com / …),
// pdfdirect 必须从**落地 href 的 origin** 发起,硬编码 onlinelibrary.wiley.com
// 会撞跨域。本脚本导航后自动取落地 origin。
//
// 用法:
//   node wiley_fetch.mjs "<doi>" "<输出.pdf>"
//   例: node wiley_fetch.mjs "10.1002/adom.202201701" Gonzalez_2022.pdf
//
// 行为:导航 doi.org → 检测人机验证(中招即停,退出码 9)→ 校验落在 Wiley 域
// → 同源 fetch `<origin>/doi/pdfdirect/<doi>?download=true` → 校验 %PDF 头。
import { navigate, evaluate, listTargets, newPage, fetchPdfInPage } from "./cdp.mjs";

const [,, doi, outPath] = process.argv;
if (!doi || !outPath) { console.error('用法: node wiley_fetch.mjs "<doi>" "<输出.pdf>"'); process.exit(1); }
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function evalSafe(tabId, expr, tries = 4) {
  for (let i = 0; i < tries; i++) {
    try { return JSON.parse(await evaluate(tabId, expr)); }
    catch (e) { console.log(`  evaluate retry ${i + 1}`); await sleep(5000); }
  }
  throw new Error("evaluate 持续超时");
}

const tabs0 = await listTargets();
let tab = tabs0.find(x => x.url.includes("onlinelibrary.wiley.com"));
if (!tab) tab = await newPage("https://onlinelibrary.wiley.com");
await sleep(3000);
await navigate(tab.id, `https://doi.org/${doi}`, { waitMs: 15000 });

const st = await evalSafe(tab.id,
  `JSON.stringify({href: location.href, robot: /are you a robot|just a moment|verify you are human/i.test(document.body ? document.body.innerText.slice(0, 2000) : ""), title: document.title.slice(0, 80)})`);
console.log("landed:", JSON.stringify(st));
if (st.robot) { console.error("ROBOT CHECK — 停止自动化,保留标签页待人工"); process.exit(9); }
if (!st.href.includes("onlinelibrary.wiley.com")) { console.error("未落到 Wiley: " + st.href.slice(0, 120)); process.exit(4); }

const origin = new URL(st.href).origin;
await fetchPdfInPage(tab.id, `${origin}/doi/pdfdirect/${doi}?download=true`, outPath);
console.log("written:", outPath);
