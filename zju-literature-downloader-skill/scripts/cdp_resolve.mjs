#!/usr/bin/env node
// cdp_resolve.mjs — 让 skill 的脚本同时支持两种传输层：
//   A) web-access CDP 代理（默认 http://127.0.0.1:3456，本机不存在）
//   B) 直连浏览器调试端口（默认 http://127.0.0.1:9222，本机可用：Edge/Chrome + 独立 profile）
//
// 解析顺序（可用 ZJU_CDP 强制指定，取值 auto|proxy|direct）：
//   1) ZJU_CDP_PROXY / --proxy 显式给出 -> 只用它
//   2) 3456 探活成功 -> 代理
//   3) 9222 探活成功 -> 直连
//   4) 都不可用 -> 抛出带修复指引的错误（而不是静默失败）
//
// 只使用浏览器已授权的页面上下文；不读取 cookie、密码、localStorage、浏览器 profile 文件。

export const DEFAULT_PROXY = "http://127.0.0.1:3456";
export const DEFAULT_DIRECT = "http://127.0.0.1:9222";

async function probe(base, timeoutMs = 1200) {
  try {
    const r = await fetch(`${base}/json/version`, { signal: AbortSignal.timeout(timeoutMs) });
    if (!r.ok) return null;
    const j = await r.json();
    return j && (j.Browser || j.webSocketDebuggerUrl) ? j : null;
  } catch { return null; }
}

// ---------- 直连 CDP 实现（等价于 3456 代理的 /new /info /navigate /eval /close 子集） ----------
class DirectCdp {
  constructor(base) {
    this.base = base.replace(/\/$/, "");
    this.id = 0;
    this.pending = new Map();
    this.connections = new Map(); // targetId -> ws
  }

  async list() {
    const r = await fetch(`${this.base}/json/list`, { signal: AbortSignal.timeout(10000) });
    return (await r.json()).filter((t) => t.type === "page");
  }

  async wsFor(targetId) {
    const cached = this.connections.get(targetId);
    if (cached && cached.readyState === 1) return cached;
    const list = await fetch(`${this.base}/json/list`, { signal: AbortSignal.timeout(10000) }).then((r) => r.json());
    const hit = (list || []).find((t) => t.id === targetId && t.webSocketDebuggerUrl);
    // 新建的 PDF 标签页常常无法用 /devtools/page/<id> 连接，必须用列表里的真实 ws 地址。
    const wsUrl = hit ? hit.webSocketDebuggerUrl
      : `ws://127.0.0.1:${new URL(this.base).port}/devtools/page/${targetId}`;
    const ws = new WebSocket(wsUrl);
    await new Promise((res, rej) => {
      const to = setTimeout(() => rej(new Error("ws connect timeout")), 15000);
      ws.onopen = () => { clearTimeout(to); res(); };
      ws.onerror = () => { clearTimeout(to); rej(new Error(`ws error for ${targetId}`)); };
    });
    this.connections.set(targetId, ws);
    return ws;
  }

  send(targetId, ws, method, params = {}, ms = 60000) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      const onMsg = (ev) => {
        let msg; try { msg = JSON.parse(ev.data); } catch { return; }
        if (msg.id !== id) return;
        ws.removeEventListener("message", onMsg);
        clearTimeout(to);
        msg.error ? rej(new Error(JSON.stringify(msg.error))) : res(msg.result);
      };
      const to = setTimeout(() => { ws.removeEventListener("message", onMsg); rej(new Error(`timeout ${method}`)); }, ms);
      ws.addEventListener("message", onMsg);
      ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(targetId, expression, timeoutMs = 120000) {
    const ws = await this.wsFor(targetId);
    try {
      await this.send(targetId, ws, "Runtime.enable", {}, 20000);
      const r = await this.send(targetId, ws, "Runtime.evaluate",
        { expression, returnByValue: true, awaitPromise: true, timeout: timeoutMs }, timeoutMs + 5000);
      if (r.exceptionDetails) throw new Error("page exception: " + JSON.stringify(r.exceptionDetails).slice(0, 400));
      return r.result ? r.result.value : undefined;
    } catch (e) {
      if (/context|navigated|ws error/i.test(e.message)) {
        this.connections.delete(targetId);
      }
      throw e;
    }
  }

  async info(targetId, timeoutMs = 10000) {
    const v = await this.evaluate(targetId, `JSON.stringify({url:location.href,title:document.title,ready:document.readyState,contentType:document.contentType})`, timeoutMs);
    const o = JSON.parse(v);
    return { targetId, url: o.url, title: o.title, ready: o.ready, contentType: o.contentType };
  }

  async newTarget(url) {
    const enc = encodeURIComponent(url);
    let t;
    try {
      const r = await fetch(`${this.base}/json/new?${enc}`, { method: "PUT", signal: AbortSignal.timeout(30000) });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      t = await r.json();
    } catch {
      const r = await fetch(`${this.base}/json/new?${enc}`, { signal: AbortSignal.timeout(30000) });
      if (!r.ok) throw new Error(`HTTP ${r.status} /json/new`);
      t = await r.json();
    }
    return { targetId: t.id, url };
  }

  async navigate(targetId, url) {
    const ws = await this.wsFor(targetId);
    await this.send(targetId, ws, "Page.enable", {}, 20000);
    await this.send(targetId, ws, "Page.navigate", { url }, 30000);
    return { targetId, url };
  }

  async close(targetId) {
    try { await fetch(`${this.base}/json/close/${encodeURIComponent(targetId)}`, { signal: AbortSignal.timeout(10000) }); } catch {}
    const ws = this.connections.get(targetId);
    if (ws) { try { ws.close(); } catch {} this.connections.delete(targetId); }
  }

  // 关闭所有缓存的 WebSocket：CLI 入口结束时必须调用，否则挂着的 ws 会让 node 进程不退出。
  dispose() {
    for (const ws of this.connections.values()) { try { ws.close(); } catch {} }
    this.connections.clear();
  }
}

// 直连传输对象上补挂与代理适配器同名的方法：
// 调用方（cdp_open_url.mjs / browser_pdf_downloader.mjs 等）统一写 t.newTarget/t.navigate/t.info/...，
// 不必关心底层是直连还是代理；t.client 仍暴露原始 DirectCdp 供旧代码使用。
function directTransport(client, base, note) {
  return {
    kind: "direct", base, client, proxy: null, direct: base, note,
    list: () => client.list(),
    newTarget: (url) => client.newTarget(url),
    navigate: (targetId, url) => client.navigate(targetId, url),
    evaluate: (targetId, js, ms) => client.evaluate(targetId, js, ms),
    info: (targetId, ms) => client.info(targetId, ms),
    close: (targetId) => client.close(targetId),
  };
}

// ---------- 3456 代理适配器 ----------
class ProxyAdapter {
  constructor(proxy) { this.proxy = proxy.replace(/\/$/, ""); }

  async get(endpoint, params = {}, timeoutMs = 60000) {
    const u = new URL(endpoint, this.proxy);
    for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
    const r = await fetch(u.toString(), { signal: AbortSignal.timeout(timeoutMs) });
    const text = await r.text();
    if (!r.ok) throw new Error(`HTTP ${r.status} from ${u}: ${text.slice(0, 300)}`);
    return JSON.parse(text);
  }

  async evaluate(targetId, js, timeoutMs = 120000) {
    const u = new URL("/eval", this.proxy);
    u.searchParams.set("target", targetId);
    const r = await fetch(u.toString(), { method: "POST", body: js, signal: AbortSignal.timeout(timeoutMs) });
    const text = await r.text();
    if (!r.ok) throw new Error(`HTTP ${r.status} from /eval: ${text.slice(0, 300)}`);
    const j = JSON.parse(text);
    return j && Object.prototype.hasOwnProperty.call(j, "value") ? j.value : j;
  }

  async info(targetId, timeoutMs = 10000) { return this.get("/info", { target: targetId }, timeoutMs); }
  async newTarget(url) { return this.get("/new", { url }, 60000); }
  async navigate(targetId, url) { return this.get("/navigate", { target: targetId, url }, 60000); }
  async close(targetId) { try { return await this.get("/close", { target: targetId }, 10000); } catch { return null; } }
}

/**
 * 解析出可用的传输层。
 * @param {string|undefined} explicitProxy --proxy 参数；给定时只用它（保持老行为）
 * @returns {Promise<{kind:'proxy'|'direct', base:string, client:object|null, proxy:string|null, direct:string|null, note:string}>}
 */
export async function resolveTransport(explicitProxy) {
  const mode = (process.env.ZJU_CDP || "auto").toLowerCase();
  const directBase = (process.env.ZJU_CDP_DIRECT || DEFAULT_DIRECT).replace(/\/$/, "");
  const proxyBase = (explicitProxy || process.env.ZJU_CDP_PROXY || DEFAULT_PROXY).replace(/\/$/, "");

  if (mode === "proxy" || explicitProxy) {
    const v = await probe(proxyBase);
    if (!v) throw new Error(proxyUnreachableMessage(proxyBase));
    return { kind: "proxy", base: proxyBase, client: null, proxy: proxyBase, direct: null, note: `proxy ${v.Browser || ""}`.trim() };
  }
  if (mode === "direct") {
    const v = await probe(directBase);
    if (!v) throw new Error(directUnreachableMessage(directBase));
    return directTransport(new DirectCdp(directBase), directBase, `direct ${v.Browser || ""}`.trim());
  }

  const pv = await probe(proxyBase);
  if (pv) return { kind: "proxy", base: proxyBase, client: null, proxy: proxyBase, direct: null, note: `proxy ${pv.Browser || ""}`.trim() };

  const dv = await probe(directBase);
  if (dv) return directTransport(new DirectCdp(directBase), directBase, `direct ${dv.Browser || ""}`.trim());

  throw new Error(proxyUnreachableMessage(proxyBase) + "\n\n" + directUnreachableMessage(directBase));
}

function proxyUnreachableMessage(proxyBase) {
  return `web-access CDP 代理不可用: ${proxyBase}\n`
    + `  修复：安装/启动 web-access-main 代理，或用 --proxy <url> 指定端口；\n`
    + `  若本机只有浏览器调试端口，脚本会自动回落到直连（见下）。`;
}

function directUnreachableMessage(directBase) {
  return `直连浏览器调试端口不可用: ${directBase}\n`
    + `  修复：用独立 profile 启动带调试端口的 Edge/Chrome，例如：\n`
    + `    msedge.exe --remote-debugging-port=9222 --user-data-dir=%LOCALAPPDATA%\\zju-debug-browser\n`
    + `  （默认 profile 会被新版 Chrome/Edge 忽略调试端口，必须用独立 --user-data-dir）\n`
    + `  然后在该窗口里人工登录浙大图书馆/WebVPN/CAS，再重跑本脚本。\n`
    + `  可用 ZJU_CDP=proxy|direct 强制指定传输层，ZJU_CDP_DIRECT=http://127.0.0.1:<port> 改端口。`;
}

export async function sleep(ms) { await new Promise((r) => setTimeout(r, ms)); }

export async function waitForComplete(t, targetId, maxMs = 45000) {
  const started = Date.now();
  let last = null;
  while (Date.now() - started < maxMs) {
    try {
      last = await t.info(targetId, 10000);
      if (last && last.ready === "complete") return last;
    } catch {}
    await sleep(1000);
  }
  return last;
}
