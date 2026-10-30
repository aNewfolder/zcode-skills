import fs from "node:fs";
import path from "node:path";
import { evaluate, fetchPdfInPage } from "./cdp.mjs";
import { ROOT as OUT, PDFS, SI } from "./paths.mjs";
const BASE = "http://127.0.0.1:9222";
fs.mkdirSync(SI, { recursive: true });
const sleep = ms => new Promise(r => setTimeout(r, ms));
const newTab = async url => (await fetch(`${BASE}/json/new?` + encodeURIComponent(url), { method: "PUT" })).json();
const closeTab = async id => { try { await fetch(`${BASE}/json/close/${id}`); } catch {} };

// Papers whose article page advertises supplements
const SI_TARGETS = [
  { id: 11, slug: "Boes_2023_Science_LN_photonics_unlocking_spectrum", url: "https://www.science.org/doi/10.1126/science.abj4396", want: /suppl|sm\.pdf/i },
  { id: 14, slug: "Wang_2024_Nature_lithium_tantalate_PICs", url: "https://www.nature.com/articles/s41586-024-07234-1", want: /MOESM|supplement|Reporting Summary|Peer Review/i },
  { id: 15, slug: "Feng_2024_Nature_LN_microwave_photonic_engine", url: "https://www.nature.com/articles/s41586-024-07078-9", want: /MOESM|supplement|Reporting Summary|Peer Review/i },
  { id: 22, slug: "Xiang_2021_Science_laser_soliton_microcombs_on_silicon", url: "https://www.science.org/doi/10.1126/science.abh2076", want: /suppl|sm\.pdf/i },
  { id: 12, slug: "Wang_2018_Nature_LN_modulators_CMOS_voltages", url: "https://www.nature.com/articles/s41586-018-0551-y", want: /MOESM|supplement/i }
];
const found = [];
for (const t of SI_TARGETS) {
  console.log(`\n=== SI [${t.id}] ${t.slug}`);
  const tab = await newTab(t.url);
  await sleep(11000);
  const links = JSON.parse(await evaluate(tab.id, `JSON.stringify([...document.querySelectorAll("a")].map(a => ({t:(a.textContent||"").replace(/\\s+/g," ").trim().slice(0,45), h:a.href||""})).filter(x => x.h && ${t.want}.test(x.h + " " + x.t)).slice(0, 6))`));
  console.log(`   candidates: ${links.map(l => l.t + " -> " + l.h.slice(0, 70)).join(" | ") || "none"}`);
  let n = 0;
  for (const l of links) {
    const ext = (l.h.match(/\.(pdf|docx|xlsx|zip|csv)(\?|#|$)/i) || [])[1] || "pdf";
    const out = path.join(SI, t.slug + "_SI" + (n ? "_" + (n + 1) : "") + "." + ext.toLowerCase());
    if (fs.existsSync(out)) { n++; continue; }
    try {
      if (ext.toLowerCase() === "pdf") { const r = await fetchPdfInPage(tab.id, l.h, out, { allowNonPdf: false }); console.log(`   SI SAVED ${(r.bytes/1e6).toFixed(2)} MB <- ${l.t}`); }
      else { const rr = await evaluate(tab.id, `(async()=>{ const r = await fetch(${JSON.stringify(l.h)}, {credentials:"include"}); const b = new Uint8Array(await r.arrayBuffer()); if (b.length > 3e7) return null; let s=""; for (let i=0;i<b.length;i+=65536) s += btoa(String.fromCharCode(...b.subarray(i, i+65536))); return s; })()`);
        if (rr) { fs.writeFileSync(out, Buffer.from(rr, "base64")); console.log(`   SI SAVED (non-pdf) <- ${l.t}`); } }
      found.push({ id: t.id, slug: t.slug, title: l.t, url: l.h, file: "SupportingInformation/" + path.basename(out) });
      n++;
    } catch (e) { console.log(`   SI x ${l.t}: ${e.message.slice(0, 80)}`); }
  }
  await closeTab(tab.id);
}
fs.writeFileSync(path.join(OUT, "si_report.json"), JSON.stringify(found, null, 2), "utf8");
console.log(`\n=== SI collected: ${found.length}`);
