#!/usr/bin/env node
// article_page_fetch.mjs — "文章页先行"通用 PDF 抓取器。
//
// 适用场景(2026-10-21 实测):browser_pdf_downloader 对 `--url <PDF直链>` 直接
// 打开+fetch 会被 Cloudflare 403(ACM /doi/pdf/ 实测;AIP /article-pdf/ 同),
// 但**先在标签里渲染文章落地页**(被动过盾、拿到 clearance cookie)之后,再从该
// 页面同源 fetch PDF 就能过。ACM 实测成;RSC/AIP/IOP 同法仍被反爬接走——盾若
// 主动拦 fetch(见下),一律停手记 waiting,不换路径变体硬试。
//
// 用法:
//   node article_page_fetch.mjs "<文章落地页URL>" "<输出.pdf>" [备选PDF直链1 备选PDF直链2 ...]
//   例: node article_page_fetch.mjs "https://dl.acm.org/doi/10.1145/xxx" out.pdf
//       node article_page_fetch.mjs "https://pubs.rsc.org/xx/article-abstract/..." out.pdf \
//            "https://pubs.rsc.org/en/content/articlepdf/2026/nr/xxxxx"
//
// 行为:复用同域标签或新建 → 导航落地页(14s)→ 人机验证检测(中招退出码 9)
// → 同源 fetch:优先显式候选 URL,否则页内找 .pdf 锚点(优先 /article/pdf、
// articlepdf、/doi/pdf/)→ 分块 base64 回传 → 校验 %PDF 头落盘。
import fs from "node:fs";
import { navigate, evaluate, listTargets, newPage } from "./cdp.mjs";

const [,, articleUrl, outPath, ...explicitCandidates] = process.argv;
if (!articleUrl || !outPath) {
  console.error('用法: node article_page_fetch.mjs "<文章落地页URL>" "<输出.pdf>" [备选PDF直链...]');
  process.exit(1);
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function evalSafe(tabId, expr, tries = 4) {
  for (let i = 0; i < tries; i++) {
    try { return JSON.parse(await evaluate(tabId, expr)); }
    catch (e) { await sleep(5000); }
  }
  throw new Error("evaluate 持续超时");
}

const host = new URL(articleUrl).host;
const tabs0 = await listTargets();
let tab = tabs0.find(x => x.url.includes(host));
if (!tab) tab = await newPage(articleUrl);
await sleep(2000);
await navigate(tab.id, articleUrl, { waitMs: 14000 });

const st = await evalSafe(tab.id,
  `JSON.stringify({href: location.href.slice(0, 110), cf: /just a moment|verify you are human|attention required/i.test(document.body ? document.body.innerText.slice(0, 1500) : "")})`);
console.log("landed:", JSON.stringify(st));
if (st.cf) { console.error("人机验证 — 停止自动化,保留标签页待人工"); process.exit(9); }

// 同源探测候选 PDF URL;fetch 里预读 4 字节判断是否真 PDF(盾页也是 200 + HTML)
async function probe(url) {
  return evalSafe(tab.id, `(async()=>{
    try {
      const resp = await fetch(${JSON.stringify(url)}, {credentials:"include"});
      const ab = await resp.arrayBuffer();
      window.__pdf = new Uint8Array(ab);
      const head = new TextDecoder().decode(ab.slice(0,4));
      return JSON.stringify({ok: resp.ok, st: resp.status, n: ab.byteLength, head, ct: (resp.headers.get("content-type")||"").slice(0,40)});
    } catch(e) { return JSON.stringify({err: String(e).slice(0,80)}); }
  })()`);
}

const anchor = explicitCandidates.length ? null : await evalSafe(tab.id, `(function(){
  const cands = [...document.querySelectorAll('a')].map(a => a.href).filter(h => h && h.includes('.pdf'));
  return JSON.stringify(cands.find(h => h.includes('/article/pdf')) || cands.find(h => h.includes('articlepdf')) || cands.find(h => h.includes('/doi/pdf/')) || cands[0] || null);
})()`);

const candidates = [...explicitCandidates, anchor].filter(Boolean);
if (!candidates.length) { console.error("无显式候选且页内未找到 .pdf 锚点"); process.exit(3); }

for (const url of candidates) {
  const r = await probe(url);
  console.log(url.slice(0, 90), "->", JSON.stringify(r).slice(0, 130));
  if (r.err || r.head !== "%PDF" || r.n < 20000) continue; // 盾页通常 200 + text/html,靠 head/大小剔除
  const chunks = []; let off = 0; const total = r.n;
  while (off < total) {
    const len = Math.min(500000, total - off);
    const b64 = await evalSafe(tab.id, `(function(){
      const slice = window.__pdf.subarray(${off}, ${off + len});
      let s = ""; const CH = 32768;
      for (let i = 0; i < slice.length; i += CH) s += String.fromCharCode.apply(null, slice.subarray(i, i + CH));
      return JSON.stringify(btoa(s));
    })()`);
    chunks.push(Buffer.from(b64, "base64"));
    off += len;
  }
  const buf = Buffer.concat(chunks);
  fs.writeFileSync(outPath, buf);
  console.log("written:", outPath, buf.length);
  process.exit(0);
}
console.error("所有候选 URL 都未返回真 PDF(可能被反爬接走)——停止,记 publisher_blocked_waiting_user");
process.exit(4);
