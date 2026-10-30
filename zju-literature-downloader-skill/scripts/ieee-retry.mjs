import fs from "node:fs";
import path from "node:path";
import { evaluate } from "./cdp.mjs";
import { ROOT as OUT, PDFS } from "./paths.mjs";
const BASE = "http://127.0.0.1:9222";
const sleep = ms => new Promise(r => setTimeout(r, ms));
const newTab = async url => (await fetch(`${BASE}/json/new?` + encodeURIComponent(url), { method: "PUT" })).json();
const closeTab = async id => { try { await fetch(`${BASE}/json/close/${id}`); } catch {} };
const log = [];
const RETRY = {
  26:["Mahajan_2022_JLT_co_packaged_photonics_HPC","10.1109/JLT.2021.3104725",9513605],
  27:["Fathololoumi_2021_JLT_1.6Tbps_silicon_photonic_engine","10.1109/JLT.2020.3039218",9264635],
  28:["Soref_2006_JSTQE_past_present_future_silicon_photonics","10.1109/JSTQE.2006.883151",4032698]
};
async function stream(tabId, total, outFile) {
  const CH = 400000; const fd = fs.openSync(outFile, "w");
  try { for (let off = 0; off < total; off += CH) {
    const b64 = await evaluate(tabId, `(()=>{ const s = window.__b.subarray(${off}, ${off+CH}); let x=""; for (let i=0;i<s.length;i++) x+=String.fromCharCode(s[i]); return btoa(x); })()`);
    fs.writeSync(fd, Buffer.from(b64, "base64"));
  } } finally { fs.closeSync(fd); }
  return fs.statSync(outFile).size;
}
for (const [id, [slug, doi, arn]] of Object.entries(RETRY)) {
  const outFile = path.join(PDFS, slug + ".pdf");
  if (fs.existsSync(outFile)) { console.log(`[${id}] have`); continue; }
  console.log(`\n=== [${id}] ${slug}`);
  const t = await newTab("https://doi.org/" + doi);
  await sleep(11000);
  const info = JSON.parse(await evaluate(t.id, `(async()=>{
    const n = ${arn};
    for (const u of [
      "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=" + n + "&ref=",
      "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=" + n,
      "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=" + n
    ]) {
      try { const r = await fetch(u, {credentials:"include"}); const b = new Uint8Array(await r.arrayBuffer());
        if (String.fromCharCode(...b.slice(0,5)) === "%PDF-") { window.__b = b; return JSON.stringify({ok:true, u, n:b.length}); }
      } catch(e) {}
    }
    return JSON.stringify({ok:false});
  })()`));
  console.log("   ", JSON.stringify(info).slice(0,120));
  if (info.ok) { const bytes = await stream(t.id, info.n, outFile); console.log(`   SAVED ${(bytes/1e6).toFixed(2)} MB via getPDF-retry`); log.push({ id, status: "downloaded", bytes }); }
  else { log.push({ id, status: "still_blocked", doi }); }
  await closeTab(t.id);
  // also try clicking the in-page PDF button and grabbing any resulting PDF blob
  if (!info.ok) {
    const t2 = await newTab("https://ieeexplore.ieee.org/document/" + arn);
    await sleep(12000);
    const r = JSON.parse(await evaluate(t2.id, `(async()=>{
      const patched = window.fetch;
      window.__blobs = [];
      window.fetch = async function(...a) { const res = await patched.apply(this, a); try { if (/pdf/i.test(String(a[0]))) { const c = res.clone(); const b = new Uint8Array(await c.arrayBuffer()); if (String.fromCharCode(...b.slice(0,5)) === "%PDF-") window.__blobs.push(b); } } catch(e){} return res; };
      const el = [...document.querySelectorAll("a,button")].find(x => (x.textContent||"").trim() === "PDF" || /download pdf/i.test(x.getAttribute("aria-label")||""));
      if (el) el.click();
      await new Promise(r => setTimeout(r, 9000));
      if (window.__blobs.length) { window.__b = window.__blobs[0]; return JSON.stringify({ok:true, n:window.__b.length}); }
      return JSON.stringify({ok:false});
    })()`));
    console.log("   click-capture:", JSON.stringify(r));
    if (r.ok) { const bytes = await stream(t2.id, r.n, outFile); console.log(`   SAVED ${(bytes/1e6).toFixed(2)} MB via click-capture`); log.push({ id, status: "downloaded", bytes }); }
    await closeTab(t2.id);
  }
}
fs.writeFileSync(path.join(OUT, "ieee_retry_log.json"), JSON.stringify(log, null, 2), "utf8");
console.log("\n=== ieee retry:", JSON.stringify(log.map(l => [l.id, l.status])));
