#!/usr/bin/env node
// new_round.mjs — 为「本轮对话」建一个成果目录，并给出可直接粘贴的开始时间。
//
// 用法:
//   node tools/new_round.mjs --list <清单文件> [--label fix14-17] [--at "2026-09-12T14:30"]
//   node tools/new_round.mjs --text "10.1038/xxx" --label quick
//
// 产物:
//   <ROOT>/rounds/<YYYY-MM-DDTHHmm>_<label>/
//       input.txt        本轮清单原样留存（复制自 --list，或写入 --text）
//       PDFs/            本轮产出（正文与 __SI## 附属材料同目录）
//       解析/            本轮解析结果
//       round-note.md    本轮小结（模板）
// 说明: 目录名"没有冒号"以便 Windows 使用；排序即时间顺序。
import fs from "node:fs";
import path from "node:path";
import { ROOT, ARCHIVE_LOGS } from "./paths.mjs";

function arg(name, def = null) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith("--") ? process.argv[i + 1] : def;
}

// 默认用**本地时间**（UTC 会让目录名差 8 小时）；--at 可显式覆盖为 2026-09-12T14:30 形式
function localStamp(d = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}${p(d.getMinutes())}`;
}
const at = arg("at") || localStamp();
const stamp = at.replace(":", "").slice(0, 16); // 2026-09-12T14:30 -> 2026-09-12T1430
const label = (arg("label") || "round").replace(/[^\w\u4e00-\u9fff-]+/g, "-").slice(0, 32);
const listFile = arg("list");
const text = arg("text");

const dirName = `${stamp}_${label}`;
const roundDir = path.join(ROOT, "rounds", dirName);
fs.mkdirSync(path.join(roundDir, "PDFs"), { recursive: true });
fs.mkdirSync(path.join(roundDir, "解析"), { recursive: true });

let inputBody = "";
if (listFile) {
  const p = path.isAbsolute(listFile) ? listFile : path.join(ROOT, listFile);
  if (!fs.existsSync(p)) { console.error(`清单文件不存在: ${p}`); process.exit(1); }
  inputBody = fs.readFileSync(p, "utf8");
} else if (text) {
  inputBody = text.endsWith("\n") ? text : text + "\n";
} else {
  console.error("需要 --list <文件> 或 --text <内容>");
  process.exit(1);
}
fs.writeFileSync(path.join(roundDir, "input.txt"), inputBody, "utf8");

const nItems = inputBody.split("\n").filter((l) => l.trim() && !l.trim().startsWith("#")).length;

const note = `# 本轮小结（${dirName}）

- 开始时间：${at}
- 清单来源：${listFile || "(对话中直接给出)"}
- 清单条目：${nItems}
- 成果目录：\`rounds/${dirName}/PDFs\`

## 结果

| # | 文献 | DOI | 状态 | 页数 | 文件 |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

## 失败与原因

| # | 文献 | 状态 | 原因 |
|---|---|---|---|
|  |  |  |  |

## 已下载过（本轮跳过）

| # | DOI | 上次状态 | 说明 |
|---|---|---|---|
|  |  |  |  |

## 备注

- 本目录可整包剪切走；\`manifest.tsv\` 与 \`全局任务简报.md\` 留在项目里作为长期记忆。
`;
fs.writeFileSync(path.join(roundDir, "round-note.md"), note, "utf8");

try {
  fs.mkdirSync(ARCHIVE_LOGS, { recursive: true });
  fs.appendFileSync(path.join(ARCHIVE_LOGS, "rounds.log"),
    `${new Date().toISOString()}\t${dirName}\titems=${nItems}\tfrom=${listFile || "--text"}\n`, "utf8");
} catch {}

console.log(JSON.stringify({
  roundDir: path.relative(ROOT, roundDir).replace(/\\/g, "/"),
  input: nItems === 0 ? "空" : `${nItems} 条`,
  note: path.relative(ROOT, path.join(roundDir, "round-note.md")).replace(/\\/g, "/"),
}, null, 2));
