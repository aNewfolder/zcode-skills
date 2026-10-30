#!/usr/bin/env node
// cdp.mjs - minimal Chrome/Edge DevTools Protocol client for the zju-literature-downloader-skill workflow.
// Talks straight to the browser's --remote-debugging-port (default 9222); no web-access proxy needed.
// CLI: node cdp.mjs list | version | open <url> | info <targetId> | eval <targetId> <js> | pdf <targetId> <url> <out>
import fs from "node:fs";
import path from "node:path";

export const BASE = process.env.CDP_PORT ? `http://127.0.0.1:${process.env.CDP_PORT}` : "http://127.0.0.1:9222";

export async function httpJson(url, opts = {}, ms = 20000) {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), ms);
  try {
    const r = await fetch(url, { ...opts, signal: ac.signal });
    if (!r.ok) throw new Error(`HTTP ${r.status} ${url}`);
    return await r.json();
  } finally { clearTimeout(t); }
}

export async function version() { return httpJson(`${BASE}/json/version`, {}, 5000); }

export async function listTargets() {
  const list = await httpJson(`${BASE}/json/list`);
  return (list || []).filter(t => t.type === "page");
}

// Resolve a target's real websocket URL from the target list. Newly created PDF tabs
// often are not reachable via /devtools/page/<id>, so always prefer this lookup.
export async function pageWsUrl(targetId) {
  const list = await httpJson(`${BASE}/json/list`);
  const hit = (list || []).find(t => t.id === targetId && t.webSocketDebuggerUrl);
  if (hit) return hit.webSocketDebuggerUrl;
  return `ws://127.0.0.1:${new URL(BASE).port}/devtools/page/${targetId}`;
}

export class CDP {
  constructor(wsUrl) { this.wsUrl = wsUrl; this.id = 0; this.pending = new Map(); this.ws = null; }
  async connect() {
    this.ws = new WebSocket(this.wsUrl);
    await new Promise((res, rej) => {
      const to = setTimeout(() => rej(new Error("ws connect timeout")), 15000);
      this.ws.onopen = () => { clearTimeout(to); res(); };
      this.ws.onerror = () => { clearTimeout(to); rej(new Error("ws error")); };
    });
    this.ws.onmessage = (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.id && this.pending.has(msg.id)) {
        const { res, rej } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? rej(new Error(JSON.stringify(msg.error))) : res(msg.result);
      }
    };
    return this;
  }
  send(method, params = {}, ms = 30000) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => { if (this.pending.has(id)) { this.pending.delete(id); rej(new Error(`timeout ${method}`)); } }, ms);
    });
  }
  close() { try { this.ws && this.ws.close(); } catch {} }
}

export async function newPage(url) {
  const enc = encodeURIComponent(url);
  let t;
  try { t = await httpJson(`${BASE}/json/new?${enc}`, { method: "PUT" }); }
  catch { t = await httpJson(`${BASE}/json/new?${enc}`); }
  return t;
}

export async function evaluate(targetId, expression, { awaitPromise = true, timeout = 60000, retries = 4 } = {}) {
  const cdp = await new CDP(await pageWsUrl(targetId)).connect();
  try {
    await cdp.send("Runtime.enable");
    let lastErr;
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        const r = await cdp.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise, timeout }, timeout + 5000);
        if (r.exceptionDetails) throw new Error("page exception: " + JSON.stringify(r.exceptionDetails).slice(0, 400));
        return r.result ? r.result.value : undefined;
      } catch (e) {
        lastErr = e;
        if (/execution context|Cannot find context|inspected target navigated|ws error|timeout Runtime.enable/i.test(e.message) && attempt < retries) {
          await new Promise(r => setTimeout(r, 2000));
          continue;
        }
        throw e;
      }
    }
    throw lastErr;
  } finally { cdp.close(); }
}

export async function navigate(targetId, url, { waitMs = 6000, timeout = 60000 } = {}) {
  const cdp = await new CDP(await pageWsUrl(targetId)).connect();
  try {
    await cdp.send("Page.enable");
    await cdp.send("Page.navigate", { url });
    const deadline = Date.now() + timeout;
    let ready = false;
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 700));
      const st = await cdp.send("Runtime.evaluate", { expression: "document.readyState", returnByValue: true });
      if (st.result && st.result.value === "complete") { ready = true; break; }
    }
    if (waitMs) await new Promise(r => setTimeout(r, waitMs));
    const info = await cdp.send("Runtime.evaluate", {
      expression: "JSON.stringify({url:location.href,title:document.title,ready:document.readyState,ct:document.contentType})",
      returnByValue: true
    });
    return { ready, info: JSON.parse(info.result.value) };
  } finally { cdp.close(); }
}

// Download from the page's own authenticated context.
export async function fetchPdfInPage(targetId, url, outPath, { allowNonPdf = false, chunk = 400000 } = {}) {
  const init = await evaluate(targetId, `(async()=>{
    const r = await fetch(${JSON.stringify(url)}, { credentials: "include", redirect: "follow" });
    const ab = await r.arrayBuffer();
    window.__zjuBytes = new Uint8Array(ab);
    return { ok: r.ok, status: r.status, ct: r.headers.get("content-type") || "", n: window.__zjuBytes.length,
             head: Array.from(window.__zjuBytes.slice(0, 8)) };
  })()`);
  if (!init || typeof init.n !== "number") throw new Error("page fetch returned no bytes: " + JSON.stringify(init));
  const head = Buffer.from(init.head || []).toString("latin1");
  if (!init.ok) throw new Error(`page fetch HTTP ${init.status} (${init.n} bytes)`);
  if (!head.startsWith("%PDF") && !allowNonPdf) throw new Error(`not a PDF (ct=${init.ct}, head=${JSON.stringify(head)})`);
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  const fd = fs.openSync(outPath, "w");
  try {
    for (let off = 0; off < init.n; off += chunk) {
      const part = await evaluate(targetId, `(()=>{ const s=window.__zjuBytes.subarray(${off},${off + chunk}); let b=""; for (let i=0;i<s.length;i++) b+=String.fromCharCode(s[i]); return btoa(b); })()`);
      fs.writeSync(fd, Buffer.from(part, "base64"));
    }
  } finally {
    fs.closeSync(fd);
    await evaluate(targetId, "(()=>{ delete window.__zjuBytes; return true; })()").catch(() => {});
  }
  return { bytes: fs.statSync(outPath).size, contentType: init.ct };
}

// 当前标签页是否已经是 PDF（浏览器内置查看器），此时普通同源 fetch 可能拿不到响应体。
export async function isPdfViewer(targetId) {
  const r = JSON.parse(await evaluate(targetId, `(() => JSON.stringify({
    ct: document.contentType, url: location.href,
    embed: !!document.querySelector('embed[type="application/pdf"]')
  }))()`));
  return r;
}

// 通过 CDP 的 Page.getResourceContent 直接取「文档自身」的字节。
// 适用场景：PDF 已在浏览器查看器里打开，同源 fetch 被查看器拦截/返回空。
export async function fetchPdfViaDevTools(targetId, outPath, { allowNonPdf = false } = {}) {
  const cdp = await new CDP(await pageWsUrl(targetId)).connect();
  try {
    await cdp.send("Page.enable");
    const tree = await cdp.send("Page.getFrameTree", {}, 20000);
    const frameId = tree.frameTree.frame.id;
    const url = tree.frameTree.frame.url;
    const res = await cdp.send("Page.getResourceContent", { frameId, url }, 120000);
    if (!res || !res.content) throw new Error("Page.getResourceContent 未返回内容");
    const buf = Buffer.from(res.content, "base64");
    const head = buf.subarray(0, 5).toString("latin1");
    if (!head.startsWith("%PDF") && !allowNonPdf) {
      throw new Error(`devtools 取到的不是 PDF (head=${JSON.stringify(head)})`);
    }
    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    fs.writeFileSync(outPath, buf);
    return { bytes: buf.length, contentType: "application/pdf (Page.getResourceContent)", sourceUrl: url };
  } finally { cdp.close(); }
}

// 从 PDF 查看器页面里分块回传字节：优先 fetch(location.href)，失败则报错交给上层换策略。
export async function fetchCurrentDocumentBytes(targetId, outPath, { allowNonPdf = false, chunk = 400000 } = {}) {
  let init = null;
  try {
    init = await evaluate(targetId, `(async()=>{
      const r = await fetch(location.href, { credentials: "include" });
      const ab = await r.arrayBuffer();
      window.__zjuBytes = new Uint8Array(ab);
      return { ok: r.ok, status: r.status, ct: r.headers.get("content-type") || "",
               n: window.__zjuBytes.length, head: Array.from(window.__zjuBytes.slice(0, 8)) };
    })()`, { timeout: 180000 });
  } catch (e) {
    init = { ok: false, err: e.message };
  }
  const head = Buffer.from((init && init.head) || []).toString("latin1");
  if (!init || !init.ok || typeof init.n !== "number" || (init.n < 1000 && !head.startsWith("%PDF"))) {
    // 回退：用 DevTools 直接取文档字节
    return await fetchPdfViaDevTools(targetId, outPath, { allowNonPdf });
  }
  if (!head.startsWith("%PDF") && !allowNonPdf) throw new Error(`not a PDF (ct=${init.ct}, head=${JSON.stringify(head)})`);
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  const fd = fs.openSync(outPath, "w");
  try {
    for (let off = 0; off < init.n; off += chunk) {
      const part = await evaluate(targetId, `(()=>{ const s=window.__zjuBytes.subarray(${off},${off + chunk}); let b=""; for (let i=0;i<s.length;i++) b+=String.fromCharCode(s[i]); return btoa(b); })()`);
      fs.writeSync(fd, Buffer.from(part, "base64"));
    }
  } finally {
    fs.closeSync(fd);
    await evaluate(targetId, "(()=>{ delete window.__zjuBytes; return true; })()").catch(() => {});
  }
  return { bytes: fs.statSync(outPath).size, contentType: init.ct };
}

function pdfPageCount(file) {
  try { const b = fs.readFileSync(file).toString("latin1"); const m = b.match(/\/Type\s*\/Page[^s]/g); return m ? m.length : null; } catch { return null; }
}

const invokedDirectly = !!process.argv[1] && (
  import.meta.url === `file://${process.argv[1].replace(/\\/g, "/")}` ||
  /(^|[\\/])cdp\.mjs$/.test(process.argv[1]) ||
  process.env.CDP_CLI === "1"
);
if (invokedDirectly) {
  const [cmd, ...rest] = process.argv.slice(2);
  try {
    if (cmd === "list") { for (const t of await listTargets()) console.log(`${t.id}\t${t.title}\t${t.url}`); }
    else if (cmd === "version") console.log(JSON.stringify(await version(), null, 2));
    else if (cmd === "open") { const t = await newPage(rest[0]); await navigate(t.id, rest[0]); console.log(`${t.id}\t${rest[0]}`); }
    else if (cmd === "info") console.log(await evaluate(rest[0], "JSON.stringify({url:location.href,title:document.title,ready:document.readyState,ct:document.contentType})"));
    else if (cmd === "eval") { const r = await evaluate(rest[0], rest.slice(1).join(" ")); console.log(typeof r === "string" ? r : JSON.stringify(r, null, 2)); }
    else if (cmd === "pdf") { const [id, url, out] = rest; const r = await fetchPdfInPage(id, url, out); console.log(`saved ${out} ${JSON.stringify(r)} pages=${pdfPageCount(out)}`); }
    else console.log("commands: version | list | open <url> | info <id> | eval <id> <js> | pdf <id> <url> <out>");
  } catch (e) { console.error("ERROR: " + e.message); process.exit(1); }
}
