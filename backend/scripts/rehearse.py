"""试点全链路离线演练：建对象 → 采集 → 识别 → 发布 → 判定，全在临时库跑。

用途：key 还没批、渠道还没扫码时，验证「代码串起来没断」。
**这里的数据全是假的，结论没有业务意义**——不能拿演练的 effective 去写客户案例。
真结论必须走真平台回答，见 docs/pilot-protocol.md。

跑法：

    python scripts/rehearse.py

它会做六步，每步打印实际数字，任何一步对不上就退出码 1：
  1. 建试点对象（setup_pilot 的逻辑，目标 + 对照 + 3 次/天）
  2. 离线采集填满 3 天基线（假平台回答，含「辽宁辽征」字样）
  3. 增量识别提及 → 与全量重算对拍（两条路径必须相等）
  4. 造一篇已发布文章 + 发布记录（URL 用假域名，只验证归因口径）
  5. 调 publish_impact(question scope) → 检查 verdict 结构完整
  6. 用 WAL 备份演练：备份 → 删一条 → 恢复 → 数字回来

不碰真实 geo.db：整个过程在 tempfile 里，结束自动清理。
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

# 演练用的假数据，一眼能和真数据分开
BRAND = "辽宁辽征"
TARGET = "沈阳离婚律师收费标准是多少"
CONTROL = "沈阳合同纠纷律师怎么找"
ANSWER_WITH_BRAND = f"在沈阳，{BRAND}律所的收费按案件类型分档，具体可查公开标准。"
ANSWER_WITHOUT_BRAND = "沈阳的律师收费按案件类型分档，建议向当地律协查询公开标准。"
BASE_DAYS = 3
PER_DAY = 3
WINDOW_DAYS = 7  # 判定窗口至少 21 条；演练填 3 天 × 3 次 = 9 条，只会得到 insufficient


class RehearsalError(Exception):
    pass


def _check(label: str, actual, expected) -> None:
    if actual != expected:
        raise RehearsalError(f"{label}: 期望 {expected!r}，实际 {actual!r}")
    print(f"  ✓ {label}: {actual}")


def run() -> int:
    # 必须在 import geo.settings 之前设好——它是 frozen dataclass，import 时就固定了路径。
    # 顺序反了会写到真实 geo.db，这是演练脚本最危险的一种 bug。
    # Windows 上 sqlite 连接偶发延迟释放，cleanup 允许忽略删不掉的文件；
    # 演练数据只在临时目录，留几个文件不会污染项目。
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    db_path = Path(tmp.name) / "rehearse.db"
    os.environ["GEO_DB_PATH"] = str(db_path)
    # 备份目录也指到临时目录，避免演练污染真实 backups/
    os.environ["GEO_BACKUP_DIR"] = str(Path(tmp.name) / "backups")

    from geo import migrate
    from geo.repositories import config_repository as config_repo
    from geo.services import backup_service, content_service
    from geo.services import mention_service, sampling_service
    from geo.settings import settings

    # settings 是 frozen dataclass，环境变量在 import 时已读取。
    # 演练库用 migrate 建骨架 + 手工造租户，不跑 seed（seed 会灌演示数据，掩盖问题）。
    try:
        print("═" * 56)
        print("试点全链路离线演练（数据全是假的，结论无业务意义）")
        print("═" * 56)

        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        migrate.apply(conn)
        now = "2026-11-25T12:00:00+08:00"
        tenant = settings.default_tenant_id
        conn.execute(
            "INSERT INTO tenant(id,name,slug,created_at) VALUES (?,?,?,?)",
            (tenant, "演练租户", "rehearse", now),
        )
        conn.commit()

        # ---- 1. 建对象（复用 setup_pilot 的四步，不另写一份）----
        print("\n[1/6] 建试点对象")
        import argparse

        from setup_pilot import setup as setup_tracker

        pilot_args = argparse.Namespace(
            name="演练试点", brand=BRAND, target=TARGET, control=CONTROL,
            aliases="", competitors="", keywords="", model="doubao",
            core_times=PER_DAY, long_tail_times=1,
        )
        result = setup_tracker(conn, pilot_args)
        tracker_id = result["tracker_id"]
        set_id = result["question_set_id"]
        _check("活动问题集已设", len(list(conn.execute(
            "SELECT id FROM question WHERE question_set_id = ?", (set_id,)
        ))), 2)

        # ---- 2. 离线填满基线（直接写 answer，模拟采集落库的结果）----
        print("\n[2/6] 填 3 天基线（每天 2 问题 × 3 次）")
        questions = list(conn.execute(
            "SELECT id, text FROM question WHERE question_set_id = ?", (set_id,)
        ))
        target_q = next(q for q in questions if q["text"] == TARGET)
        control_q = next(q for q in questions if q["text"] == CONTROL)

        base = date(2026, 11, 20)
        inserted = 0
        for offset in range(BASE_DAYS):
            day = (base + timedelta(days=offset)).isoformat()
            for slot in range(PER_DAY):
                for question in questions:
                    text = ANSWER_WITH_BRAND if question["id"] == target_q["id"] else ANSWER_WITHOUT_BRAND
                    answer_id = config_repo.uid()
                    conn.execute(
                        """INSERT INTO answer(id, tenant_id, tracker_id, question_id, model_key,
                                              channel, raw_uri, text, text_hash, collected_at,
                                              row_hash, sample_day, sample_idx, search_called, search_count)
                           VALUES (?,?,?,?,?,'api','collect://rehearse',?,?,?,?,?,?,1,1)""",
                        (
                            answer_id, tenant, tracker_id, question["id"], "doubao",
                            text, f"h-{answer_id}", f"{day}T10:00:00+08:00",
                            f"r-{day}-{question['id']}-{slot}", day, slot + 1,
                        ),
                    )
                    # 模拟采集后增量识别
                    mention_service.detect_for_answer(
                        conn, answer_id=answer_id, tenant_id=tenant, tracker_id=tracker_id
                    )
                    inserted += 1
        conn.commit()
        _check("落库答案数", inserted, BASE_DAYS * PER_DAY * 2)

        gaps = sampling_service.compute_gaps(
            conn, tracker_id,
            (base + timedelta(days=BASE_DAYS - 1)).isoformat(),
            (base + timedelta(days=BASE_DAYS - 1)).isoformat(),
            model="doubao",
        )
        _check("末日缺口已归零", gaps["totals"]["missing"], 0)

        # ---- 3. 增量识别 vs 全量重算 ----
        print("\n[3/6] 增量识别与全量重算对拍")
        before = conn.execute(
            "SELECT COUNT(*) FROM mention WHERE answer_id IN (SELECT id FROM answer WHERE tracker_id=?)",
            (tracker_id,),
        ).fetchone()[0]
        recomputed = mention_service.recompute(conn, tenant_id=tenant, tracker_id=tracker_id, actor_id=None)
        after = conn.execute(
            "SELECT COUNT(*) FROM mention WHERE answer_id IN (SELECT id FROM answer WHERE tracker_id=?)",
            (tracker_id,),
        ).fetchone()[0]
        _check("重算前后提及数一致", after, before)
        del recomputed

        # ---- 4. 造已发布文章（只验归因口径，URL 是假的）----
        print("\n[4/6] 造已发布文章与发布记录")
        article_id = config_repo.uid()
        channel_id = config_repo.uid()
        pub_id = config_repo.uid()
        conn.execute(
            """INSERT INTO channel_account(id,tenant_id,platform_key,platform_name,status,login_mode)
               VALUES (?,?,'zhihu','知乎','connected','scan_qr')""",
            (channel_id, tenant),
        )
        conn.execute(
            """INSERT INTO article(id,tenant_id,tracker_id,question_id,title,body,status,version,created_at,updated_at)
               VALUES (?,?,?,?,?,?,'published',1,?,?)""",
            (article_id, tenant, tracker_id, target_q["id"], "演练标题", "演练正文", now, now),
        )
        conn.execute(
            """INSERT INTO publication(id,article_id,channel_id,status,published_url,published_at,created_at)
               VALUES (?,?,?,'published','https://example.invalid/rehearse',?,?)""",
            (pub_id, article_id, channel_id, now, now),
        )
        conn.commit()

        # ---- 5. 判定结构 ----
        print("\n[5/6] 判定（要验证的是结构，不是结论）")
        impact = content_service.publish_impact(
            conn, article_id, WINDOW_DAYS, scope="question", control_question_id=control_q["id"]
        )
        verdict = impact.get("verdict") or {}
        _check("scope 是 question", impact.get("scope"), "question")
        _check("有预注册规则", verdict.get("rule", {}).get("target_lift_pp"), 20.0)
        # 演练只有 9 条/窗，按规则必然不足——这正说明阈值不是摆设
        _check("样本不足时不下结论", verdict.get("status"), "insufficient")

        # ---- 6. 备份恢复演练 ----
        print("\n[6/6] 备份 → 删一条 → 恢复")
        conn.close()
        import importlib

        importlib.reload(backup_service)
        snapshot = backup_service.create_backup(db_path)
        _check("备份里答案数", snapshot["answers"], inserted)

        check_conn = sqlite3.connect(db_path)
        check_conn.execute("PRAGMA foreign_keys = OFF")
        check_conn.execute("DELETE FROM answer WHERE id = (SELECT id FROM answer LIMIT 1)")
        check_conn.commit()
        _check("删掉一条后", check_conn.execute("SELECT COUNT(*) FROM answer").fetchone()[0], inserted - 1)
        check_conn.close()

        restored = backup_service.restore_backup(snapshot["path"], db_path, make_safety_copy=False)
        _check("恢复后答案数", restored["after"]["answers"], inserted)

        print("\n" + "═" * 56)
        print("全链路演练通过：建对象 / 采集 / 识别 / 归因 / 判定 / 备份 六步都通")
        print("再次强调：以上是假数据，verdict=insufficient 是样本不足，不是试点结论")
        print("═" * 56)
        return 0
    except Exception as exc:
        print(f"\n[x] 演练失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        tmp.cleanup()


if __name__ == "__main__":
    raise SystemExit(run())
