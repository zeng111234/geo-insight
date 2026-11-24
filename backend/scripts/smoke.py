"""End-to-end smoke test: seed a temp database, boot uvicorn, hit real HTTP.

Run from the project root:

    python backend/scripts/smoke.py

It seeds a temp DB and boots its own uvicorn on an auto-picked free port,
so it never tests the dev server by accident. Override with GEO_SMOKE_PORT=n
(an occupied explicit port is a hard error).

Exits non-zero on the first failed assertion, printing the response body.
"""

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 0 = 自动挑空闲端口。开发服务常占着 8123，若冒烟也去连它，
# 请求会打到开发库而不是本脚本刚 seed 的临时库，断言就成了假绿灯。
PORT = int(os.getenv("GEO_SMOKE_PORT", "0"))
BASE = f"http://127.0.0.1:{PORT}"
TRACKER = "11111111-1111-1111-1111-111111111111"
ART_PUBLISHED = "55555555-5555-5555-5555-555555555551"
ART_DRAFT = "55555555-5555-5555-5555-555555555552"
DB = Path(tempfile.gettempdir()) / "geo_smoke.db"


def get(path: str) -> tuple[int, dict]:
    req = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def get_html(path: str) -> tuple[int, str, str]:
    req = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.headers.get("content-type", ""), resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("content-type", ""), exc.read().decode("utf-8", "replace")


def post(path: str, payload: dict) -> tuple[int, dict]:
    return _request(path, payload, "POST")


def patch(path: str, payload: dict) -> tuple[int, dict]:
    return _request(path, payload, "PATCH")


def delete(path: str) -> int:
    req = urllib.request.Request(BASE + path, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _request(path: str, payload: dict, method: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def check(name: str, condition: bool, detail) -> None:
    if condition:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}\n      {json.dumps(detail, ensure_ascii=False)[:600]}")
        raise SystemExit(1)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main() -> None:
    global PORT, BASE
    if PORT == 0:
        PORT = _free_port()
    elif _port_in_use(PORT):
        raise SystemExit(
            f"port {PORT} is occupied (likely the dev server). "
            "Stop it, or unset GEO_SMOKE_PORT to auto-pick a free port."
        )
    BASE = f"http://127.0.0.1:{PORT}"

    if DB.exists():
        DB.unlink()
    env = os.environ.copy()
    env["GEO_DB_PATH"] = str(DB)
    env["PYTHONPATH"] = str(ROOT / "src")

    subprocess.run([sys.executable, "-m", "geo.seed"], cwd=ROOT, env=env, check=True, capture_output=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "geo.api.app:app", "--port", str(PORT), "--log-level", "warning"],
        cwd=ROOT,
        env=env,
    )
    try:
        for _ in range(40):
            if proc.poll() is not None:
                raise SystemExit(f"smoke server exited early (code {proc.returncode}), port {PORT}")
            try:
                status, _ = get("/health")
                if status == 200:
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.25)
        else:
            raise SystemExit("server did not start")

        status, body = get("/health")
        check("health", status == 200 and body["status"] == "ok", body)

        status, body = get("/v1/trackers")
        check("tracker list", status == 200 and len(body["items"]) == 2, body)
        golden = next(t for t in body["items"] if t["id"] == TRACKER)
        check(
            "question summary",
            golden["question_summary"] == {"total": 12, "core": 4, "long_tail": 8},
            golden["question_summary"],
        )

        status, body = get(f"/v1/trackers/{TRACKER}/metrics/overview?from=2026-11-01&to=2026-11-03")
        check("valid total 9", body["valid_total"] == 9, body)
        check("mention count 2", body["cards"]["mention_count"] == 2, body)
        check("mention rate 22.22%", body["cards"]["mention_rate"]["value"] == 0.2222, body)
        check("avg rank insufficient (2 ranked)", body["cards"]["avg_rank"]["status"] == "insufficient_samples", body)
        check("no list samples 0", body["no_list_samples"] == 0, body)

        status, body = get(f"/v1/trackers/{TRACKER}/metrics/overview?from=2026-11-01&to=2026-11-04")
        check("no list samples 1", body["no_list_samples"] == 1, body)
        check("mention count 3", body["cards"]["mention_count"] == 3, body)

        status, body = get(f"/v1/trackers/{TRACKER}/metrics/overview?from=2026-11-05&to=2026-11-05")
        check("two samples insufficient", body["cards"]["mention_rate"]["status"] == "insufficient_samples", body)
        check("two samples value null", body["cards"]["mention_rate"]["value"] is None, body)

        status, body = get(f"/v1/trackers/{TRACKER}/answers?from=2026-11-01&to=2026-11-03&limit=100")
        check("answer list 9", status == 200 and body["page"]["total"] == 9, body)
        check("answer has mentions", any(item["mentions"] for item in body["items"]), body)

        status, body = get(f"/v1/trackers/{TRACKER}/citations/summary?from=2026-11-01&to=2026-11-03")
        check("citations present", body["total_citations"] == 18, body)
        check("video share present", body["video_share"] == 0.5, body)
        check("unknown published share", body["unknown_published_share"] == 0.5, body)

        status, body = get(f"/v1/trackers/{TRACKER}/citations?from=2026-11-01&to=2026-11-03")
        urls = [x["url"] for x in body["items"]]
        check(
            "citation detail dedups URLs but accumulates counts",
            status == 200 and len(urls) == len(set(urls))
            and body["total_urls"] == len(urls)
            and body["total_occurrences"] >= body["total_urls"],
            body,
        )

        # 变异检查：种子里 18 个 URL 本来就不重复，只断言「URL 唯一」是空断言。
        # 从本次响应里挑一个确实落在查询周期内的 URL，复制它的一条引用，
        # 验证明细仍只一行、次数累计 +1；结束后删掉，避免污染后续断言。
        target_url = body["items"][0]["url"]
        baseline_times = int(body["items"][0]["times"])
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM citation WHERE url = ? LIMIT 1", (target_url,)
            ).fetchone()
            check("mutation target is in the queried period", row is not None, {"url": target_url})
            cols = list(row.keys())
            values = [row[k] for k in cols]
            values[cols.index("id")] = "smoke-duplicate-citation"
            conn.execute(
                f"INSERT INTO citation({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", values
            )
            conn.commit()

            status, body2 = get(f"/v1/trackers/{TRACKER}/citations?from=2026-11-01&to=2026-11-03")
            same = [x for x in body2["items"] if x["url"] == target_url]
            check(
                "duplicate citation keeps one detail row but accumulates times",
                status == 200
                and body2["total_urls"] == body["total_urls"]
                and body2["total_occurrences"] == body["total_occurrences"] + 1
                and len(same) == 1 and int(same[0]["times"]) == baseline_times + 1,
                {"before": body["total_occurrences"], "after": body2["total_occurrences"], "same": same},
            )
        finally:
            conn.execute("DELETE FROM citation WHERE id = ?", ("smoke-duplicate-citation",))
            conn.commit()
            conn.close()

        status, body = get("/v1/meta/methodology")
        check(
            "methodology exposes configurable no-list threshold",
            status == 200 and "no_list_alert_threshold" in body and body["definitions"],
            body,
        )
        status, ctype, html = get_html("/")
        check(
            "dashboard reads threshold from methodology, not hardcoded",
            "no_list_alert_threshold" in html,
            "threshold must come from /v1/meta/methodology",
        )
        check(
            "dashboard fetches deduped citation detail",
            "/citations?" in html and "信源明细" in html,
            "citation detail must be wired into the dashboard",
        )

        status, body = get(f"/v1/trackers/{TRACKER}/metrics/trend?from=2026-11-01&to=2026-11-03")
        check("trend has 3 days", status == 200 and len(body["points"]) == 3, body)

        # ---- 富数据窗口 2026-11-07..30：页面密度与内容故事（黄金窗口之外）----
        status, body = get(f"/v1/trackers/{TRACKER}/metrics/overview?from=2026-11-07&to=2026-11-30")
        check("rich period valid total 1288", body["valid_total"] == 1288, body["valid_total"])
        status, body = get(f"/v1/trackers/{TRACKER}/metrics/trend?from=2026-11-07&to=2026-11-30")
        # 24 个自然日减去 11-22 采集中断日（0 条答案的日期不进趋势点）
        check("rich period trend 23 days", status == 200 and len(body["points"]) == 23, len(body["points"]))
        status, early = get(f"/v1/trackers/{TRACKER}/metrics/overview?from=2026-11-07&to=2026-11-12")
        status, late = get(f"/v1/trackers/{TRACKER}/metrics/overview?from=2026-11-25&to=2026-11-30")
        check(
            "mention rate rises after content push",
            early["cards"]["mention_rate"]["value"] is not None
            and late["cards"]["mention_rate"]["value"] is not None
            and late["cards"]["mention_rate"]["value"] > early["cards"]["mention_rate"]["value"],
            {"early": early["cards"]["mention_rate"], "late": late["cards"]["mention_rate"]},
        )
        check("rich period avg rank has value", late["cards"]["avg_rank"]["status"] == "ok", late["cards"]["avg_rank"])
        status, body = get(f"/v1/trackers/{TRACKER}/citations?from=2026-11-07&to=2026-11-30")
        check(
            "citation totals are full while items capped at 100",
            status == 200
            and len(body["items"]) == 100
            and body["total_urls"] > 100
            and body["total_occurrences"] > body["total_urls"]
            and int(body["items"][0]["times"]) > 1,
            {
                "items": len(body["items"]),
                "total_urls": body["total_urls"],
                "occurrences": body["total_occurrences"],
                "top_times": body["items"][0]["times"] if body["items"] else None,
            },
        )

        # 分页：明细表 10 行/页，服务端分页必须真的翻得动、页间不重叠、总数口径一致。
        status, c1 = get(f"/v1/trackers/{TRACKER}/citations?from=2026-11-07&to=2026-11-30&limit=10&page=1")
        status, c2 = get(f"/v1/trackers/{TRACKER}/citations?from=2026-11-07&to=2026-11-30&limit=10&page=2")
        u1 = {x["url"] for x in c1["items"]}
        u2 = {x["url"] for x in c2["items"]}
        check(
            "citations paginate without overlap",
            len(c1["items"]) == 10 and len(c2["items"]) == 10
            and not (u1 & u2)
            and c1["page"]["total"] == c2["page"]["total"] == c1["total_urls"],
            {"p1": len(u1), "p2": len(u2), "meta": c2["page"]},
        )
        status, a1 = get(f"/v1/trackers/{TRACKER}/answers?from=2026-11-07&to=2026-11-30&limit=10&page=1")
        status, a2 = get(f"/v1/trackers/{TRACKER}/answers?from=2026-11-07&to=2026-11-30&limit=10&page=2")
        check(
            "answers paginate without overlap",
            len(a1["items"]) == 10 and len(a2["items"]) == 10
            and not ({x["id"] for x in a1["items"]} & {x["id"] for x in a2["items"]})
            and a1["page"]["total"] == a2["page"]["total"] == 1288,
            {"p1": len(a1["items"]), "p2": len(a2["items"]), "total": a1["page"]["total"]},
        )

        status, body = get("/v1/trackers/does-not-exist/metrics/overview?from=2026-11-01&to=2026-11-03")
        check("404 for unknown tracker", status == 404 and body["code"] == "NOT_FOUND", body)

        # 看板必须由后端同源提供：file:// 打开时浏览器会拦掉对 127.0.0.1 的请求，
        # 表现为 "Failed to fetch"。这两条断言就是为这个故障加的回归。
        status, ctype, html = get_html("/")
        check("dashboard served at /", status == 200 and "text/html" in ctype, {"status": status, "type": ctype})
        check("dashboard is the app page", "GEO Insight" in html and 'id="cards"' in html, html[:200])
        check(
            "page does not hardcode absolute api base",
            'http://127.0.0.1:8123/v1' not in html,
            "index.html must derive API base from location, not pin 127.0.0.1",
        )
        check("page falls back only for file://", 'location.protocol === "file:"' in html, "missing same-origin logic")
        check("dashboard uses a tracker select", '<select id="tracker"' in html, "UUID input should be replaced by a select")

        status, ctype, _ = get_html("/static/index.html")
        check("static mount works", status == 200 and "text/html" in ctype, {"status": status, "type": ctype})

        # ---- 002 内容工作室 ----
        status, ctype, html = get_html("/studio")
        check("studio page served", status == 200 and "内容工作室" in html, {"status": status})
        # 决策 003：不复刻参考产品，页面上不允许出现积分与 AI 成稿入口。
        # 这三条在改动前必然失败，是「假象不能回来」的回归闸门。
        for forbidden in ("Credits", "5 积分", "AI 生成草稿", "AI 助手"):
            check(f"studio has no fake affordance [{forbidden}]", forbidden not in html, {"found": forbidden})
        check("studio states human authorship", "人工成稿" in html, "missing 人工成稿 statement")

        status, body = get("/v1/channels")
        channels = body.get("items", [])
        check("7 channels seeded", status == 200 and len(channels) == 7, {"count": len(channels)})
        connected = [c for c in channels if c["status"] == "connected"]
        disconnected = [c for c in channels if c["status"] != "connected"]
        check("2 channels connected", len(connected) == 2, connected)

        status, body = get(f"/v1/trackers/{TRACKER}/articles")
        check("8 articles seeded", status == 200 and len(body["items"]) == 8, body)

        status, body = get(f"/v1/articles/{ART_PUBLISHED}/impact")
        check("impact status ok", body["status"] == "ok", body)
        check("impact before 22.22%", body["before"]["mention_rate"]["value"] == 0.2222, body["before"])
        check("impact after 40%", body["after"]["mention_rate"]["value"] == 0.4, body["after"])
        check("impact delta 17.8pp", body["delta_pp"] == 17.8, body)

        status, body = get(f"/v1/articles/{ART_DRAFT}/impact")
        check("unpublished article has no comparison", body["status"] == "not_published", body)

        status, body = post(f"/v1/articles/{ART_DRAFT}/publish", {"channel_ids": [disconnected[0]["id"]]})
        check(
            "disconnected channel rejected",
            status == 400 and body["code"] == "CHANNEL_NOT_CONNECTED",
            body,
        )

        status, body = post(f"/v1/articles/{ART_DRAFT}/publish", {"channel_ids": [connected[0]["id"]]})
        check("publish queues one channel", status == 202 and len(body["queued"]) == 1, body)

        status, body = post(f"/v1/articles/{ART_DRAFT}/publish", {"channel_ids": [connected[0]["id"]]})
        check(
            "republish does not duplicate",
            status == 202 and body["created"] == [] and len(body["existing"]) == 1,
            body,
        )

        # ---- 001 配置面 / 下钻 / 报告 ----
        status, ctype, html = get_html("/settings")
        check("settings page served", status == 200 and "监控配置" in html, {"status": status})
        status, ctype, html = get_html("/reports")
        check("reports page served", status == 200 and "报告中心" in html, {"status": status})

        status, body = get(f"/v1/trackers/{TRACKER}")
        check("tracker detail has aliases", status == 200 and len(body["brand"]["aliases"]) == 3, body.get("brand"))
        check("daily planned = 60", body["sampling"]["daily_planned"] == 60, body["sampling"])
        check(
            "failure rate over threshold",
            body["sampling"]["failure"]["status"] == "over_threshold"
            and body["sampling"]["failure"]["failed"] == 214,
            body["sampling"]["failure"],
        )

        status, body = get(f"/v1/trackers/{TRACKER}/metrics/avg_rank/samples?from=2026-11-01&to=2026-11-04")
        check("rank samples base 2", status == 200 and body["sample_base"] == 2, body.get("sample_base"))
        check(
            "no-list mention excluded from denominator",
            any(i["mentioned"] and not i["in_denominator"] for i in body["items"]),
            "a mention without list index must be flagged out",
        )

        status, body = get("/v1/meta/methodology")
        check("methodology 6 definitions", status == 200 and len(body["definitions"]) == 6, body)

        status, body = post(
            f"/v1/trackers/{TRACKER}/questions",
            {"text": "*沈阳哪一家律所最专业\n沈阳交通事故律师怎么收费"},
        )
        check(
            "question import dedupes",
            status == 201 and body["created_count"] == 1 and body["duplicate_count"] == 1,
            body,
        )
        question_id = body["created"][0]["id"]
        status, body = patch(f"/v1/questions/{question_id}", {"tier": "core"})
        check(
            "tier change effective tomorrow",
            status == 200 and body["effective_from"] > "2026-11-11" and body["history_unchanged"],
            body,
        )

        status, body = post("/v1/trackers", {"name": "冒烟对象", "brand_name": "冒烟品牌"})
        new_tracker = body.get("id")
        check("tracker created without questions", status == 201 and body["sampling"]["daily_planned"] == 0, body)
        status, body = patch(f"/v1/trackers/{new_tracker}", {"status": "archived"})
        check("tracker archived", status == 200 and body["status"] == "archived", body)
        status, body = patch(f"/v1/trackers/{new_tracker}", {"status": "active"})
        check("archived tracker cannot reactivate", status == 409 and body["code"] == "STATE_CONFLICT", body)

        status, body = get(f"/v1/trackers/{TRACKER}/answers?from=2026-11-01&to=2026-11-03&limit=1")
        answer_id = body["items"][0]["id"]
        status, body = get(f"/v1/answers/{answer_id}")
        check(
            "answer detail has text and citations",
            status == 200 and body["text"] and body["citations"] and body["text_hash"],
            {"status": status},
        )
        status, ctype, html = get_html(f"/answer/{answer_id}")
        check("answer page served", status == 200 and "答案详情" in html, {"status": status})

        # seed 必须自带演示报告：否则用户 --reset 后报告中心是空的，演示会断在这里。
        status, seeded = get(f"/v1/trackers/{TRACKER}/reports")
        check(
            "seed ships demo reports so /reports is never empty after --reset",
            status == 200 and len(seeded["items"]) >= 2
            and any(x["comparable"] is True for x in seeded["items"]),
            {"count": len(seeded.get("items", [])), "comparable": [x.get("comparable") for x in seeded.get("items", [])]},
        )

        status, body = post(f"/v1/trackers/{TRACKER}/reports", {"from": "2026-11-01", "to": "2026-11-03"})
        check("report generated", status == 201 and body["status"] == "ready", {"status": status})
        report_id = body["id"]
        check("report hash is sha256", len(body["payload_hash"]) == 64, body.get("payload_hash"))
        check(
            "first period reported incomparable",
            body["payload"]["comparison"]["comparable"] is False,
            body["payload"]["comparison"],
        )
        check(
            "single-day report rejected",
            post(f"/v1/trackers/{TRACKER}/reports", {"from": "2026-11-03", "to": "2026-11-03"})[0] == 400,
            "period_start must be < period_end",
        )

        status, ctype, html = get_html(f"/report/{report_id}")
        check("report page served", status == 200 and "GEO Insight" in html, {"status": status})
        status, body = get(f"/v1/reports/{report_id}")
        check("report snapshot readable", status == 200 and body["payload_hash"], {"status": status})

        status, again = post(f"/v1/trackers/{TRACKER}/reports", {"from": "2026-11-01", "to": "2026-11-03"})
        check(
            "repeated report generation reuses the snapshot",
            status == 201 and again["id"] == report_id and again.get("reused") is True,
            {"id": again.get("id"), "reused": again.get("reused")},
        )

        status, body = post(f"/v1/trackers/{TRACKER}/reports", {"from": "2026-11-04", "to": "2026-11-05"})
        comparison = body["payload"]["comparison"]
        check(
            "comparable report outputs delta",
            comparison["comparable"] is True and comparison["delta_pp"] is not None,
            comparison,
        )

        with urllib.request.urlopen(
            BASE + f"/v1/trackers/{TRACKER}/answers/export?from=2026-11-01&to=2026-11-03&format=csv", timeout=5
        ) as resp:
            raw = resp.read()
        check("csv export has BOM and header", raw[:3] == b"\xef\xbb\xbf" and b"question_text" in raw, raw[:80])

        print("\nALL SMOKE CHECKS PASSED")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
