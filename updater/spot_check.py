#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
カワラボ スケジュール 週次「AI点検」の下ごしらえ
=================================================
直近の公式予定をいくつか選び、公式の生ページを取り直して、
schedule.json の中身(日付・会場・タイトル・出演グループ)とズレが無いか機械的に突き合わせる。

- 毎時の自動更新(update_schedule.py)とは別物。週1回くらい、AI(または人)が
  「本当に合っているか」を確かめるための"材料"を作るスクリプト。
- ここで出る report は『疑わしい点の洗い出し』。最終判断はAI/人が公式ページを見て行う。
  例: Toi Toi Toi / log you 等は同じ事務所でも PEAK SPOT など"別プロジェクト"のグループなので、
      公式の出演表記に名前があっても、KAWAII LAB. だけを載せるこのアプリでは非掲載が正しい。
      (だからグループ名は groups.json に載っている KAWAII LAB. のグループだけを照合対象にする)

使い方:  python3 updater/spot_check.py [件数(既定8)]
出力:    logs/spot_check_YYYY-MM-DD.txt  と  .json
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import update_schedule as U   # 同じ updater/ 内の本体から関数・定数を借りる

LOG_DIR = U.LOG_DIR
SCHEDULE_JSON = U.SCHEDULE_JSON
GROUPS_JSON = U.GROUPS_JSON


def norm_txt(s):
    """タイトル/会場の比較用の正規化。本体の norm_venue(空白・中黒・記号除去＋小文字)に加え、
    半角/全角アット(@/＠)も無視して、表記ゆれによる偽の差分を減らす。"""
    return U.norm_venue(s).replace("@", "").replace("＠", "")


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    sched = U.load_json(SCHEDULE_JSON, {"events": []})
    groups_master = U.load_json(GROUPS_JSON, {"groups": []})
    # 出演グループの照合は本体(update_schedule)と同じ基準を使う。
    # こうしないと「片やグループ名のみ・片やメンバー名込み」で判定が食い違い、
    # 生誕祭(メンバー名だけのページ)などで偽の差分が毎回出てしまう。
    name_to_id, member_to_id = U.build_group_matcher(groups_master)

    today = datetime.now(U.JST).date()
    # 公式由来で detail_url があり、今日以降の予定を、近い順に limit 件
    cands = []
    for e in sched.get("events", []):
        if e.get("source") != "official" or not e.get("detail_url"):
            continue
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
        except Exception:
            continue
        if d >= today:
            cands.append(e)
    cands.sort(key=lambda e: e["date"])
    cands = cands[:limit]

    report = {
        "checked_at": datetime.now(U.JST).isoformat(timespec="seconds"),
        "count": len(cands),
        "items": [],
    }
    lines = [f"# 週次点検 {report['checked_at']}  対象 {len(cands)}件", "", ""]

    for e in cands:
        item = {"id": e["id"], "date": e["date"], "title": e.get("title", ""),
                "url": e.get("detail_url", ""), "issues": []}
        try:
            html = U.fetch(e["detail_url"])
        except Exception as ex:  # noqa: BLE001
            item["issues"].append(f"ページ取得失敗: {ex}")
            report["items"].append(item)
            lines += [f"## {e['date']} {e.get('title','')}", f"  ⚠ ページ取得失敗: {ex}", ""]
            continue

        det = U.parse_detail(html)

        # 日付・会場のズレ(正規化して完全一致を見る)
        if det.get("date") and det["date"] != e.get("date"):
            item["issues"].append(f"日付ズレ: 保存={e.get('date')} / 公式={det['date']}")
        if det.get("venue") and norm_txt(det["venue"]) != norm_txt(e.get("venue", "")):
            item["issues"].append(f"会場ズレ: 保存={e.get('venue')} / 公式={det['venue']}")
        # タイトル差: 完全一致でなくても、片方が他方を「十分な長さで」含むなら同一扱い。
        # (短い側が短すぎる内包は本物の更新を見逃すので、min長 12 以上のときだけ一致とみなす)
        ta, tb = norm_txt(det.get("title", "")), norm_txt(e.get("title", ""))
        if ta and tb and ta != tb:
            contained = (ta in tb or tb in ta) and min(len(ta), len(tb)) >= 12
            if not contained:
                item["issues"].append(f"タイトル差: 保存={e.get('title')} / 公式={det.get('title')}")

        # 出演グループ: 本体と同じ判定基準(グループ名＋メンバー名)でページ全文から検出し、保存と照合
        page_text = U.strip_tags(html)
        mentioned = U.detect_groups(page_text, name_to_id, member_to_id)
        stored = [g for g in e.get("groups", []) if g != "kawaii_lab"]
        miss = [g for g in mentioned if g not in stored]
        extra = [g for g in stored if g not in mentioned]
        if miss:
            item["issues"].append("見落とし候補(公式に名前があるがアプリ未掲載): " + ", ".join(miss))
        if extra:
            item["issues"].append("余分候補(アプリにあるが公式本文に名前なし): " + ", ".join(extra))

        item["stored_groups"] = e.get("groups", [])
        item["page_mentioned_groups"] = mentioned
        report["items"].append(item)

        lines.append(f"## {e['date']} {e.get('title','')}")
        if item["issues"]:
            for s in item["issues"]:
                lines.append(f"  ⚠ {s}")
        else:
            lines.append("  ✓ ズレなし")
        lines.append(f"  url: {e.get('detail_url','')}")
        lines.append("")
        time.sleep(getattr(U, "SLEEP", 0.4))

    issue_total = sum(len(it["issues"]) for it in report["items"])
    report["issue_total"] = issue_total
    lines[1] = ("結果: 要確認 %d件(AIが公式ページで最終判断してください)" % issue_total
                if issue_total else "結果: 機械点検ではズレは検出されませんでした")

    LOG_DIR.mkdir(exist_ok=True)
    day = today.isoformat()
    (LOG_DIR / f"spot_check_{day}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (LOG_DIR / f"spot_check_{day}.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"→ logs/spot_check_{day}.txt / .json に保存しました")


if __name__ == "__main__":
    main()
