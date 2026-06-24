#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
カワラボ スケジュール自動更新スクリプト
==========================================
KAWAII LAB. 公式サイトのスケジュール一覧＋各イベント詳細ページを巡回し、
「いつ・誰が・どこで・何のライブ・チケット・締切」を抽出して data/schedule.json に書き出す。

- 標準ライブラリ(urllib / re / json / datetime / html)だけで動く(このMacはnpm等が無いため)。
- 取得に失敗したら既存データを壊さず保持し、logs/ に記録する。
- launchd から1時間ごとに呼ばれる想定(インストール_自動更新.command 参照)。

出典(取得元):
  一覧 : https://kawaiilab.asobisystem.com/live_information/schedule/list?year=YYYY&month=MM
  詳細 : https://kawaiilab.asobisystem.com/live_information/detail/<ID>
"""

import json
import os
import re
import sys
import time
import html as htmllib
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---- パス設定 ---------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent        # カワラボ_スケジュール/
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
SCHEDULE_JSON = DATA_DIR / "schedule.json"
GROUPS_JSON = DATA_DIR / "groups.json"

BASE = "https://kawaiilab.asobisystem.com"
LIST_URL = BASE + "/live_information/schedule/list"
DETAIL_URL = BASE + "/live_information/detail/{id}"

JST = timezone(timedelta(hours=9))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

MONTHS_AHEAD = 6        # 今月から何か月先まで取りに行くか
REFRESH_WINDOW_DAYS = 60  # この日数以内のイベントは毎回詳細を取り直す(チケット状況が変わるため)
SLEEP = 0.4            # サーバー負荷をかけないための待ち(秒)
# 正確性優先: 上の窓より先のイベントも、1日に1回は詳細を取り直す(会場/出演/時刻の更新を取りこぼさない)。
# 同じ日のうちはキャッシュを使い、取得元サーバーへの負荷を抑える。


# ---- ログ -------------------------------------------------------------------
def log(msg):
    LOG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line)
    with open(LOG_DIR / "update.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _atomic_write_json(path, obj):
    """一時ファイルに書いてから os.replace で差し替える(原子的書き込み)。
    途中で中断しても、本番の schedule.json が壊れた半端な状態にならない。
    GitHub Actions で公開配信する場合も、壊れたファイルを配らないために重要。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---- HTTP -------------------------------------------------------------------
def fetch(url, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.0 + i)
    raise last


# ---- HTML ヘルパ ------------------------------------------------------------
def strip_tags(s, keep_breaks=True):
    if keep_breaks:
        s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
        s = re.sub(r"</(p|div|li)>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t　]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def extract_links(block):
    return re.findall(r'href="(https?://[^"]+)"', block)


def norm_venue(s):
    return re.sub(r'[\s　・.,，、]', '', (s or "")).lower()


# ---- 一覧ページの解析 -------------------------------------------------------
def parse_list(html, year):
    """月別一覧ページから {id, category, month, date, youbi, title} の配列を返す。"""
    events = []
    # 各イベントは <a href="/live_information/detail/ID" ...> ... </a>
    for m in re.finditer(
        r'<a\s+href="/live_information/detail/(\d+)"[^>]*class="box[^"]*"[^>]*>(.*?)</a>',
        html, re.S,
    ):
        eid, block = m.group(1), m.group(2)
        mon = re.search(r'block--date__month">\s*(\d{1,2})', block)
        day = re.search(r'block--date__date">\s*(\d{1,2})', block)
        ybi = re.search(r'block--date__youbi">\s*\[?([A-Za-z]+)', block)
        cat = re.search(r'class="category">\s*([^<]+?)\s*</', block)
        tit = re.search(r'class="tit">(.*?)</p>', block, re.S)
        if not (mon and day):
            continue
        events.append({
            "id": eid,
            "year": year,
            "month": int(mon.group(1)),
            "day": int(day.group(1)),
            "youbi": ybi.group(1) if ybi else "",
            "category": cat.group(1).strip() if cat else "",
            "title_short": strip_tags(tit.group(1)) if tit else "",
        })
    return events


# ---- 詳細ページの解析 -------------------------------------------------------
def parse_detail(html):
    out = {
        "date": None, "venue": "", "prefecture": "",
        "open": "", "start": "", "ticket_url": "", "ticket_status": "",
        "ticket_info": "", "price": "", "deadline": "", "info": "", "title": "",
    }

    # タイトル(詳細ページの見出し)
    t = re.search(r'class="detail--?ttl[^"]*">(.*?)</', html, re.S) \
        or re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
    if t:
        out["title"] = strip_tags(t.group(1))

    # 日付 2026.06.03
    d = re.search(r'class="item-detail">\s*(\d{4})\.(\d{2})\.(\d{2})', html)
    if d:
        out["date"] = f"{d.group(1)}-{d.group(2)}-{d.group(3)}"

    # 会場(開催場所・会場 の直後の item-detail)
    v = re.search(r'開催場所・会場.*?<div class="item-detail">(.*?)</div>', html, re.S)
    if v:
        block = v.group(1)
        pref = re.search(r'class="prefecture">([^<]*)</span>', block)
        if pref:
            out["prefecture"] = pref.group(1).strip()
        out["venue"] = strip_tags(re.sub(r'<span class="prefecture">.*?</span>', "", block, flags=re.S))

    # INFO 本文(block--editor)
    info_m = re.search(r'block--editor">(.*?)</div>\s*</li>', html, re.S) \
        or re.search(r'INFO.*?block--editor">(.*?)</div>', html, re.S)
    info_block = info_m.group(1) if info_m else ""
    info_text = strip_tags(info_block)
    out["info"] = info_text[:600]

    # OPEN / START 時刻
    o = re.search(r'OPEN[\s　]*([0-9]{1,2}[:：][0-9]{2})', info_text, re.I) \
        or re.search(r'開場[\s　:：]*([0-9]{1,2}[:：][0-9]{2})', info_text)
    s = re.search(r'START[\s　]*([0-9]{1,2}[:：][0-9]{2})', info_text, re.I) \
        or re.search(r'開演[\s　:：]*([0-9]{1,2}[:：][0-9]{2})', info_text)
    if o:
        out["open"] = o.group(1).replace("：", ":")
    if s:
        out["start"] = s.group(1).replace("：", ":")

    # チケットURL: INFO内の外部リンクを優先 → 無ければ公式のお知らせ/詳細リンク
    all_links = extract_links(info_block)
    ext = [u for u in all_links if "asobisystem" not in u]
    if ext:
        out["ticket_url"] = ext[0]
    else:
        news = [u for u in all_links if "news" in u or "detail" in u]
        if news:
            out["ticket_url"] = news[0]

    lines = [ln.strip() for ln in info_text.splitlines() if ln.strip()]

    # チケット状況(現在の状態がはっきり分かる語のみ)
    for kw in ["一般発売中", "チケット発売中", "発売中", "完売", "SOLD OUT", "受付終了"]:
        if kw in info_text:
            out["ticket_status"] = kw
            break

    # チケット情報: まず 🎫 の行を優先(具体的な期間が入る)、無ければチケット系の最初の行
    tix_idx = None
    for i, ln in enumerate(lines):
        if "🎫" in ln:
            tix_idx = i
            break
    if tix_idx is None:
        for i, ln in enumerate(lines):
            if re.search(r'(チケット|先行|受付|抽選|申込|一般販売)', ln):
                tix_idx = i
                break
    if tix_idx is not None:
        combo = lines[tix_idx]
        for nxt in lines[tix_idx + 1:tix_idx + 3]:
            if re.search(r'\d', nxt) and not re.search(r'(出演|📍|会場|OPEN|START|開場|開演|🔗|http|≪)', nxt):
                combo += " " + nxt
            else:
                break
        out["ticket_info"] = combo[:170]

    # 料金(¥/￥/料金 を含む行)
    for ln in lines:
        if "料金" in ln or "¥" in ln or "￥" in ln or ("円" in ln and ("チケット" in ln or "前売" in ln or "席" in ln)):
            out["price"] = ln[:120]
            break

    # 締切(明確な締切表現のみ。先行期間は ticket_info 側に入る)
    for i, ln in enumerate(lines):
        if re.search(r'(締切|締め切り|〆切|応募期限|受付期限|販売期限)', ln):
            combo = ln
            for nxt in lines[i + 1:i + 2]:
                if re.search(r'\d', nxt) and not re.search(r'(出演|📍|会場|🔗|http)', nxt):
                    combo += " " + nxt
            out["deadline"] = combo[:140]
            break

    return out


# ---- Eventernote(グループ個別の先々のツアー/フェス) ------------------------
def parse_eventernote(html, group_id):
    """グループのEventernoteイベント一覧から今後の予定を抽出する。"""
    events = []
    parts = re.split(r'<li class="clearfix\s*">', html)
    for block in parts[1:]:
        d = re.search(r'class="day\d">\s*(\d{4}-\d{2}-\d{2})', block)
        if not d:
            continue
        wd = re.search(r'class="wday\d">\s*([^<]+)', block)
        t = re.search(r'<h4><a href="/events/(\d+)">(.*?)</a>', block, re.S)
        title = strip_tags(t.group(2)) if t else ""
        if not title:
            alt = re.search(r'<img[^>]*alt="([^"]+)"', block)
            title = htmllib.unescape(alt.group(1)) if alt else ""
        eid = "evt" + t.group(1) if t else ("evt-" + d.group(1) + "-" + group_id)
        v = re.search(r'会場:\s*<a href="/places/\d+">([^<]+)</a>', block)
        venue = strip_tags(v.group(1)) if v else ""
        tm = re.search(r'開場\s*([0-9]{1,2}[:：][0-9]{2}).*?開演\s*([0-9]{1,2}[:：][0-9]{2})', block, re.S)
        op = tm.group(1).replace("：", ":") if tm else ""
        st = tm.group(2).replace("：", ":") if tm else ""
        url_m = re.search(r'<h4><a href="(/events/\d+)"', block)
        cat = "LIVE"
        if re.search(r'(フェス|FES|FESTIVAL|祭)', title, re.I):
            cat = "FES"
        events.append({
            "id": eid,
            "date": d.group(1),
            "youbi": "",
            "category": cat,
            "title": title,
            "groups": [group_id],
            "venue": venue,
            "prefecture": "",
            "open": op,
            "start": st,
            "ticket_url": "",
            "ticket_status": "",
            "ticket_info": "",
            "price": "",
            "deadline": "",
            "info": "",
            "detail_url": "https://www.eventernote.com" + url_m.group(1) if url_m else "",
            "source": "eventernote",
        })
    return events


def merge_events(official, evt_events, today):
    """公式イベントにEventernoteイベントを統合(同日・同会場は重複とみなしグループを合算)。"""
    result = list(official)

    def same_event(a, b):
        if a["date"] != b["date"]:
            return False
        va, vb = norm_venue(a.get("venue")), norm_venue(b.get("venue"))
        # 会場名は「一方が他方を内包」かつ十分な長さのときだけ同一とみなす
        # (zepp 等の施設プレフィックス4文字一致での誤統合を避ける)
        if va and vb and min(len(va), len(vb)) >= 5 and (va in vb or vb in va):
            return True
        # 同日でタイトルが一致(または一方が他方を含む長い一致)なら同一イベント
        ta, tb = norm_venue(a.get("title")), norm_venue(b.get("title"))
        if ta and tb and ta == tb:
            return True
        if ta and tb and min(len(ta), len(tb)) >= 12 and (ta in tb or tb in ta):
            return True
        return False

    for ev in evt_events:
        try:
            if datetime.strptime(ev["date"], "%Y-%m-%d").date() < today:
                continue
        except ValueError:
            continue
        merged = False
        for o in result:
            if same_event(o, ev):
                for g in ev["groups"]:
                    if g not in o["groups"]:
                        o["groups"].append(g)
                # 時刻が公式に無くEventernoteにあれば補完
                if not o.get("open") and ev.get("open"):
                    o["open"] = ev["open"]
                if not o.get("start") and ev.get("start"):
                    o["start"] = ev["start"]
                merged = True
                break
        if not merged:
            result.append(ev)
    return result


# ---- 複数日まとめ詳細の「その日」だけを切り出す ----------------------------
def slice_info_for_date(info, month, day):
    """1ページに複数日の出演者が書かれている場合、その日の見出しブロックだけを返す。
    例: 「🗓️6月12日…👥参加グループ FRUITS ZIPPER…」の6/12分だけ。単日なら全文を返す。"""
    if not info:
        return info
    # 「M月D日」形式の見出しを優先(構造化された出演者ブロックの目印)
    headers = [(m.start(), int(m.group(1)), int(m.group(2)))
               for m in re.finditer(r'(\d{1,2})月(\d{1,2})日', info)]
    distinct = {(a, b) for _, a, b in headers}
    if len(distinct) < 2:
        return info  # 単日 or 日付見出し無し → 全文で判定
    starts = [pos for pos, a, b in headers if a == month and b == day]
    if not starts:
        return info
    start = min(starts)
    end = len(info)
    # 次の別日付の見出しまで
    for pos, a, b in sorted(headers):
        if pos > start and (a, b) != (month, day):
            end = min(end, pos)
            break
    # 日をまたぐ「CD販売/参加券」等のセクションが続く場合はそこで打ち切る
    for marker in ["💿", "📸特典会参加券", "CD販売購入", "特典会参加券販売", "🎫"]:
        p = info.find(marker, start)
        if start < p < end:
            end = p
    return info[start:end]


# ---- グループ判定 -----------------------------------------------------------
def build_group_matcher(groups_master):
    """テキストからグループidを推定するための (パターン, group_id) 一覧を作る。"""
    name_to_id = []
    member_to_id = {}
    for g in groups_master["groups"]:
        gid = g["id"]
        name_to_id.append((g["name"], gid))
        # 表記ゆれ
        if g["name"] == "FRUITS ZIPPER":
            name_to_id.append(("フルーツジッパー", gid))
        if g["name"] == "CUTIE STREET":
            name_to_id.append(("キューティーストリート", gid))
        if g["name"] == "CANDY TUNE":
            name_to_id.append(("キャンディーチューン", gid))
            name_to_id.append(("キャンディチューン", gid))
        if g["name"] == "SWEET STEADY":
            name_to_id.append(("スイートステディ", gid))
        for mem in g.get("members", []):
            # 「古澤 里紗」と「古澤里紗」両方
            member_to_id[mem["name"]] = gid
            member_to_id[mem["name"].replace(" ", "")] = gid
            member_to_id[mem["name"].replace("　", "")] = gid
    return name_to_id, member_to_id


def detect_groups(text, name_to_id, member_to_id):
    """テキストから具体的なグループidを返す(合同フォールバックは呼び出し側で判断)。"""
    found = []
    low = text.upper()
    for name, gid in name_to_id:
        if gid == "kawaii_lab":
            continue  # 合同は具体グループとしては数えない
        if name.upper() in low and gid not in found:
            found.append(gid)
    # メンバー名(誕生日・ソロ等)
    if not found:
        for mem, gid in member_to_id.items():
            if mem and mem in text and gid not in found:
                found.append(gid)
    return found


def detect_groups_with_fallback(title, day_info, full_info, name_to_id, member_to_id):
    """まず当日ブロックで判定。具体グループが出なければ全文で判定。
    それでも無く KAWAII LAB. 名義があれば「合同」を付ける。"""
    gids = detect_groups(title + " " + day_info, name_to_id, member_to_id)
    if not gids and day_info != full_info:
        gids = detect_groups(title + " " + full_info, name_to_id, member_to_id)
    if not gids:
        t = (title + " " + full_info)
        if "KAWAII LAB" in t.upper() or "カワイイラボ" in t or "カワラボ" in t:
            gids = ["kawaii_lab"]
    return gids


# ---- メイン -----------------------------------------------------------------
def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


# ---- 取得結果の自己点検(中身が壊れていないか) -------------------------------
def quality_check(events, prev_official, prev_eventernote):
    """取得できたデータの中身を点検し (health, warnings, stats) を返す。
    health は配信状態の信号:
      'good' = 正常 / 'warn' = 更新はできたが要注意 / 'fail' = 信頼できる更新ができず旧データ維持を推奨。

    重要: 判定は『ソース別』に行う。
      - 公式(official) = 本体。激減・全滅は fail(旧データ維持)。
      - Eventernote   = 補助(先々のツアー/フェス)。落ちても warn 止まりにし、本体まで止めない。
        (現状データは Eventernote 偏重なので、合算件数で判定すると補助の不調で誤って fail 凍結してしまう)
    空欄チェックも公式のみを分母にする(Eventernote は会場未定が正常にあり得るため)。
    通信失敗の数は呼び出し側で足す。"""
    n = len(events)
    official = [e for e in events if e.get("source") == "official"]
    evt = [e for e in events if e.get("source") != "official"]
    no_date = [e for e in events if not e.get("date")]
    no_venue_off = [e for e in official if not e.get("venue")]
    no_groups_off = [e for e in official if not e.get("groups")]
    no = len(official)
    stats = {
        "events": n,
        "official": no,
        "eventernote": len(evt),
        "empty_date": len(no_date),
        "empty_venue_official": len(no_venue_off),
        "empty_groups_official": len(no_groups_off),
        "prev_official": prev_official,
        "prev_eventernote": prev_eventernote,
    }
    warnings = []

    # --- 致命的(公式=本体が壊れた → 旧データ維持を推奨) ---
    if prev_official > 0 and no == 0:
        return "fail", [f"公式予定が0件(前回{prev_official}件)。サイト構造変更の可能性。"], stats
    if prev_official >= 5 and no <= prev_official * 0.4:
        return ("fail",
                [f"公式予定が前回{prev_official}件→今回{no}件に激減(6割超減)。取得失敗の可能性。"],
                stats)
    if n == 0:
        return "fail", ["取得結果が0件。サイト構造変更の可能性。"], stats

    # --- 要注意(更新はするが警告を残す) ---
    # 公式の中程度の減少(本体の取りこぼし)
    if prev_official >= 5 and no <= prev_official * 0.7:
        warnings.append(f"公式予定が前回{prev_official}件→今回{no}件に減少(3割超減)。")
    # 小規模時の安全網: 前回が少数(2〜4件)でも半減かつ今回1件以下なら警告(prev>=5 の網からこぼれる範囲)
    elif 2 <= prev_official < 5 and no <= prev_official * 0.5:
        warnings.append(f"公式予定が前回{prev_official}件→今回{no}件に減少。")
    # Eventernote(補助)の激減は warn 止まり。fail には昇格させない。
    if prev_eventernote >= 10 and len(evt) <= prev_eventernote * 0.4:
        warnings.append(
            f"Eventernote由来の予定が前回{prev_eventernote}件→今回{len(evt)}件に激減。"
            "先々のツアー/フェスが一時的に欠けている可能性。")
    if no_date:
        warnings.append(f"日付が空の予定が{len(no_date)}件。")
    # 空会場・空グループは公式のみを分母に(Eventernote は会場/出演未定が正常にあり得るため)。
    # 下限を公式3件にしているのは、件数が少ない時でも壊れを拾えるようにするため。
    if no >= 3 and len(no_venue_off) / no > 0.30:
        warnings.append(f"公式予定で会場が空のものが{len(no_venue_off)}/{no}件(3割超)。")
    if no >= 3 and len(no_groups_off) / no > 0.40:
        warnings.append(
            f"公式予定の出演グループが空のものが{len(no_groups_off)}/{no}件(4割超)。"
            "グループ判定が壊れている可能性。")

    return ("warn" if warnings else "good"), warnings, stats


def main():
    groups_master = load_json(GROUPS_JSON, {"groups": []})
    name_to_id, member_to_id = build_group_matcher(groups_master)

    old = load_json(SCHEDULE_JSON, {"events": []})
    cache = {e["id"]: e for e in old.get("events", []) if "id" in e}

    today = datetime.now(JST).date()
    log(f"=== 更新開始 (today={today}) ===")

    # 取得対象の (year, month) 一覧
    ym = []
    y, m = today.year, today.month
    for _ in range(MONTHS_AHEAD + 1):
        ym.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1

    list_events = []
    list_fail = 0
    for (yy, mm) in ym:
        url = f"{LIST_URL}?viewMode=default&year={yy}&month={mm:02d}"
        try:
            html = fetch(url)
            evs = parse_list(html, yy)
            # 年跨ぎ補正(12月ページに翌1月が混在する等)
            for e in evs:
                if e["month"] < mm and (mm - e["month"]) >= 6:
                    e["year"] = yy + 1
                elif e["month"] > mm and (e["month"] - mm) >= 6:
                    e["year"] = yy - 1
            log(f"一覧 {yy}-{mm:02d}: {len(evs)}件")
            list_events.extend(evs)
            time.sleep(SLEEP)
        except Exception as e:  # noqa: BLE001
            log(f"一覧取得失敗 {yy}-{mm:02d}: {e}")
            list_fail += 1

    # 重複id除去(月をまたぐ複数日イベントが両方に出ることがある)
    seen = {}
    for e in list_events:
        seen[e["id"]] = e
    list_events = list(seen.values())

    if not list_events:
        log("一覧から0件。サイト構造変更の可能性。既存データを保持して終了。")
        _write_meta(old, ok=False, health="fail",
                    warnings=["公式の月別一覧が全て取得できませんでした(0件)。サイト構造変更の可能性。"],
                    stats={"events": 0, "official": 0, "list_month_failures": list_fail},
                    note="一覧0件のため既存維持")
        return

    events = []
    fetched = 0
    detail_fail = 0
    for le in list_events:
        eid = le["id"]
        date_str = f"{le['year']}-{le['month']:02d}-{le['day']:02d}"
        try:
            ev_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            ev_date = today

        # 近いイベントは毎回詳細を取り直す。先のイベントも「今日まだ取得していなければ」取り直す
        # (正確性優先: 1日1回は遠い予定も最新化。同日内はキャッシュ再利用でサーバー負荷を抑える)。
        near = ev_date <= today + timedelta(days=REFRESH_WINDOW_DAYS)
        cached = cache.get(eid)
        fresh_today = bool(cached) and cached.get("_fetched") == today.isoformat()
        if cached and not near and cached.get("venue") and fresh_today:
            ev = dict(cached)
            # 一覧側の最新値で軽く上書き
            ev["category"] = le["category"] or ev.get("category", "")
            events.append(ev)
            continue

        det_ok = False
        try:
            dhtml = fetch(DETAIL_URL.format(id=eid))
            det = parse_detail(dhtml)
            fetched += 1
            det_ok = True
            time.sleep(SLEEP)
        except Exception as e:  # noqa: BLE001
            log(f"詳細取得失敗 id={eid}: {e}")
            det = cached or {}
            detail_fail += 1

        title = (det.get("title") or le.get("title_short") or "").strip()
        # 複数日まとめページは「その日」の出演者ブロックを優先。
        # ただし出演が日ごとに分かれておらず当日ブロックに無い場合は全文で取り直す。
        full_info = det.get("info", "")
        day_info = slice_info_for_date(full_info, le["month"], le["day"])
        title_all = title + " " + le.get("title_short", "")
        gids = detect_groups_with_fallback(title_all, day_info, full_info, name_to_id, member_to_id)

        ev = {
            "id": eid,
            "date": det.get("date") or date_str,
            "youbi": le.get("youbi", ""),
            "category": le.get("category", ""),
            "title": title or le.get("title_short", ""),
            "groups": gids,
            "venue": det.get("venue", ""),
            "prefecture": det.get("prefecture", ""),
            "open": det.get("open", ""),
            "start": det.get("start", ""),
            "ticket_url": det.get("ticket_url", ""),
            "ticket_status": det.get("ticket_status", ""),
            "ticket_info": det.get("ticket_info", ""),
            "price": det.get("price", ""),
            "deadline": det.get("deadline", ""),
            "info": det.get("info", ""),
            "detail_url": DETAIL_URL.format(id=eid),
            "source": "official",
            # 最後に詳細を「取得できた」日(遠い予定の1日1回再取得の判定に使う)。
            # 失敗してキャッシュ代用したときは前回値を引き継ぎ、同日中の再取得を許す。
            "_fetched": today.isoformat() if det_ok else (cached or {}).get("_fetched", ""),
        }
        events.append(ev)

    log(f"公式 詳細を新規取得: {fetched}件 / 公式イベント: {len(events)}件")

    # --- Eventernote でグループ個別の先々のツアー/フェスを補完 ---
    # 失敗時の代用元: 前回の Eventernote 予定をグループidごとに引けるようにしておく。
    old_evt_by_group = {}
    for e in old.get("events", []):
        if e.get("source") == "eventernote":
            for gid in e.get("groups", []):
                old_evt_by_group.setdefault(gid, []).append(e)

    evt_all = []
    evt_fail = 0
    for g in groups_master.get("groups", []):
        url = g.get("eventernote", "")
        m = re.search(r'/actors/.+?/(\d+)', url)
        if not m:
            continue
        try:
            ehtml = fetch(url.rstrip("/") + "/events")
            evs = parse_eventernote(ehtml, g["id"])
            log(f"Eventernote {g['name']}: {len(evs)}件")
            evt_all.extend(evs)
            time.sleep(SLEEP)
        except Exception as e:  # noqa: BLE001
            log(f"Eventernote取得失敗 {g['name']}: {e}")
            evt_fail += 1
            # 補助ソースが落ちても先々の予定を失わないよう、前回分で代用する
            cached_evs = old_evt_by_group.get(g["id"], [])
            if cached_evs:
                evt_all.extend(cached_evs)
                log(f"  → {g['name']}: 前回のEventernote予定{len(cached_evs)}件で代用")

    # キャッシュ代用で同一イベントが重複しうるので id で一意化する
    uniq = {}
    for e in evt_all:
        uniq[e.get("id")] = e
    evt_all = list(uniq.values())

    before = len(events)
    events = merge_events(events, evt_all, today)
    log(f"Eventernote統合: +{len(events) - before}件(新規) / 合計 {len(events)}件")

    events.sort(key=lambda e: (e["date"], e.get("start", "")))

    # --- 自己点検(取得した中身が壊れていないか) ---
    prev_official = len([e for e in old.get("events", []) if e.get("source") == "official"])
    prev_eventernote = len([e for e in old.get("events", []) if e.get("source") != "official"])
    health, warnings, stats = quality_check(events, prev_official, prev_eventernote)
    detail_attempted = fetched + detail_fail   # 実際に詳細取得を試みた件数(キャッシュ再利用分は除く)
    stats["list_month_failures"] = list_fail
    stats["detail_attempted"] = detail_attempted
    stats["detail_failures"] = detail_fail
    stats["eventernote_failures"] = evt_fail

    # 通信失敗の数も health / warnings に反映する
    # 全滅判定は「試みた件数」が分母(キャッシュでスキップした分を含めない)。
    if detail_attempted > 0 and detail_fail >= detail_attempted:
        health = "fail"
        warnings.insert(0, "公式の詳細ページが(取得を試みた分)全件失敗。サイト構造変更の可能性。")
    elif detail_fail:
        warnings.append(f"公式の詳細ページ{detail_fail}件が取得失敗(その分は前回値で代用)。")
        if health == "good":
            health = "warn"
    if list_fail:
        warnings.append(f"公式の月別一覧{list_fail}か月分が取得失敗。")
        if health == "good":
            health = "warn"
    if evt_fail:
        # Eventernote は補助ソース。落ちても前回分で代用済みなので warn 止まり。
        warnings.append(f"Eventernote{evt_fail}グループ分が取得失敗(その分は前回の予定で代用)。")
        if health == "good":
            health = "warn"

    # 致命的: 信頼できる更新ができない。旧データがあれば壊さず維持する。
    if health == "fail" and old.get("events"):
        log("自己点検で致命的な異常を検知。新データを書かず旧データを維持: " + " / ".join(warnings))
        _write_meta(old, ok=False, health="fail", warnings=warnings, stats=stats)
        return

    now = datetime.now(JST).isoformat(timespec="seconds")
    out = {
        "meta": {
            "last_updated": now,
            "last_attempt": now,
            "source": BASE + "/live_information/schedule/list",
            "event_count": len(events),
            "ok": health != "fail",   # 後方互換(従来の ok フラグ)
            "health": health,         # good / warn / fail
            "warnings": warnings,     # 画面に出す注意書き
            "stats": stats,           # 点検に使った内訳(件数・空欄数・失敗数)
        },
        "events": events,
    }
    DATA_DIR.mkdir(exist_ok=True)
    _atomic_write_json(SCHEDULE_JSON, out)
    log(f"=== 更新完了: {len(events)}件 health={health}"
        + (f" warnings={len(warnings)}件" if warnings else "")
        + f" -> {SCHEDULE_JSON.name} ===")
    if warnings:
        log("注意: " + " / ".join(warnings))


def _write_meta(old, ok, note="", health=None, warnings=None, stats=None):
    old.setdefault("meta", {})
    old["meta"]["last_attempt"] = datetime.now(JST).isoformat(timespec="seconds")
    old["meta"]["ok"] = ok
    if health is not None:
        old["meta"]["health"] = health
    if warnings is not None:
        old["meta"]["warnings"] = warnings
    if stats is not None:
        old["meta"]["stats"] = stats
    if note:
        old["meta"]["note"] = note
    _atomic_write_json(SCHEDULE_JSON, old)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log(f"!!! 想定外エラー: {e}")
        sys.exit(1)
