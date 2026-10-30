import fs from "node:fs";
import path from "node:path";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";
import { ROOT as OUT, PDFS, ARCHIVE_LOGS } from "./paths.mjs";
const OUTUNUSED = 0;
fs.mkdirSync(PDFS, { recursive: true });

const DOIS = {
  1:"10.1038/nphoton.2010.167", 2:"10.1109/JLT.2015.2465382", 3:"10.1109/JSTQE.2021.3126124",
  4:"10.1063/5.0050117", 5:"10.1038/s44287-025-00223-0", 6:"10.1063/1.5087862",
  7:"10.1109/JSTQE.2012.2235413", 8:"10.1109/MNANO.2019.2891369", 9:"10.1016/j.pquantelec.2017.01.001",
  10:"10.1364/AOP.411024", 11:"10.1126/science.abj4396", 12:"10.1038/s41586-018-0551-y",
  13:"10.1364/OPTICA.415762", 14:"10.1038/s41586-024-07234-1", 15:"10.1038/s41586-024-07078-9",
  16:"10.1038/s41566-018-0310-5", 17:"10.1364/AOP.382548", 18:"10.1109/JLT.2020.2993166",
  19:"10.1007/s12200-016-0633-0", 20:"10.1364/OE.26.013106", 21:"10.1038/s41586-018-0028-z",
  23:"10.1038/s41566-019-0358-x", 24:"10.1038/nphoton.2017.93", 25:"10.1038/s41566-020-00754-y",
  26:"10.1109/JLT.2021.3104725", 27:"10.1109/JLT.2020.3039218", 28:"10.1109/JSTQE.2006.883151",
  29:"10.1364/OPTICA.449691"
};

const out = {};
for (const [id, doi] of Object.entries(DOIS)) {
  try {
    const r = await fetch(`https://api.openalex.org/works/doi:${doi}?mailto=lit@example.com`, { headers: { "user-agent": UA }, signal: AbortSignal.timeout(25000) });
    if (!r.ok) { out[id] = { error: `openalex ${r.status}` }; console.log(`[${id}] openalex ${r.status}`); continue; }
    const w = await r.json();
    const locs = [];
    if (w.best_oa_location) locs.push({ src: "best_oa", url: w.best_oa_location.pdf_url || w.best_oa_location.landing_page_url, host: w.best_oa_location.source && w.best_oa_location.source.display_name, lic: w.best_oa_location.license });
    for (const l of (w.locations || [])) if (l.is_oa && (l.pdf_url || l.landing_page_url)) locs.push({ src: "location", url: l.pdf_url || l.landing_page_url, host: l.source && l.source.display_name, lic: l.license });
    out[id] = { doi, oa: !!w.open_access?.is_oa, oa_status: w.open_access?.oa_status, title: w.title, locs: locs.slice(0, 4) };
    console.log(`[${id}] oa=${w.open_access?.is_oa} status=${w.open_access?.oa_status} pdfs=${locs.filter(l=>/\.pdf|\/pdf/i.test(l.url||"")).length}`);
  } catch (e) { out[id] = { error: String(e.message) }; console.log(`[${id}] ERR ${e.message}`); }
  await new Promise(r => setTimeout(r, 250));
}
fs.writeFileSync(path.join(ARCHIVE_LOGS, "openalex.json"), JSON.stringify(out, null, 2), "utf8");
console.log("\n--- OA candidates with direct pdf url ---");
for (const [id, v] of Object.entries(out)) for (const l of (v.locs || [])) if (/\.pdf(\?|$)|\/pdf\//i.test(l.url || "")) console.log(`${id}\t${l.host}\t${l.url}`);
