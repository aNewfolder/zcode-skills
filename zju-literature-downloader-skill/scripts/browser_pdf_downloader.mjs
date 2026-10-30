#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { resolveTransport, waitForComplete } from "./cdp_resolve.mjs";

function usage() {
  console.log(`Usage:
  node browser_pdf_downloader.mjs --url <pdf-url> --out <file.pdf> [--proxy <url>] [--close] [--allow-non-pdf] [--chunk-size <n>]
  node browser_pdf_downloader.mjs --target <targetId> --out <file.pdf> [--proxy <url>]

Downloads a PDF through an already-authenticated browser page.
Transports (auto-detected): web-access CDP proxy http://127.0.0.1:3456,
then direct browser debug port http://127.0.0.1:9222 (ZJU_CDP_DIRECT / ZJU_CDP=proxy|direct).

It does not bypass logins, CAPTCHA, Cloudflare, paywalls, or publisher restrictions.`);
}

function parseArgs(argv) {
  const args = { close: false, allowNonPdf: false, chunkSize: 262144 };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--help" || a === "-h") args.help = true;
    else if (a === "--url") args.url = argv[++i];
    else if (a === "--target") args.target = argv[++i];
    else if (a === "--out") args.out = argv[++i];
    else if (a === "--proxy") args.proxy = argv[++i].replace(/\/$/, "");
    else if (a === "--direct") { process.env.ZJU_CDP_DIRECT = argv[++i].replace(/\/$/, ""); }
    else if (a === "--close") args.close = true;
    else if (a === "--allow-non-pdf") args.allowNonPdf = true;
    else if (a === "--chunk-size") args.chunkSize = Number(argv[++i]);
    else throw new Error(`Unknown argument: ${a}`);
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv);
  if (args.help) { usage(); return; }
  if (!args.out) throw new Error("--out is required");
  if (!args.url && !args.target) throw new Error("Provide --url or --target");

  const t = await resolveTransport(args.proxy);
  process.stderr.write(`[transport] ${t.kind} -> ${t.base}\n`);

  const evalIn = (targetId, js, ms) => (t.kind === "direct" ? t.client.evaluate(targetId, js, ms) : t.evaluate(targetId, js, ms));
  const infoOf = (targetId, ms) => (t.kind === "direct" ? t.client.info(targetId, ms) : t.info(targetId, ms));

  let target = args.target;
  let openedByScript = false;

  if (!target) {
    const created = await t.newTarget(args.url);
    target = created.targetId;
    openedByScript = true;
    if (!target) throw new Error("could not create a tab: " + JSON.stringify(created));
    if (t.kind === "direct") {
      await t.client.navigate(target, args.url);
      await waitForComplete(t.client, target);
    } else {
      await waitForComplete(t, target);
    }
  } else if (args.url) {
    if (t.kind === "direct") await t.client.navigate(target, args.url);
    else await t.navigate(target, args.url);
    if (t.kind === "direct") await waitForComplete(t.client, target);
    else await waitForComplete(t, target);
  }

  const initJs = `(
    async () => {
      const r = await fetch(location.href, { credentials: "include" });
      const ct = r.headers.get("content-type") || "";
      const ab = await r.arrayBuffer();
      window.__zjuLiteratureDownloaderBytes = new Uint8Array(ab);
      return {
        ok: r.ok,
        status: r.status,
        contentType: ct,
        size: window.__zjuLiteratureDownloaderBytes.length,
        url: location.href,
        head: Array.from(window.__zjuLiteratureDownloaderBytes.slice(0, 8))
      };
    }
  )()`;

  const init = await evalIn(target, initJs, 120000);
  // 直连时 Runtime.evaluate({returnByValue}) 直接给值；代理时 /eval 返回 {value:...} 已解包。
  const meta = init && init.value !== undefined && init.value !== null && typeof init.value === "object" && "ok" in init.value ? init.value : init;
  if (!meta || !meta.ok) {
    throw new Error(`Browser fetch failed: ${JSON.stringify(meta)}`);
  }

  const headAscii = Buffer.from(meta.head || []).toString("ascii");
  if (!args.allowNonPdf && !headAscii.startsWith("%PDF")) {
    throw new Error(
      `Downloaded content is not a PDF. content-type=${meta.contentType}, head=${JSON.stringify(meta.head)}. ` +
      `If this is expected, rerun with --allow-non-pdf.`
    );
  }

  fs.mkdirSync(path.dirname(path.resolve(args.out)), { recursive: true });
  const stream = fs.createWriteStream(args.out);
  const size = Number(meta.size);
  for (let start = 0; start < size; start += args.chunkSize) {
    const end = Math.min(start + args.chunkSize, size);
    const chunkJs = `(
      () => {
        const bytes = window.__zjuLiteratureDownloaderBytes.slice(${start}, ${end});
        let bin = "";
        for (let i = 0; i < bytes.length; i += 0x8000) {
          bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
        }
        return btoa(bin);
      }
    )()`;
    const chunk = await evalIn(target, chunkJs, 120000);
    const b64 = typeof chunk === "string" ? chunk : chunk && chunk.value;
    if (typeof b64 !== "string") throw new Error(`chunk ${start}-${end} returned no data: ${JSON.stringify(chunk).slice(0, 200)}`);
    stream.write(Buffer.from(b64, "base64"));
  }
  await new Promise((resolve, reject) => {
    stream.end(resolve);
    stream.on("error", reject);
  });

  const saved = fs.readFileSync(args.out);
  const savedHead = saved.subarray(0, 8).toString("ascii");
  const result = {
    transport: t.kind,
    out: path.resolve(args.out),
    bytes: saved.length,
    contentType: meta.contentType,
    sourceUrl: meta.url,
    signature: savedHead,
    pdf: savedHead.startsWith("%PDF"),
  };

  if (args.close && openedByScript) {
    try {
      if (t.kind === "direct") await t.client.close(target);
      else await t.close(target);
    } catch (_) {}
  }

  console.log(JSON.stringify(result, null, 2));

  if (t.kind === "direct") t.client.dispose(); // 不关 ws 的话 node 进程不会退出
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
