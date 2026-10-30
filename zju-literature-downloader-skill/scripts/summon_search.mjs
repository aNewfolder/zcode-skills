import { listTargets, navigate, evaluate } from "./cdp.mjs";
const tabs = await listTargets();
const sum = tabs.find(x => x.url.includes("summon"));
const query = process.argv.slice(2).join(" ") || "10.1038/xxxx/example";
const url = "https://zju.summon.serialssolutions.com/search?#!/search?pn=1&ho=t&include.ft.matches=f&l=en&q=" + encodeURIComponent(query);
console.log("navigating to:", url);
const r = await navigate(sum.id, url, { waitMs: 9000 });
console.log("page:", JSON.stringify(r.info));
for (let i = 0; i < 3; i++) {
  const res = await evaluate(sum.id, `JSON.stringify({
    n: document.querySelectorAll("a").length,
    hits: [...document.querySelectorAll("a")].map(a => ({t:(a.textContent||"").replace(/\\s+/g," ").trim(), h:a.getAttribute("href")||"", aria:a.getAttribute("aria-label")||""}))
      .filter(x => /pdf|full text|在线全文|全文/i.test(x.t + " " + x.aria))
      .slice(0, 12),
    titles: [...document.querySelectorAll("h2, .title, [class*=title]")].map(e => (e.textContent||"").replace(/\\s+/g," ").trim()).filter(Boolean).slice(0,6),
    bodyHas: ["Are you a robot","not authorized","outside the authorized","No results","结果"].filter(k => document.body.innerText.includes(k))
  })`);
  const parsed = JSON.parse(res);
  console.log(`\n--- attempt ${i + 1}: links=${parsed.n} pdfish=${parsed.hits.length} markers=${JSON.stringify(parsed.bodyHas)}`);
  console.log("titles:", JSON.stringify(parsed.titles));
  for (const h of parsed.hits) console.log(`   [${h.t}] aria=${h.aria} -> ${h.h.slice(0, 150)}`);
  if (parsed.hits.length) break;
  await new Promise(x => setTimeout(x, 4000));
}
