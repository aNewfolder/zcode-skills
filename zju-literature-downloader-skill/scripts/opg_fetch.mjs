#!/usr/bin/env node
// opg_fetch.mjs — Optica (opg.optica.org) 签名 PDF 下载：双标签法。
//
// 为什么必须双标签（2026-10-13 实测）：
//   viewmedia.cfm 先渲染 "Please wait..." 反爬中转页，再 302 到
//   directpdfaccess/<token>/<file>.pdf 签名 URL。该令牌被查看器渲染**消耗一次**：
//   在 PDF 查看器标签里 fetch(location.href) 只会拿到 text/html；
//   从文章页跨域 fetch 资产 URL 又撞 CORS。因此：
//   B 标签只负责「拿新鲜签名 URL」，A 标签（同源 HTML 页）负责 fetch。
//
// 用法:
//   node opg_fetch.mjs "<viewmedia URL>" "<输出.pdf>"
//   例: node opg_fetch.mjs "https://opg.optica.org/prj/viewmedia.cfm?uri=prj-10-8-1892&seq=0" Zhu_2022.pdf
//
// 坑位记录:
//   - tabB 必须**先导航再轮询**，且要求 href 与导航前不同：若 tabB 停在上一篇的
//     directpdfaccess URL 上直接轮询，会把旧 URL 当成本篇的，产出「对的名字、错的论文」
//     （identity check 能抓住，但别靠它兜底）。
//   - OA 文章与有机构授权的订阅文章都走得通（授权看文章页是否带机构标记）。
import fs from "node:fs";
import { navigate, evaluate, listTargets, newPage, fetchPdfInPage, httpJson } from "./cdp.mjs";

const [,, mediaUrl, outPath] = process.argv;
if (!mediaUrl || !outPath) { console.error('用法: node opg_fetch.mjs "<viewmedia URL>" "<输出.pdf>"'); process.exit(1); }
const sleep = ms => new Promise(r => setTimeout(r, ms));
const closeTab = async id => { try { await httpJson(`http://127.0.0.1:9222/json/close/${id}`); } catch {} };

let tabs = await listTargets();
let tabB = tabs.find(x => x.url.includes("optica"));
if (!tabB) tabB = await newPage(mediaUrl);
console.log("tabB:", tabB.id);

// 先记录旧 href（可能停在上一篇的签名 URL 上），导航到本篇 viewmedia，只接受「变化的」新签名 URL
const before = JSON.parse(await evaluate(tabB.id, `JSON.stringify({href: location.href})`)).href;
await navigate(tabB.id, mediaUrl, { waitMs: 4000 });
let signedUrl = null;
for (let i = 0; i < 6 && !signedUrl; i++) {
  await sleep(4000);
  const r = JSON.parse(await evaluate(tabB.id, `JSON.stringify({href: location.href})`));
  console.log("  tabB href:", r.href.slice(0, 100));
  if (r.href.includes("directpdfaccess") && r.href !== before) signedUrl = r.href;
}
if (!signedUrl) { console.error("FAILED: 未拿到新签名 URL（可能无授权或被反爬拦截）"); process.exit(2); }
console.log("signedUrl ok");

// A 标签：同源 HTML 上下文（由 mediaUrl 推导 abstract 页），负责实际 fetch
const htmlUrl = mediaUrl.replace("viewmedia.cfm", "abstract.cfm");
tabs = await listTargets();
let tabA = tabs.find(x => x.url.includes("opg.optica.org") && x.id !== tabB.id && !x.url.includes("directpdfaccess"));
if (!tabA) tabA = await newPage(htmlUrl);
await navigate(tabA.id, htmlUrl, { waitMs: 9000 });
const chk = JSON.parse(await evaluate(tabA.id, `JSON.stringify({href: location.href.slice(0,80), ct: document.contentType, ready: document.readyState})`));
console.log("tabA:", JSON.stringify(chk));
if (chk.ct !== "text/html") { console.error("tabA 不是 HTML 页，无法作 fetch 上下文"); process.exit(3); }

await fetchPdfInPage(tabA.id, signedUrl, outPath); // 内部校验 %PDF 头
console.log("written:", outPath);
await closeTab(tabA.id);
