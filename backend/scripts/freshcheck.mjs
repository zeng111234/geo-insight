// 服务新鲜度检查：8123 上跑的代码是不是当前工作区检出的版本。
//
// 为什么单独要这一层（2026-11-25 反复踩）：
// git pull / 改完代码之后，8123 上跑的还是旧进程。browsercheck / interactioncheck
// 打的是这个旧进程，于是新功能相关的断言假红——代码是对的，只是服务没重启。
// 靠人记「改完要重启」不可靠；这里让机器比对版本，给出确定答案。
//
// 用法：先起服务，再 node backend/scripts/freshcheck.mjs
// 换端口：GEO_BASE=http://127.0.0.1:8124 node backend/scripts/freshcheck.mjs
// 退出码：0=一致；1=服务是旧进程（需重启）或无法判断；2=服务连不上。
//
// 判据来自 /health 的 code_rev（服务进程启动时读一次 HEAD，固定不变），
// 与工作区 `git rev-parse --short HEAD` 对比。非 git 检出返回 unknown：
// 这时明确说「无法判断」，不假装一致。

import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const BASE = process.env.GEO_BASE || "http://127.0.0.1:8123";
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

function workspaceRev() {
  try {
    return execFileSync("git", ["rev-parse", "--short", "HEAD"], {
      cwd: ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return "unknown";
  }
}

async function serverRev() {
  const res = await fetch(BASE + "/health", { signal: AbortSignal.timeout(5000) });
  if (!res.ok) throw new Error("HTTP " + res.status);
  const body = await res.json();
  return {
    rev: body.code_rev ?? "unknown",
    status: body.status,
    metricVersion: body.metric_version,
  };
}

try {
  const server = await serverRev();
  const ws = workspaceRev();
  console.log(`服务 ${BASE}：code_rev=${server.rev}（status=${server.status}）`);
  console.log(`工作区 HEAD：${ws}`);

  if (server.rev === "unknown" || ws === "unknown") {
    console.error("无法判断版本一致性（非 git 检出或 /health 无 code_rev）。请手动确认服务是否重启过。");
    process.exit(1);
  }
  if (server.rev === ws) {
    console.log("PASS  服务加载的是当前工作区代码。");
    process.exit(0);
  }
  console.error(`FAIL  服务是旧进程：服务=${server.rev}，工作区=${ws}。`);
  console.error("      先 Ctrl+C 停掉服务，再双击 start.bat 重开，然后重跑检查。");
  process.exit(1);
} catch (e) {
  console.error(`ERROR  连不上 ${BASE}：${e.message}`);
  console.error("      先启动服务（双击 start.bat），或设 GEO_BASE 指向正确端口。");
  process.exit(2);
}
