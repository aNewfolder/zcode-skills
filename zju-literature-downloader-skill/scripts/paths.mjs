#!/usr/bin/env node
// paths.mjs — 统一路径解析：一切相对「本文件所在目录」，与当前工作目录无关。
//
// 为什么必须这样：本项目历史上用相对 cwd 的路径（"Task1/文献自动下载"）产出了一棵嵌套
// 重复目录树，另有若干脚本硬编码了某个人的本机绝对路径，换工作区后永远写不到。
//
// 覆盖方式（优先级从高到低）：
//   ZJU_PROJECT_ROOT   直接指定项目根
//   ZJU_OUT            指定成果根（等价于项目根）
//   默认               本文件所在目录的上一级（tools/ -> 项目根）
import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

export const HERE = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(process.env.ZJU_PROJECT_ROOT || process.env.ZJU_OUT || path.join(HERE, ".."));
export const PDFS = path.join(ROOT, "PDFs");
// 附属材料自 2026-09-12 起与正文同目录、用 __SI## 后缀；SupportingInformation/ 仅作历史兼容。
export const SI = path.join(ROOT, "SupportingInformation");
export const SI_LEGACY = SI;
export function siDirInUse() {
  try {
    if (fs.readdirSync(PDFS).some((f) => /__SI\d+\./i.test(f))) return PDFS;
  } catch {}
  return fs.existsSync(SI) && fs.readdirSync(SI).length ? SI : PDFS;
}
/** 给定正文文件名，返回它对应的附属材料命名前缀与正则 */
export function siNameFor(mainFile, index) {
  const stem = mainFile.replace(/\.pdf$/i, "");
  return `${stem}__SI${String(index).padStart(2, "0")}.pdf`;
}
export function siMatcher(mainFile) {
  const stem = mainFile.replace(/\.pdf$/i, "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`^${stem}__SI\\d+\\.pdf$`, "i");
}
export const DOCS = path.join(ROOT, "docs");
export const ARCHIVE_LOGS = path.join(ROOT, "archive", "logs");
export const MANIFEST = path.join(ROOT, "manifest.tsv");

export function ensure(...dirs) {
  for (const d of dirs) fs.mkdirSync(d, { recursive: true });
  return dirs;
}

export function sha256(file) {
  return createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

/** 把运行日志集中写到 archive/logs，不要散落在项目根 */
export function logJson(name, data) {
  ensure(ARCHIVE_LOGS);
  const p = path.join(ARCHIVE_LOGS, name);
  fs.writeFileSync(p, JSON.stringify(data, null, 1), "utf8");
  return p;
}

if (process.argv[1] && /paths\.mjs$/.test(process.argv[1])) {
  console.log(JSON.stringify({ HERE, ROOT, PDFS, SI, MANIFEST, ARCHIVE_LOGS }, null, 2));
}
