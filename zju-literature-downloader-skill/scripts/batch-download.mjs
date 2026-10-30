// batch-download.mjs v5
import fs from "node:fs";
import path from "node:path";
import { evaluate, fetchPdfInPage } from "./cdp.mjs";
import { ROOT as OUT, PDFS } from "./paths.mjs";
const BASE = "http://127.0.0.1:9222";
fs.mkdirSync(PDFS, { recursive: true });

const IDS = {
  1:"Liang_2010_NatPhoton_lasers_on_silicon",2:"Komljenovic_2016_JLT_heterogeneous_silicon_PICs",
  3:"Xiang_2022_JSTQE_high_performance_silicon_photonics",4:"Margalit_2021_APL_future_of_silicon_photonics",
  5:"Wan_2025_NatRevEE_silicon_photonics_with_CMOS",8:"Jones_2019_IEEENanoMag_InP_silicon_transceivers",
  9:"Corbett_2017_PQE_transfer_print_heterogeneous_integration",12:"Wang_2018_Nature_LN_modulators_CMOS_voltages",
  14:"Wang_2024_Nature_lithium_tantalate_PICs",15:"Feng_2024_Nature_LN_microwave_photonic_engine",
  16:"Marpaung_2019_NatPhoton_integrated_microwave_photonics",17:"Liu_2020_AOP_integrated_microwave_photonic_filters",
  18:"Pan_2020_JLT_microwave_photonic_radars",19:"Li_2016_FOE_recent_advances_microwave_photonics",
  21:"Atabaki_2018_Nature_photonics_with_silicon_nanoelectronics",23:"Gaeta_2019_NatPhoton_photonic_chip_frequency_combs",
  26:"Mahajan_2022_JLT_co_packaged_photonics_HPC",27:"Fathololoumi_2021_JLT_1.6Tbps_silicon_photonic_engine",
  28:"Soref_2006_JSTQE_past_present_future_silicon_photonics",29:"Xu_2022_Optica_dual_polarization_TFLN_IQ_modulators",
  30:"Zhang_2025_CAE_AI_era_optoelectronic_integration",32:"Pavesi_2016_Springer_Silicon_Photonics_III",
  33:"Vivien_2013_CRCPress_handbook_of_silicon_photonics",34:"Coldren_2012_Wiley_diode_lasers_and_PICs_book",
  35:"Reed_2008_Wiley_silicon_photonics_state_of_the_art_book"
};
const DOIS = {
  1:"10.1038/nphoton.2010.167",2:"10.1109/JLT.2015.2465382",3:"10.1109/JSTQE.2021.3126124",
  4:"10.1063/5.0050117",5:"10.1038/s44287-025-00223-0",8:"10.1109/MNANO.2019.2891369",
  9:"10.1016/j.pquantelec.2017.01.001",12:"10.1038/s41586-018-0551-y",14:"10.1038/s41586-024-07234-1",
  15:"10.1038/s41586-024-07078-9",16:"10.1038/s41566-018-0310-5",17:"10.1364/AOP.382548",
  18:"10.1109/JLT.2020.2993166",19:"10.1007/s12200-016-0633-0",21:"10.1038/s41586-018-0028-z",
  23:"10.1038/s41566-019-0358-x",26:"10.1109/JLT.2021.3104725",27:"10.1109/JLT.2020.3039218",
  28:"10.1109/JSTQE.2006.883151",29:"10.1364/OPTICA.449691",30:"10.15302/J-SSCAE-2025.11.016",
  32:"10.1007/978-3-642-10503-6",33:"10.1201/b14668",34:"10.1002/9781118148167",35:"10.1002/9780470994535"
};
const args = process.argv.slice(2);
let ids = args.filter(a => /^\d+$/.test(a)).map(Number);
if (args.includes("--all-pending")) ids = Object.keys(DOIS).map(Number).filter(id => !fs.existsSync(path.join(PDFS, IDS[id] + ".pdf")));

const sleep = ms => new Promise(r => setTimeout(r, ms));
const log = [];
const newTab = async url => (await fetch(`${BASE}/json/new?` + encodeURIComponent(url), { method: "PUT" })).json();
const closeTab = async id => { try { await fetch(`${BASE}/json/close/${id}`); } catch {} };
const isCas = u => /zjuam\.zju\.edu\.cn|\/cas\/login|webvpn\.zju\.edu\.cn\/login|idp\.zju\.edu\.cn\/idp|linkinghub|serialssolutions\.com\/\?ctx_ver|\/login\?/i.test(u || "");
const isChallenge = i => /are you a robot|just a moment|human verification|verify you are human|unusual traffic|cf-challenge/i.test((i.body || "") + " " + (i.title || ""));
const isPdf = i => /\.pdf(\?|#|$)/i.test(i.url || "") || (i.ct || "").includes("pdf") || /mhjfbmdgcfjbbpaeojofohoefgiehjai/.test(i.url || "");

async function readPage(tabId) {
  return JSON.parse(await evaluate(tabId, `JSON.stringify({
    url: location.href, title: document.title, ct: document.contentType,
    body: (document.body && document.body.innerText || "").replace(/\\s+/g," ").slice(0, 300),
    authed: /Brought to you by|brought to you by|Zhejiang|浙江大学|Access through your institution|Get PDF|ZHEJIANG/i.test(document.body.innerText),
    pdfLinks: [...document.querySelectorAll("a")].map(a => ({t:(a.textContent||"").replace(/\\s+/g," ").trim(), h:a.href||"", aria:a.getAttribute("aria-label")||""}))
      .filter(x => x.h && x.h.startsWith("http") && (/^(View PDF|PDF|Get PDF|Download PDF|Download|Full Text|全文|View full text)$/i.test(x.t) || /pdf/i.test(x.aria) || /\\.pdf(\\?|#|$)/i.test(x.h) || /pdfdirect|viewmedia|article-pdf|fulltextPDF|downloadpdf|stamp\\.jsp/i.test(x.h)))
      .slice(0, 14),
    metaPdf: (document.querySelector('meta[name="citation_pdf_url"]')||{}).content || null,
    iframes: [...document.querySelectorAll("iframe,embed,object")].map(f => f.src || f.data || "").filter(Boolean).slice(0, 8),
    summon: [...document.querySelectorAll("a")].map(a => ({t:(a.textContent||"").replace(/\\s+/g," ").trim(), aria:a.getAttribute("aria-label")||"", h:a.getAttribute("href")||""}))
      .filter(x => x.h && x.h.startsWith("http") && (/^(PDF|Full Text|View PDF)$/i.test(x.t) || x.t === "在线全文" || /^在线全文/.test(x.aria))).slice(0, 8)
  })`));
}

function candidatesFor(id, p) {
  const doi = DOIS[id] || "", u = p.url || "", out = [];
  const push = (url, note) => { if (url && url.startsWith("http") && !out.some(o => o.url === url)) out.push({ url, note }); };
  const natureArticle = /nature\.com\/articles\//.test(u);
  // IEEE: the stamp.jsp iframe carries the real PDF stream
  for (const f of p.iframes || []) if (/stamp\.jsp|ielx|\.pdf/i.test(f)) push(f, "iframe:ieee/pdf");
  if (p.metaPdf && natureArticle) push(p.metaPdf, "meta-citation_pdf_url");
  for (const l of p.pdfLinks || []) push(l.h, "anchor:" + (l.t || l.aria || "pdf"));
  if (p.metaPdf && !out.some(o => o.url === p.metaPdf)) push(p.metaPdf, "meta-citation_pdf_url");
  if (/^10\.1038\//.test(doi) && natureArticle) push(u.split("#")[0].replace(/\/$/, "") + ".pdf", "pattern:nature");
  if (/^10\.1364\//.test(doi) && /opg\.optica\.org/.test(u)) {
    const m = u.match(/uri=([a-z]+-\d+-\d+-\d+)/i), j = (u.match(/\/(optica|oe|aop|prj)\//) || [])[1] || "oe";
    if (m) push(`https://opg.optica.org/${j}/viewmedia.cfm?uri=${m[1]}&seq=0`, "pattern:optica");
  }
  if (/^10\.1126\//.test(doi) && /science\.org/.test(u)) push(`https://www.science.org/doi/pdf/${doi}?download=true`, "pattern:science");
  if (/^10\.1007\//.test(doi)) push(`https://link.springer.com/content/pdf/${doi}.pdf`, "pattern:springer");
  if (/^10\.1002\//.test(doi) && /wiley\.com/.test(u)) push(`https://${location.host}/doi/pdfdirect/${doi}?download=true`, "pattern:wiley");
  return out;
}

async function trySave(tabId, url, outFile, note) {
  try { const r = await fetchPdfInPage(tabId, url, outFile, {});
    console.log(`   SAVED ${(r.bytes/1e6).toFixed(2)} MB via ${note}`);
    return { ok: true, bytes: r.bytes, url, note };
  } catch (e) { console.log(`   x ${note}: ${e.message.slice(0,85)}`); return { ok: false, error: e.message.slice(0, 140), note }; }
}
// Open a candidate URL in a fresh tab; if it renders as a PDF, save it same-origin.
async function tryViaTab(url, outFile, note) {
  if (!url) return { ok: false, note, error: "no url" };
  const t = await newTab(url); await sleep(7000);
  try {
    const p = await readPage(t.id);
    if (isPdf(p)) { const r = await trySave(t.id, "location.href", outFile, note + ":as-tab"); return r; }
    // maybe an intermediate page that embeds the PDF
    for (const f of p.iframes || []) if (/\.pdf|ielx|stamp\.jsp/i.test(f)) { const r = await trySave(t.id, f, outFile, note + ":iframe"); if (r.ok) { await closeTab(t.id); return r; } }
    console.log(`   x ${note}: landed on ${p.url.slice(0, 80)}`);
    return { ok: false, note, error: "not pdf: " + p.url.slice(0, 80) };
  } finally { await closeTab(t.id); }
}

async function extractFromViewer(tabId, outFile) {
  const probe = await evaluate(tabId, `(async()=>{
    if (typeof PDFViewerApplication === "undefined" || !PDFViewerApplication.pdfDocument) return JSON.stringify({ok:false});
    const d = PDFViewerApplication.pdfDocument;
    try { const bytes = await d.getData(); return JSON.stringify({ok:true, pages:d.numPages, n:bytes.length, head:String.fromCharCode(...bytes.slice(0,5))}); } catch(e) { return JSON.stringify({ok:false, err:String(e)}); }
  })()`);
  const info = JSON.parse(probe);
  if (info.ok && info.head === "%PDF-") {
    const CH = 300000; fs.mkdirSync(path.dirname(outFile), { recursive: true });
    const fd = fs.openSync(outFile, "w");
    try { for (let off = 0; off < info.n; off += CH) {
      const b64 = await evaluate(tabId, `(async()=>{ const bytes = await PDFViewerApplication.pdfDocument.getData(); const s = bytes.subarray(${off}, ${off + CH}); let b=""; for (let i=0;i<s.length;i++) b += String.fromCharCode(s[i]); return btoa(b); })()`);
      fs.writeSync(fd, Buffer.from(b64, "base64"));
    } } finally { fs.closeSync(fd); }
    console.log(`   SAVED ${(fs.statSync(outFile).size/1e6).toFixed(2)} MB via proquest-viewer`);
    return { ok: true, bytes: fs.statSync(outFile).size, note: "proquest-viewer" };
  }
  return { ok: false, error: "viewer probe failed: " + probe.slice(0, 100) };
}

console.log("ids:", ids.join(", "));
for (const id of ids) {
  const slug = IDS[id], doi = DOIS[id], outFile = path.join(PDFS, slug + ".pdf");
  if (fs.existsSync(outFile)) { console.log(`[${id}] have`); continue; }
  console.log(`\n=== [${id}] ${slug}`);
  const sTab = await newTab("https://zju.summon.serialssolutions.com/search?#!/search?pn=1&ho=t&include.ft.matches=f&l=en&q=" + encodeURIComponent(doi));
  let summonLinks = null;
  for (let a = 0; a < 4 && !summonLinks; a++) { await sleep(a === 0 ? 9000 : 5500);
    try { const p = await readPage(sTab.id); if ((p.summon || []).length) summonLinks = p.summon; } catch {} }
  let pTab = null, page = null, viaSummon = null;
  if (summonLinks) { const pick = summonLinks.find(l => l.t === "PDF") || summonLinks.find(l => /^在线全文/.test(l.aria)) || summonLinks[0];
    viaSummon = pick; console.log(`   summon -> [${pick.t}${pick.aria ? " / " + pick.aria.slice(0, 40) : ""}]`); pTab = await newTab(pick.h); }
  else { console.log("   summon: none, using DOI"); pTab = await newTab("https://doi.org/" + doi); }
  for (const w of [5000, 6000, 7000, 8000, 9000]) {
    await sleep(w);
    try { page = await readPage(pTab.id); } catch { continue; }
    const tag = isCas(page.url) ? "CAS" : isChallenge(page) ? "CHALLENGE" : isPdf(page) ? "PDF" : page.url === "about:blank" ? "blank" : "page";
    console.log(`   +${w}ms ${tag} authed=${page.authed} ${page.url.slice(0, 88)}`);
    if (["CAS", "CHALLENGE"].includes(tag)) break;
    if (tag === "page" && (page.metaPdf || (page.pdfLinks || []).length || /proquest|viewer\.html|stamp\.jsp/i.test(page.url))) break;
  }
  if (!page) { log.push({ id, status: "unknown" }); await closeTab(sTab.id); await closeTab(pTab.id); continue; }
  if (isCas(page.url)) { log.push({ id, status: "cas_waiting_user", url: page.url }); console.log("   -> CAS"); await closeTab(sTab.id); continue; }
  if (isChallenge(page)) { log.push({ id, status: "publisher_verification_waiting_user", url: page.url }); console.log("   -> challenge"); await closeTab(sTab.id); continue; }

  let saved = null;
  if (isPdf(page)) saved = await trySave(pTab.id, "location.href", outFile, "pdf-tab");
  if (!(saved && saved.ok)) {
    const cands = candidatesFor(id, page);
    console.log(`   candidates: ${cands.map(c => c.note).slice(0, 6).join(", ") || "none"}`);
    for (const c of cands) { saved = await trySave(pTab.id, c.url, outFile, c.note); if (saved.ok) break; }
    if (!(saved && saved.ok)) for (const c of cands.slice(0, 4)) { saved = await tryViaTab(c.url, outFile, c.note); if (saved.ok) break; }
  }
  if (!(saved && saved.ok) && /proquest/i.test(page.url)) saved = await extractFromViewer(pTab.id, outFile);
  log.push(saved && saved.ok ? { id, status: "downloaded", bytes: saved.bytes, via: saved.note, summon: viaSummon && viaSummon.t, summonAria: viaSummon && viaSummon.aria }
                             : { id, status: "no_pdf_reached", url: page.url, authed: page.authed, err: saved && saved.error });
  await closeTab(sTab.id); await closeTab(pTab.id); await sleep(1000);
}
fs.writeFileSync(path.join(OUT, "batch_run_log_" + ids.join("_") + ".json"), JSON.stringify(log, null, 2), "utf8");
const byStatus = {}; for (const l of log) byStatus[l.status] = (byStatus[l.status] || 0) + 1;
console.log("\n=== summary:", JSON.stringify(byStatus));
