// ieee-download.mjs — IEEE Xplore via stampPDF/getPDF.jsp using the browser's institutional session.
import fs from "node:fs";
import path from "node:path";
import { evaluate, listTargets, fetchPdfInPage } from "./cdp.mjs";
import { ROOT as OUT, PDFS } from "./paths.mjs";
const BASE = "http://127.0.0.1:9222";
const IEEE = {
  2:["Komljenovic_2016_JLT_heterogeneous_silicon_PICs","10.1109/JLT.2015.2465382"],
  3:["Xiang_2022_JSTQE_high_performance_silicon_photonics","10.1109/JSTQE.2021.3126124"],
  8:["Jones_2019_IEEENanoMag_InP_silicon_transceivers","10.1109/MNANO.2019.2891369"],
  18:["Pan_2020_JLT_microwave_photonic_radars","10.1109/JLT.2020.2993166"],
  26:["Mahajan_2022_JLT_co_packaged_photonics_HPC","10.1109/JLT.2021.3104725"],
  27:["Fathololoumi_2021_JLT_1.6Tbps_silicon_photonic_engine","10.1109/JLT.2020.3039218"],
  28:["Soref_2006_JSTQE_past_present_future_silicon_photonics","10.1109/JSTQE.2006.883151"]
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const newTab = async url => (await fetch(`${BASE}/json/new?` + encodeURIComponent(url), { method: "PUT" })).json();
const closeTab = async id => { try { await fetch(`${BASE}/json/close/${id}`); } catch {} };
const log = [];
for (const [id, [slug, doi]] of Object.entries(IEEE)) {
  const outFile = path.join(PDFS, slug + ".pdf");
  if (fs.existsSync(outFile)) { console.log(`[${id}] have`); continue; }
  console.log(`\n=== [${id}] ${slug}`);
  const t = await newTab("https://doi.org/" + doi);
  let arnumber = null, title = "";
  for (let i = 0; i < 4 && !arnumber; i++) {
    await sleep(i === 0 ? 10000 : 6000);
    try {
      const p = JSON.parse(await evaluate(t.id, `JSON.stringify({url:location.href, title:document.title, arn:(document.body.innerHTML.match(/arnumber[=\\"':]+(\\d{6,9})/)||[])[1]||null, denied:/denied/i.test(location.href), access:/Access provided by/i.test(document.body.innerText), zju:/Zhejiang/i.test(document.body.innerText)})`));
      console.log(`   +${i}: ${p.url.slice(0,70)} arn=${p.arn} denied=${p.denied} zju=${p.zju}`);
      title = p.title; arnumber = p.arn || (p.url.match(/document\/(\d{6,9})/) || [])[1] || null;
    } catch (e) { console.log("   read err", e.message); }
  }
  if (!arnumber) { log.push({ id, status: "no_arnumber", title }); console.log("   -> cannot resolve arnumber"); await closeTab(t.id); continue; }
  const got = await evaluate(t.id, `(async()=>{ try { const r = await fetch("https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=${arnumber}&ref=", {credentials:"include"}); const b = new Uint8Array(await r.arrayBuffer()); window.__ieeeBytes = b; return JSON.stringify({status:r.status, ct:r.headers.get("content-type"), n:b.length, head:String.fromCharCode(...b.slice(0,5))}); } catch(e){ return JSON.stringify({err:String(e)}); } })()`);
  const info = JSON.parse(got);
  console.log("   getPDF:", JSON.stringify(info).slice(0, 140));
  if (info.head === "%PDF-") {
    const CH = 400000; fs.mkdirSync(path.dirname(outFile), { recursive: true });
    const fd = fs.openSync(outFile, "w");
    try { for (let off = 0; off < info.n; off += CH) {
      const b64 = await evaluate(t.id, `(()=>{ const s = window.__ieeeBytes.subarray(${off}, ${off + CH}); let b=""; for (let i=0;i<s.length;i++) b+=String.fromCharCode(s[i]); return btoa(b); })()`);
      fs.writeSync(fd, Buffer.from(b64, "base64"));
      process.stdout.write(`\r   ${Math.min(off + CH, info.n)}/${info.n}`);
    } } finally { process.stdout.write("\n"); fs.closeSync(fd); }
    const bytes = fs.statSync(outFile).size;
    console.log(`   SAVED ${(bytes/1e6).toFixed(2)} MB (arnumber=${arnumber})`);
    log.push({ id, status: "downloaded", bytes, arnumber, title });
  } else { log.push({ id, status: "getpdf_failed", info, title }); }
  await closeTab(t.id); await sleep(1000);
}
fs.writeFileSync(path.join(OUT, "ieee_run_log.json"), JSON.stringify(log, null, 2), "utf8");
const s = {}; for (const l of log) s[l.status] = (s[l.status] || 0) + 1;
console.log("\n=== ieee summary:", JSON.stringify(s));
