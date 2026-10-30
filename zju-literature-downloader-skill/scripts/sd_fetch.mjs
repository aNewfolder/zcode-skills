#!/usr/bin/env node
// sd_fetch.mjs — ScienceDirect 文章 PDF 下载（实测可行路线，2026-10-13）。
//
// 路线：文章页按本篇 PII 过滤出 pdfft 链接 → 导航到该 URL → 标签直接落在
// pdf.sciencedirectassets.com 签名 PDF（X-Amz 时效链接，非一次性）→ 在该
// 资产域标签内同源 fetchPdfInPage。
//
// 实测坑位（Edge 155）：
//   - 文章页上下文 fetch(pdfft) 得 HTML（302 链）；跨域 fetch 资产 URL 撞 CORS；
//     在 OPG 的单次令牌场景外，SD 的 X-Amz 链接允许重复 GET，但**必须在资产域
//     自己的标签里 fetch**（导航后的 PDF 查看器上下文 origin 即资产域）。
//   - `el.click()` 无用户激活，target=_blank 弹窗被拦；Input.dispatchMouseEvent
//     也不给 activation —— 别跟弹窗较劲，直接 navigate。
//   - 一旦出现 "Are you a robot?"：停止自动化、保留标签页、记
//     sciencedirect_robot_check；实测搁置约半小时可自行解除，解除后从文章页重进。
//   - 推荐区也全是 "View PDF"，必须按本篇 PII 过滤。
//
// 用法:
//   node sd_fetch.mjs "<PII>" "<输出.pdf>"
//   例: node sd_fetch.mjs S2405959523000772 Kim_2024_ICTExpress.pdf
// 前提: 调试浏览器里已打开该文章页（含机构授权横幅）。
import fs from "node:fs";
import { navigate, evaluate, listTargets, fetchPdfInPage } from "./cdp.mjs";

const [,, pii, outPath] = process.argv;
if (!pii || !outPath) { console.error('用法: node sd_fetch.mjs "<PII>" "<输出.pdf>"'); process.exit(1); }
const sleep = ms => new Promise(r => setTimeout(r, ms));

const tabs = await listTargets();
// 书章(如 Semiconductors and Semimetals)落地页是 /science/chapter/.../pii/<PII>，
// 且 /science/article/pii/<PII> 会被 302 回 chapter 路径——两种都要认（2026-10-22 实测）。
const art = tabs.find(x => x.url.includes(`sciencedirect.com/science/article/pii/${pii}`))
        || tabs.find(x => x.url.includes(`sciencedirect.com/science/chapter/`) && x.url.includes(pii));
if (!art) { console.error(`未找到 ${pii} 的文章页标签（先在调试浏览器里打开文章页）`); process.exit(2); }

const ent = await evaluate(art.id, `JSON.stringify({robot: /are you a robot|just a moment/i.test(document.body.innerText.slice(0,2000)), zju: /Brought to you by/i.test(document.body.innerText)})`);
const ent0 = JSON.parse(ent);
if (ent0.robot) { console.error("文章页处于人机验证状态：停止自动化，等人工完成或搁置后自行解除"); process.exit(9); }
console.log("entitlement:", ent0.zju ? "Zhejiang (有授权)" : "未见机构横幅（OA 可能仍可下）");

// pdfft 链接 → 同标签导航 → 落在资产域签名 URL
const pdfft = JSON.parse(await evaluate(art.id, `JSON.stringify([...document.querySelectorAll('a')].map(a=>a.href).filter(h=>h.includes('${pii}/pdfft'))[0])`));
if (!pdfft) { console.error("未找到本篇 pdfft 链接"); process.exit(3); }
await navigate(art.id, pdfft, { waitMs: 10000 });
const st = JSON.parse(await evaluate(art.id, `JSON.stringify({href: location.href, robot: /are you a robot|just a moment/i.test(document.body.innerText.slice(0,2000))})`));
if (st.robot) { console.error("ROBOT CHECK — 停止自动化，保留标签页待人工"); process.exit(9); }
if (!st.href.includes("sciencedirectassets.com")) { console.error("未落到资产域签名 URL: " + st.href.slice(0, 120)); process.exit(4); }
console.log("asset url ok (len " + st.href.length + ")");

await fetchPdfInPage(art.id, st.href, outPath); // 同源 fetch，内部校验 %PDF 头
console.log("written:", outPath);
