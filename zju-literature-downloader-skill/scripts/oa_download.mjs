import fs from "node:fs";
import path from "node:path";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36";
import { ROOT as OUT, PDFS, ARCHIVE_LOGS } from "./paths.mjs";
const OUTUNUSED = 0;
fs.mkdirSync(PDFS, { recursive: true });
const oa = JSON.parse(fs.readFileSync(path.join(ARCHIVE_LOGS, "openalex.json"), "utf8"));

const META = {
  1:["Liang_2010_NatPhoton_lasers_on_silicon"], 2:["Komljenovic_2016_JLT_heterogeneous_silicon_PICs"],
  3:["Xiang_2022_JSTQE_high_performance_silicon_photonics"], 4:["Margalit_2021_APL_future_of_silicon_photonics"],
  5:["Wan_2025_NatRevEE_silicon_photonics_with_CMOS"], 6:["Smit_2019_APLPhoton_InP_photonic_integration"],
  7:["Heck_2013_JSTQE_hybrid_silicon_PIC"], 8:["Jones_2019_IEEENanoMag_InP_silicon_transceivers"],
  9:["Corbett_2017_PQE_transfer_print_heterogeneous_integration"], 10:["Zhu_2021_AOP_integrated_photonics_on_TFLN"],
  11:["Boes_2023_Science_LN_photonics_unlocking_spectrum"], 12:["Wang_2018_Nature_LN_modulators_CMOS_voltages"],
  13:["Zhang_2021_Optica_LN_modulators_performance_scalability"], 14:["Wang_2024_Nature_lithium_tantalate_PICs"],
  15:["Feng_2024_Nature_LN_microwave_photonic_engine"], 16:["Marpaung_2019_NatPhoton_integrated_microwave_photonics"],
  17:["Liu_2020_AOP_integrated_microwave_photonic_filters"], 18:["Pan_2020_JLT_microwave_photonic_radars"],
  19:["Li_2016_FOE_recent_advances_microwave_photonics"], 20:["Stojanovic_2018_OE_monolithic_silicon_photonic_CMOS_SOI"],
  21:["Atabaki_2018_Nature_photonics_with_silicon_nanoelectronics"],
  23:["Gaeta_2019_NatPhoton_photonic_chip_frequency_combs"],
  24:["Shen_2017_NatPhoton_deep_learning_coherent_nanophotonic"],
  25:["Shastri_2021_NatPhoton_photonics_for_AI_neuromorphic"],
  26:["Mahajan_2022_JLT_co_packaged_photonics_HPC"], 27:["Fathololoumi_2021_JLT_1.6Tbps_silicon_photonic_engine"],
  28:["Soref_2006_JSTQE_past_present_future_silicon_photonics"],
  29:["Xu_2022_Optica_dual_polarization_TFLN_IQ_modulators"]
};

// extra candidate URLs to try (arXiv preprints, repository copies, known OA file paths)
const EXTRA = {
  6:["https://pure.tue.nl/ws/files/128418711/1.5087862.pdf","https://pubs.aip.org/aip/app/article-pdf/doi/10.1063/1.5087862/15532045/050901_1_online.pdf"],
  7:["https://escholarship.org/content/qt92h2804z/qt92h2804z.pdf"],
  10:["https://arxiv.org/pdf/2102.11956","https://opg.optica.org/aop/viewmedia.cfm?uri=aop-13-2-242&seq=0"],
  13:["https://opg.optica.org/optica/viewmedia.cfm?uri=optica-8-5-652&seq=0"],
  15:["https://scholars.cityu.edu.hk/files/236798147/221456013.pdf"],
  18:["https://ieeexplore.ieee.org/ielx7/50/9194960/09089228.pdf"],
  20:["https://opg.optica.org/oe/viewmedia.cfm?uri=oe-26-10-13106&seq=0"],
  21:["https://www.nature.com/articles/s41586-018-0028-z.pdf"],
  23:["https://www.nature.com/articles/s41566-019-0358-x.pdf"],
  24:["https://arxiv.org/pdf/1610.02365","https://www.nature.com/articles/nphoton.2017.93.pdf"],
  25:["https://arxiv.org/pdf/2011.00111","https://www.nature.com/articles/s41566-020-00754-y.pdf"],
  29:["https://opg.optica.org/optica/viewmedia.cfm?uri=optica-9-1-61&seq=0"],
  16:["https://www.nature.com/articles/s41566-018-0310-5.pdf"],
  4:["https://pubs.aip.org/aip/apl/article-pdf/doi/10.1063/5.0050117/14511449/220501_1_online.pdf"],
  3:["https://ieeexplore.ieee.org/ielx7/2944/9519571/09609553.pdf"]
};

async function tryFetch(url, ms = 60000) {
  const ac = new AbortController(); const t = setTimeout(() => ac.abort(), ms);
  try { return await fetch(url, { signal: ac.signal, redirect: "follow", headers: { "user-agent": UA, accept: "application/pdf,text/html;q=0.9,*/*;q=0.8" } }); }
  finally { clearTimeout(t); }
}

function extractPdfLinks(html, baseUrl) {
  const links = [];
  const re = /(?:href|content|src)\s*=\s*["']([^"']+)["']/gi;
  let m;
  while ((m = re.exec(html))) {
    const u = m[1];
    if (/\.pdf(\?|#|$)/i.test(u) || /\/pdf\//i.test(u) || /pdfdirect|viewmedia|article-pdf|stamp\.jsp/i.test(u)) {
      try { links.push(new URL(u, baseUrl).href); } catch {}
    }
  }
  return [...new Set(links)];
}

const report = [];
for (const [id, [slug]] of Object.entries(META)) {
  const outFile = path.join(PDFS, slug + ".pdf");
  if (fs.existsSync(outFile) && fs.statSync(outFile).size > 50000) { report.push({ id, slug, status: "already_have" }); console.log(`[${id}] already present`); continue; }
  const v = oa[id] || {};
  const cands = [...new Set([...(EXTRA[id] || []), ...((v.locs || []).map(l => l.url).filter(Boolean)), v.doi ? `https://doi.org/${v.doi}` : null].filter(Boolean))];
  let done = false;
  const tried = [];
  for (const url of cands) {
    try {
      const r = await tryFetch(url);
      const ct = (r.headers.get("content-type") || "").toLowerCase();
      if (ct.includes("pdf")) {
        const buf = Buffer.from(await r.arrayBuffer());
        if (buf.subarray(0, 5).toString("latin1") !== "%PDF-") { tried.push(`${url} -> ct pdf but bad magic`); continue; }
        fs.writeFileSync(outFile, buf);
        const pages = (buf.toString("latin1").match(/\/Type\s*\/Page[^s]/g) || []).length;
        report.push({ id, slug, status: "downloaded_oa", url, bytes: buf.length, pages });
        console.log(`[${id}] OA OK ${buf.length} B pages=${pages} <- ${url}`);
        done = true; break;
      }
      if (ct.includes("html")) {
        const html = await r.text();
        const links = extractPdfLinks(html, url).slice(0, 3);
        tried.push(`${url} -> html(${html.length}) pdfLinks=${links.length}`);
        for (const l of links) {
          if (/\/(login|signin|purchase|cart)/i.test(l)) continue;
          try {
            const r2 = await tryFetch(l);
            const ct2 = (r2.headers.get("content-type") || "").toLowerCase();
            if (!ct2.includes("pdf")) { tried.push(`${l} -> ${r2.status} ${ct2.slice(0,30)}`); continue; }
            const buf2 = Buffer.from(await r2.arrayBuffer());
            if (buf2.subarray(0, 5).toString("latin1") !== "%PDF-") { tried.push(`${l} -> bad magic`); continue; }
            fs.writeFileSync(outFile, buf2);
            const pages2 = (buf2.toString("latin1").match(/\/Type\s*\/Page[^s]/g) || []).length;
            report.push({ id, slug, status: "downloaded_oa_followed", url: l, bytes: buf2.length, pages: pages2, via: url });
            console.log(`[${id}] OA OK (followed) ${buf2.length} B pages=${pages2} <- ${l}`);
            done = true; break;
          } catch (e) { tried.push(`${l} -> ERR ${e.message}`); }
        }
        if (done) break;
      } else {
        tried.push(`${url} -> ${r.status} ${ct.slice(0, 30)}`);
      }
    } catch (e) { tried.push(`${url} -> ERR ${e.message}`); }
  }
  if (!done) { report.push({ id, slug, status: "oa_failed", tried }); console.log(`[${id}] OA failed: ${tried.join(" | ").slice(0, 200)}`); }
  await new Promise(r => setTimeout(r, 400));
}
fs.writeFileSync(path.join(ARCHIVE_LOGS, "oa_report.json"), JSON.stringify(report, null, 2), "utf8");
const ok = report.filter(r => String(r.status).startsWith("downloaded") || r.status === "already_have").length;
console.log(`\n=== OA downloaded: ${ok}/${report.length}`);
