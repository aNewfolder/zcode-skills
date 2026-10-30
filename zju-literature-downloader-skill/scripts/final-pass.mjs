// final-pass.mjs — ProQuest PDF.js extraction (correct timing) + Wiley pdfdirect fix.
import fs from "node:fs";
import path from "node:path";
import { evaluate } from "./cdp.mjs";
import { ROOT as OUT, PDFS } from "./paths.mjs";
const BASE = "http://127.0.0.1:9222";
const sleep = ms => new Promise(r => setTimeout(r, ms));
const newTab = async url => (await fetch(`${BASE}/json/new?` + encodeURIComponent(url), { method: "PUT" })).json();
const closeTab = async id => { try { await fetch(`${BASE}/json/close/${id}`); } catch {} };
const log = [];

async function streamBytes(tabId, exprGlobal, total, outFile) {
  const CH = 400000; fs.mkdirSync(path.dirname(outFile), { recursive: true });
  const fd = fs.openSync(outFile, "w");
  try { for (let off = 0; off < total; off += CH) {
    const b64 = await evaluate(tabId, `(()=>{ const s = ${exprGlobal}.subarray(${off}, ${off + CH}); let b=""; for (let i=0;i<s.length;i++) b += String.fromCharCode(s[i]); return btoa(b); })()`);
    fs.writeSync(fd, Buffer.from(b64, "base64"));
  } } finally { fs.closeSync(fd); }
  return fs.statSync(outFile).size;
}

// --- ProQuest-hosted items: open the page, follow its PDF.js iframe, compile the document ---
const PQ = {
  12:["Wang_2018_Nature_LN_modulators_CMOS_voltages","2123610320"],
  21:["Atabaki_2018_Nature_photonics_with_silicon_nanoelectronics","2056021658"],
  15:["Feng_2024_Nature_LN_microwave_photonic_engine","2954922315"],
  4:["Margalit_2021_APL_future_of_silicon_photonics",null],
  23:["Gaeta_2019_NatPhoton_photonic_chip_frequency_combs",null],
  1:["Liang_2010_NatPhoton_lasers_on_silicon",null],
  16:["Marpaung_2019_NatPhoton_integrated_microwave_photonics",null]
};
const SEARCH_DOI = {
  12:"10.1038/s41586-018-0551-y",21:"10.1038/s41586-018-0028-z",15:"10.1038/s41586-024-07078-9",
  4:"10.1063/5.0050117",23:"10.1038/s41566-019-0358-x",1:"10.1038/nphoton.2010.167",16:"10.1038/s41566-018-0310-5"
};
for (const [id, [slug, docid]] of Object.entries(PQ)) {
  const outFile = path.join(PDFS, slug + ".pdf");
  if (fs.existsSync(outFile)) { console.log(`[${id}] have`); continue; }
  console.log(`\n=== [${id}] ${slug}`);
  let pageUrl = docid ? `https://www.proquest.com/docview/${docid}/fulltextPDF?accountid=15198`
                      : null;
  let t = null;
  if (!pageUrl) {
    // find the ProQuest route through Summon
    const sTab = await newTab("https://zju.summon.serialssolutions.com/search?#!/search?pn=1&ho=t&include.ft.matches=f&l=en&q=" + encodeURIComponent(SEARCH_DOI[id]));
    let link = null;
    for (let a = 0; a < 4 && !link; a++) { await sleep(a === 0 ? 9000 : 5500);
      try { link = await evaluate(sTab.id, `(() => { const a = [...document.querySelectorAll("a")].find(x => (x.textContent||"").trim() === "PDF" && (x.getAttribute("href")||"").startsWith("http")); return a ? a.getAttribute("href") : null; })()`); } catch {} }
    if (!link) { console.log("   no summons route"); log.push({ id, status: "no_route" }); await closeTab(sTab.id); continue; }
    t = await newTab(link); await sleep(14000);
  } else { t = await newTab(pageUrl); await sleep(14000); }
  // if not on ProQuest, try to reach it via the publisher page's ProQuest link
  let cur = JSON.parse(await evaluate(t.id, "JSON.stringify({url:location.href})"));
  if (!/proquest/i.test(cur.url)) { console.log(`   landed on ${cur.url.slice(0,80)} (not ProQuest)`); }
  const vurl = await evaluate(t.id, `(() => { const f = [...document.querySelectorAll("iframe")].map(f => f.src||"").find(s => /viewer\\.html/.test(s)); return f || null; })()`);
  if (!vurl) { console.log("   no PDF.js viewer iframe"); log.push({ id, status: "no_viewer", url: cur.url }); await closeTab(t.id); continue; }
  const v = await newTab(vurl);
  let info = null;
  for (const w of [12000, 8000, 8000]) {
    await sleep(w);
    try { info = JSON.parse(await evaluate(v.id, `JSON.stringify({ app: typeof PDFViewerApplication !== "undefined", doc: typeof PDFViewerApplication !== "undefined" && !!PDFViewerApplication.pdfDocument, pages: (typeof PDFViewerApplication !== "undefined" && PDFViewerApplication.pdfDocument) ? PDFViewerApplication.pdfDocument.numPages : null })`)); } catch {}
    console.log(`   viewer +${w}:`, JSON.stringify(info));
    if (info && info.doc) break;
  }
  if (info && info.doc) {
    const got = JSON.parse(await evaluate(v.id, `(async()=>{ try { const b = await PDFViewerApplication.pdfDocument.getData(); window.__pqB = b; return JSON.stringify({n:b.length, head:String.fromCharCode(...b.slice(0,5))}); } catch(e){ return JSON.stringify({err:String(e).slice(0,100)}); } })()`));
    if (got.head === "%PDF-") {
      const bytes = await streamBytes(v.id, "window.__pqB", got.n, outFile);
      console.log(`   SAVED ${(bytes/1e6).toFixed(2)} MB (${info.pages} pages) via proquest-pdfjs`);
      log.push({ id, status: "downloaded", bytes, pages: info.pages, via: "proquest-pdfjs" });
    } else { console.log("   getData:", JSON.stringify(got).slice(0,120)); log.push({ id, status: "getdata_failed", got }); }
  } else log.push({ id, status: "viewer_not_ready" });
  await closeTab(t.id); await closeTab(v.id); await sleep(1000);
}
fs.writeFileSync(path.join(OUT, "final_pass_log.json"), JSON.stringify(log, null, 2), "utf8");
const s = {}; for (const l of log) s[l.status] = (s[l.status] || 0) + 1;
console.log("\n=== final pass summary:", JSON.stringify(s));
