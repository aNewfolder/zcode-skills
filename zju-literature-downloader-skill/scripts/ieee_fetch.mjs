#!/usr/bin/env node
// ieee_fetch.mjs — IEEE Xplore 通用 DOI 下载：doi.org → document 页取 arnumber
// → stamp.jsp → iframe 的 stampPDF/getPDF.jsp → 在 stamp 页上下文 fetchPdfInPage。
//
// 坑位记录（2026-10-13 实测，Edge 155 + 浙大校园网出口）：
//   - Crossref 的 alternative-id 常缺 arnumber，别依赖；document 页 HTML 里
//     正则 /arnumber[="' :]+(\d{6,9})/ 稳定可取。
//   - stamp.jsp 的 iframe 是 stampPDF/getPDF.jsp?tp=&arnumber=N&ref= —— **没有
//     .pdf 后缀**，按「iframe 里找 .pdf」过滤会一无所获；直接取 frames[0] fetch。
//   - document 页文本含 "Zhejiang"/"Access provided by" 即机构授权有效；
//     若含 /denied/ 或跳登录，记 cas_waiting_user / publisher_verification_waiting_user。
//
// 用法:
//   node ieee_fetch.mjs "<DOI>" "<输出.pdf>"
import { navigate, evaluate, newPage, fetchPdfInPage } from "./cdp.mjs";

const [,, doi, outPath] = process.argv;
if (!doi || !outPath) { console.error('用法: node ieee_fetch.mjs "<DOI>" "<输出.pdf>"'); process.exit(1); }
const sleep = ms => new Promise(r => setTimeout(r, ms));

const t = await newPage("https://doi.org/" + doi);
console.log("tab:", t.id);
let arn = null;
for (let i = 0; i < 6 && !arn; i++) {
  await sleep(i === 0 ? 10000 : 5000);
  const r = JSON.parse(await evaluate(t.id, `JSON.stringify({url: location.href.slice(0,110), title: document.title.slice(0,80), arn: (document.body.innerHTML.match(/arnumber[=\\"':]+(\\d{6,9})/)||[])[1]||null, zju: /Zhejiang|Access provided by/i.test(document.body.innerText), denied: /denied/i.test(location.href)})`));
  console.log(i, JSON.stringify(r));
  if (r.arn) arn = r.arn;
}
if (!arn) { console.error("未取到 arnumber（页面可能未加载完或被拦）"); process.exit(2); }

await navigate(t.id, `https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=${arn}`, { waitMs: 9000 });
let pdfUrl = null;
for (let i = 0; i < 4 && !pdfUrl; i++) {
  const r = JSON.parse(await evaluate(t.id, `JSON.stringify({
    href: location.href.slice(0,120),
    frames: [...document.querySelectorAll('iframe,embed')].map(e=>e.src||e.data||'').filter(Boolean).slice(0,3)
  })`));
  console.log("stamp", i, JSON.stringify(r));
  pdfUrl = (r.frames || [])[0] || null; // getPDF.jsp 无 .pdf 后缀，取第一个 frame
  if (!pdfUrl) await sleep(4000);
}
if (!pdfUrl) { console.error("stamp 页未找到 getPDF iframe（可能无授权）"); process.exit(3); }
console.log("pdfUrl:", pdfUrl.slice(0, 120));

await fetchPdfInPage(t.id, pdfUrl, outPath); // 内部校验 %PDF 头
console.log("written:", outPath);
