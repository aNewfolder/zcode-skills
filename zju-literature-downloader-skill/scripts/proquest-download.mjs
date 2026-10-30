// proquest-download.mjs — capture the PDF bytes produced by ProQuest's own Download action.
import fs from "node:fs";
import path from "node:path";
import { evaluate } from "./cdp.mjs";
import { ROOT as OUT, PDFS } from "./paths.mjs";
const BASE = "http://127.0.0.1:9222";
const ITEMS = {
  12:["Wang_2018_Nature_LN_modulators_CMOS_voltages","2123610320"],
  21:["Atabaki_2018_Nature_photonics_with_silicon_nanoelectronics","2056021658"],
  15:["Feng_2024_Nature_LN_microwave_photonic_engine","2954922315"],
  23:["Gaeta_2019_NatPhoton_photonic_chip_frequency_combs",null],
  1:["Liang_2010_NatPhoton_lasers_on_silicon",null],
  16:["Marpaung_2019_NatPhoton_integrated_microwave_photonics",null]
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const newTab = async url => (await fetch(`${BASE}/json/new?` + encodeURIComponent(url), { method: "PUT" })).json();
const closeTab = async id => { try { await fetch(`${BASE}/json/close/${id}`); } catch {} };
const log = [];

async function tryProQuest(id, slug, docid) {
  const url = docid ? `https://www.proquest.com/docview/${docid}/fulltextPDF?accountid=15198`
                    : null;
  if (!url) return { ok: false, error: "no docid" };
  const t = await newTab(url);
  await sleep(13000);
  // Instrument the page to capture the PDF that ProQuest's download action produces.
  await evaluate(t.id, `(() => {
    if (window.__pAttached) return "already";
    window.__pCaptures = [];
    const OrigXHR = window.XMLHttpRequest;
    window.XMLHttpRequest = function () {
      const x = new OrigXHR(); const open = x.open;
      x.open = function (m, u, ...r) { x.__u = u; return open.call(x, m, u, ...r); };
      const send = x.send;
      x.send = function (...a) {
        x.addEventListener("load", () => {
          try { if (/pdf|download/i.test(x.__u || "") || x.responseType === "arraybuffer") {
            const b = x.responseType === "arraybuffer" ? new Uint8Array(x.response) : null;
            if (b && b.length > 10000 && String.fromCharCode(...b.slice(0,5)) === "%PDF-") window.__pCaptures.push({ u: x.__u, n: b.length, b64: null });
          } } catch (e) {}
        });
        return send.apply(x, a);
      };
      return x;
    };
    window.__pAttached = true;
    return "attached";
  })()`);
  // Find and click anything that looks like a download control
  const found = await evaluate(t.id, `JSON.stringify({
    controls: [...document.querySelectorAll("a,button,[role=button]")].map(e => ({ t:(e.textContent||"").replace(/\\s+/g," ").trim().slice(0,30), aria:e.getAttribute("aria-label")||"", title:e.getAttribute("title")||"", cls:(e.className||"").toString().slice(0,50) }))
      .filter(x => /下载|download|pdf|保存/i.test(x.t + " " + x.aria + " " + x.title + " " + x.cls)).slice(0, 10)
  })`);
  console.log("   controls:", found.slice(0, 300));
  const clicked = await evaluate(t.id, `(() => {
    const els = [...document.querySelectorAll("a,button,[role=button]")];
    const e = els.find(x => /下载|download/i.test((x.textContent||"") + (x.getAttribute("aria-label")||"") + (x.getAttribute("title")||"")));
    if (!e) return "none";
    e.click(); return "clicked:" + (e.textContent||"").trim().slice(0,25);
  })()`);
  console.log("   click:", clicked);
  await sleep(9000);
  const caps = await evaluate(t.id, `JSON.stringify((window.__pCaptures||[]).map(c => ({u:(c.u||"").slice(0,90), n:c.n})))`);
  console.log("   captures:", caps);
  // fetch the captured url in-page with responseType arraybuffer, then stream out
  const info = await evaluate(t.id, `(async()=>{
    const cands = [...new Set([...(window.__pCaptures||[]).map(c=>c.u), ...performance.getEntriesByType("resource").map(r=>r.name).filter(n=>/pdf/i.test(n))])];
    for (const u of cands) {
      try { const r = await fetch(u, {credentials:"include"}); const ab = await r.arrayBuffer(); const b = new Uint8Array(ab);
        if (String.fromCharCode(...b.slice(0,5)) === "%PDF-") { window.__pqBytes = b; return JSON.stringify({ok:true, u:u.slice(0,90), n:b.length}); }
      } catch(e) {}
    }
    return JSON.stringify({ok:false, cands: cands.length});
  })()`);
  console.log("   capture fetch:", info);
  const parsed = JSON.parse(info);
  if (!parsed.ok) { await closeTab(t.id); return { ok: false, error: "no captured pdf" }; }
  const CH = 400000; fs.mkdirSync(path.dirname(path.join(PDFS, slug + ".pdf")), { recursive: true });
  const outFile = path.join(PDFS, slug + ".pdf");
  const fd = fs.openSync(outFile, "w");
  try { for (let off = 0; off < parsed.n; off += CH) {
    const b64 = await evaluate(t.id, `(()=>{ const s = window.__pqBytes.subarray(${off}, ${off+CH}); let b=""; for (let i=0;i<s.length;i++) b+=String.fromCharCode(s[i]); return btoa(b); })()`);
    fs.writeSync(fd, Buffer.from(b64, "base64"));
  } } finally { fs.closeSync(fd); }
  await closeTab(t.id);
  const bytes = fs.statSync(outFile).size;
  console.log(`   SAVED ${(bytes/1e6).toFixed(2)} MB from ProQuest`);
  return { ok: true, bytes, url: parsed.u };
}

for (const [id, [slug, docid]] of Object.entries(ITEMS)) {
  const outFile = path.join(PDFS, slug + ".pdf");
  if (fs.existsSync(outFile)) { console.log(`[${id}] have`); continue; }
  console.log(`\n=== [${id}] ${slug}`);
  const r = await tryProQuest(id, slug, docid);
  log.push({ id, slug, ...r });
}
fs.writeFileSync(path.join(OUT, "proquest_run_log.json"), JSON.stringify(log, null, 2), "utf8");
console.log("\n=== proquest summary:", JSON.stringify(log.map(l => [l.id, l.ok ? "ok" : "fail"])));
