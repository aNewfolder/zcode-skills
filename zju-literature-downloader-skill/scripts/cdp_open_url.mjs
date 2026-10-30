#!/usr/bin/env node
import { resolveTransport, waitForComplete } from "./cdp_resolve.mjs";

function usage() {
  console.log(`Usage:
  node cdp_open_url.mjs --url <url> [--proxy http://127.0.0.1:3456] [--direct http://127.0.0.1:9222] [--wait]

Opens a URL in the already-authorized browser session, URL-encoding nested URLs correctly
(this matters for Summon URLs containing "#!", where a stripped fragment yields about:blank).

Transports (auto-detected):
  1) web-access CDP proxy  http://127.0.0.1:3456   (--proxy / ZJU_CDP_PROXY)
  2) direct browser debug port http://127.0.0.1:9222 (ZJU_CDP_DIRECT)
Force one with ZJU_CDP=proxy|direct.

It does not read cookies, passwords, local storage, or browser profiles.`);
}

function parseArgs(argv) {
  const args = { wait: false };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--help" || a === "-h") args.help = true;
    else if (a === "--url") args.url = argv[++i];
    else if (a === "--proxy") args.proxy = argv[++i].replace(/\/$/, "");
    else if (a === "--direct") { process.env.ZJU_CDP_DIRECT = argv[++i].replace(/\/$/, ""); }
    else if (a === "--wait") args.wait = true;
    else throw new Error(`Unknown argument: ${a}`);
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv);
  if (args.help) { usage(); return; }
  if (!args.url) throw new Error("--url is required");

  const t = await resolveTransport(args.proxy);
  process.stderr.write(`[transport] ${t.kind} -> ${t.base}\n`);

  const created = await t.newTarget(args.url);
  const target = created.targetId;
  if (!target) throw new Error("could not create a tab: " + JSON.stringify(created));

  let info;
  if (t.kind === "direct") {
    await t.client.navigate(target, args.url);
    info = args.wait ? await waitForComplete(t.client, target) : await t.client.info(target);
  } else {
    info = args.wait ? await waitForComplete(t, target) : await t.info(target);
  }

  console.log(JSON.stringify({
    transport: t.kind,
    targetId: target,
    title: info?.title || null,
    url: info?.url || null,
    ready: info?.ready || null,
  }, null, 2));

  if (t.kind === "direct") t.client.dispose(); // 不关 ws 的话 node 进程不会退出
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
