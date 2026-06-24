/* =========================================================
   カワラボ カレンダー  ロジック
   - data/groups.json と data/schedule.json を読み込み
   - カレンダー / リスト / チケット・締切 / 物販 の4ビュー
   - グループ絞り込み・推し設定・かぶり検出・ドライブ支援(.ics/地図)
   ========================================================= */
(() => {
  "use strict";

  const DATA = { groups: [], gmap: {}, events: [], meta: {} };
  const NONE = "_none";
  const ATTEND = new Set(["LIVE", "EVENT", "FES"]); // 「現場」とみなす種別
  const WDAY = ["日", "月", "火", "水", "木", "金", "土"];

  const STATE = {
    view: "calendar",
    calY: 0, calM: 0,
    active: new Set(),
    oshiGroups: new Set(),
    oshiMembers: new Set(),
    oshiOnly: false,
  };

  // ---------- ユーティリティ ----------
  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
  const esc = (s) => (s || "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const pad = (n) => String(n).padStart(2, "0");
  const todayStr = () => {
    const d = new Date();
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  };
  const parseYMD = (s) => {
    const [y, m, d] = (s || "").split("-").map(Number);
    return new Date(y, (m || 1) - 1, d || 1);
  };
  const normVenue = (s) => (s || "").replace(/[\s　・.,，、（）()]/g, "").toLowerCase();
  const daysBetween = (a, b) => Math.round((parseYMD(b) - parseYMD(a)) / 86400000);

  const gColor = (id) => (DATA.gmap[id] && DATA.gmap[id].color) || "#b9adc4";
  const gShort = (id) => (DATA.gmap[id] && DATA.gmap[id].short) || "?";
  const gName = (id) => (DATA.gmap[id] && DATA.gmap[id].name) || "未分類";

  // 背景色(グループ色)に対して読みやすい文字色を自動で選ぶ(白文字が淡色で読めない問題の対策)
  const _lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  function relLum(hex) {
    const h = (hex || "").replace("#", "");
    if (h.length < 6) return 1;
    const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b);
  }
  // 濃文字(#3d2a47≈輝度0.03)と白文字のうち、コントラストが高い方を返す
  function readableText(hex) {
    const L = relLum(hex);
    const darkC = (Math.max(L, 0.03) + 0.05) / (Math.min(L, 0.03) + 0.05);
    const whiteC = 1.05 / (L + 0.05);
    return darkC >= whiteC ? "#3d2a47" : "#ffffff";
  }

  // ---------- 読み込み ----------
  async function loadData() {
    const bust = "?_=" + Date.now();
    const [g, s] = await Promise.all([
      fetch("data/groups.json" + bust).then((r) => r.json()),
      fetch("data/schedule.json" + bust).then((r) => r.json()),
    ]);
    DATA.groups = g.groups || [];
    DATA.gmap = {};
    DATA.groups.forEach((x) => (DATA.gmap[x.id] = x));
    DATA.gmap[NONE] = { id: NONE, name: "未分類", short: "未分類", color: "#b9adc4", members: [] };
    DATA.events = (s.events || []).slice();
    DATA.meta = s.meta || {};
  }

  function groupsPresent() {
    const set = new Set();
    let hasNone = false;
    DATA.events.forEach((e) => {
      if (!e.groups || e.groups.length === 0) hasNone = true;
      else e.groups.forEach((id) => set.add(id));
    });
    const ordered = DATA.groups.map((g) => g.id).filter((id) => set.has(id));
    if (hasNone) ordered.push(NONE);
    return ordered;
  }

  // ---------- 推し設定の保存/読み込み ----------
  function loadOshi() {
    try {
      const o = JSON.parse(localStorage.getItem("kawalab_oshi") || "{}");
      STATE.oshiGroups = new Set(o.groups || []);
      STATE.oshiMembers = new Set(o.members || []);
    } catch (e) { /* ignore */ }
  }
  function saveOshi() {
    localStorage.setItem("kawalab_oshi", JSON.stringify({
      groups: Array.from(STATE.oshiGroups),
      members: Array.from(STATE.oshiMembers),
    }));
  }
  const noteKey = (id) => "kawalab_note_" + id;
  const getNote = (id) => localStorage.getItem(noteKey(id)) || "";
  const setNote = (id, v) => localStorage.setItem(noteKey(id), v);

  // ---------- イベント判定 ----------
  const isOshiEvent = (ev) =>
    (ev.groups || []).some((id) => STATE.oshiGroups.has(id));

  function eventVisible(ev) {
    if (STATE.oshiOnly && !isOshiEvent(ev)) return false;
    const gs = ev.groups && ev.groups.length ? ev.groups : [NONE];
    return gs.some((id) => STATE.active.has(id));
  }
  function visibleEvents() {
    return DATA.events.filter(eventVisible);
  }
  function eventPrimaryColor(ev) {
    const gs = ev.groups && ev.groups.length ? ev.groups : [NONE];
    const inView = gs.find((id) => STATE.active.has(id)) || gs[0];
    return gColor(inView);
  }

  // 日付ごとのイベント (表示中フィルタ適用)
  function eventsByDate() {
    const map = {};
    visibleEvents().forEach((e) => {
      (map[e.date] = map[e.date] || []).push(e);
    });
    Object.values(map).forEach((arr) =>
      arr.sort((a, b) => (a.start || "99").localeCompare(b.start || "99")));
    return map;
  }
  // かぶり日: 同日に「現場」種別で会場が2か所以上
  function conflictInfo(arr) {
    const attend = (arr || []).filter((e) => ATTEND.has(e.category) || !e.category);
    const venues = new Set(attend.map((e) => normVenue(e.venue) || e.id));
    return { conflict: venues.size >= 2, count: attend.length };
  }

  // =========================================================
  //  描画
  // =========================================================
  function render() {
    renderUpdated();
    renderHealth();
    renderChips();
    renderView();
    renderBday();
  }

  function renderUpdated() {
    const el = $("#updated");
    const t = DATA.meta.last_updated;
    if (!t) { el.textContent = "最終更新 不明"; return; }
    const d = new Date(t);
    el.textContent = `最終更新 ${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${pad(d.getMinutes())}`;
  }

  // 自己点検バナー: 取得に問題があるとき(health が warn / fail)だけ表示する
  function renderHealth() {
    const el = $("#healthBanner");
    if (!el) return;
    const h = DATA.meta.health;
    const warns = DATA.meta.warnings || [];
    if (h !== "warn" && h !== "fail") { el.hidden = true; el.innerHTML = ""; return; }
    const fail = h === "fail";
    el.className = "health-banner " + (fail ? "health-banner--fail" : "health-banner--warn");
    const cnt = (DATA.meta.event_count != null) ? DATA.meta.event_count : DATA.events.length;
    const hasData = cnt > 0;
    const head = fail
      ? (hasData
          ? "最新の取得に一部失敗しました。表示中の予定はそのまま使えますが、一部が前回の情報のままかもしれません。大事な予定は公式サイトで最終確認を。"
          : "データの取得に失敗しました（復旧待ち）。お手数ですが公式サイトをご確認ください。")
      : "一部の情報がうまく取得できていない可能性があります。";
    const list = warns.length
      ? `<ul class="health-banner__list">${warns.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`
      : "";
    const src = DATA.meta.source || "https://kawaiilab.asobisystem.com/live_information/schedule/list";
    el.innerHTML =
      `<span class="health-banner__icon">${fail ? "⚠️" : "🔔"}</span>` +
      `<div class="health-banner__body"><strong>${esc(head)}</strong>${list}` +
      `<span class="health-banner__hint">最終確認は必ず` +
      `<a href="${esc(src)}" target="_blank" rel="noopener">公式サイト</a>でお願いします。</span></div>`;
    el.hidden = false;
  }

  function renderChips() {
    const wrap = $("#groupChips");
    wrap.innerHTML = "";
    groupsPresent().forEach((id) => {
      const on = STATE.active.has(id);
      const b = document.createElement("button");
      b.className = "chip";
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.innerHTML = `<span class="chip__dot" style="background:${gColor(id)}"></span>${esc(gShort(id))}`;
      b.title = gName(id);
      b.addEventListener("click", () => {
        if (STATE.active.has(id)) STATE.active.delete(id);
        else STATE.active.add(id);
        render();
      });
      wrap.appendChild(b);
    });
  }

  function renderView() {
    $$(".view").forEach((v) => v.classList.remove("is-active"));
    $("#view-" + STATE.view).classList.add("is-active");
    $$(".tab").forEach((t) => t.classList.toggle("is-active", t.dataset.view === STATE.view));
    if (STATE.view === "calendar") renderCalendar();
    else if (STATE.view === "list") renderList();
    else if (STATE.view === "tickets") renderTickets();
    else if (STATE.view === "goods") renderGoods();
  }

  // ---------- カレンダー ----------
  function renderCalendar() {
    const y = STATE.calY, m = STATE.calM; // m: 1-12
    $("#calTitle").textContent = `${y}年${m}月`;
    const byDate = eventsByDate();
    const first = new Date(y, m - 1, 1);
    const startDow = first.getDay();
    const dim = new Date(y, m, 0).getDate();
    const today = todayStr();
    const bdayByDate = birthdaysInMonth(y, m);

    const grid = $("#calGrid");
    grid.innerHTML = "";
    ["日", "月", "火", "水", "木", "金", "土"].forEach((w, i) => {
      const h = document.createElement("div");
      h.className = "cal-wday" + (i === 0 ? " sun" : i === 6 ? " sat" : "");
      h.textContent = w;
      grid.appendChild(h);
    });
    for (let i = 0; i < startDow; i++) {
      const e = document.createElement("div");
      e.className = "cal-cell empty";
      grid.appendChild(e);
    }
    for (let d = 1; d <= dim; d++) {
      const ds = `${y}-${pad(m)}-${pad(d)}`;
      const dow = new Date(y, m - 1, d).getDay();
      const evs = byDate[ds] || [];
      const ci = conflictInfo(evs);
      const cell = document.createElement("div");
      cell.className = "cal-cell" +
        (evs.length ? " has-events" : "") +
        (ds === today ? " today" : "") +
        (ci.conflict ? " conflict" : "");
      const dcls = dow === 0 ? " sun" : dow === 6 ? " sat" : "";
      let html = `<div class="cell-date${dcls}">${d}` +
        (ci.conflict ? `<span class="cell-conflict">⚡<span class="cf-t">かぶり</span></span>` : "") + `</div>`;
      const show = evs.slice().sort((a, b) => (isOshiEvent(b) ? 1 : 0) - (isOshiEvent(a) ? 1 : 0));
      show.slice(0, 3).forEach((ev) => {
        const oshi = isOshiEvent(ev);
        const pc = eventPrimaryColor(ev);
        html += `<div class="pill${oshi ? " oshi" : ""}" style="background:${pc};color:${readableText(pc)}">` +
          (oshi ? `<span class="heart">♥</span>` : "") + esc(shortTitle(ev)) + `</div>`;
      });
      if (evs.length > 3) html += `<div class="pill pill--more">＋${evs.length - 3}件</div>`;
      const obd = (bdayByDate[d] || []).filter((mb) => STATE.oshiMembers.has(mb.name));
      if (obd.length) html += `<div class="cell-bday">🎂${esc(obd[0].name.split(" ").pop())}</div>`;
      cell.innerHTML = html;
      if (evs.length) cell.addEventListener("click", () => openDay(ds, evs, ci));
      grid.appendChild(cell);
    }
  }
  function shortTitle(ev) {
    let t = ev.title || "";
    t = t.replace(/[『』「」]/g, "");
    return t.length > 9 ? t.slice(0, 9) + "…" : t;
  }

  // ---------- リスト ----------
  function renderList() {
    const body = $("#listBody");
    const today = todayStr();
    const evs = visibleEvents().filter((e) => e.date >= today).sort((a, b) =>
      a.date.localeCompare(b.date) || (a.start || "").localeCompare(b.start || ""));
    if (!evs.length) { body.innerHTML = emptyState("予定が見つかりません", "フィルタを見直してね", "mascot-search.png", "🔍"); return; }
    const byDate = eventsByDate();
    let html = "", curMonth = "";
    evs.forEach((ev) => {
      const mk = ev.date.slice(0, 7);
      if (mk !== curMonth) {
        curMonth = mk;
        const [yy, mm] = mk.split("-");
        html += `<div class="list-month"><span>${yy}年${Number(mm)}月</span></div>`;
      }
      const ci = conflictInfo(byDate[ev.date] || []);
      html += cardHTML(ev, ci.conflict);
    });
    body.innerHTML = html;
    bindCards(body);
  }

  function cardHTML(ev, conflict, extra) {
    const d = parseYMD(ev.date);
    const dow = d.getDay();
    const wcls = dow === 0 ? " sun" : dow === 6 ? " sat" : "";
    const oshi = isOshiEvent(ev);
    const groups = (ev.groups && ev.groups.length ? ev.groups : [NONE])
      .map((id) => `<span class="gtag" style="background:${gColor(id)};color:${readableText(gColor(id))}">${esc(gShort(id))}</span>`).join("");
    let badges = "";
    if (ev.ticket_url || ev.ticket_status || ev.ticket_info) badges += `<span class="badge badge--ticket">🎫</span>`;
    if (conflict) badges += `<span class="badge badge--conflict">⚡かぶり</span>`;
    const time = ev.start ? `🕐 ${ev.start}` : "";
    const place = ev.venue ? `📍 ${esc((ev.prefecture ? ev.prefecture + " " : "") + ev.venue)}` : "";
    return `<div class="ev-card${oshi ? " oshi" : ""}" data-id="${ev.id}">
      <div class="ev-date">
        <div class="ev-date__m">${Number(ev.date.slice(5, 7))}月</div>
        <div class="ev-date__d">${Number(ev.date.slice(8, 10))}</div>
        <div class="ev-date__w${wcls}">${WDAY[dow]}</div>
      </div>
      <div class="ev-main">
        <span class="ev-cat ${ev.category || "OTHER"}">${esc(ev.category || "予定")}</span>
        <div class="ev-title">${oshi ? "♥ " : ""}${esc(ev.title)}</div>
        <div class="ev-meta">${[time, place].filter(Boolean).join(" ")}</div>
        <div class="ev-groups">${groups}</div>
        ${extra || ""}
      </div>
      <div class="ev-badges">${badges}</div>
    </div>`;
  }
  function bindCards(root) {
    $$(".ev-card", root).forEach((c) =>
      c.addEventListener("click", () => {
        const ev = DATA.events.find((e) => e.id === c.dataset.id);
        if (ev) openDrawer(ev);
      }));
  }

  // ---------- チケット・締切 ----------
  function renderTickets() {
    const body = $("#ticketsBody");
    const today = todayStr();
    let evs = visibleEvents().filter((e) =>
      e.date >= today && (e.ticket_url || e.ticket_status || e.deadline || e.ticket_info));
    // 緊急度: 締切あり → 日付が近い順
    evs.sort((a, b) => {
      const ad = a.deadline ? 0 : 1, bd = b.deadline ? 0 : 1;
      return ad - bd || a.date.localeCompare(b.date);
    });
    if (!evs.length) {
      body.innerHTML = emptyState("チケット情報のある予定がありません",
        "公式サイトで最新の発売情報をチェックしてね", "mascot-ticket.png", "🎫");
      return;
    }
    body.innerHTML = evs.map((ev) => {
      const dd = daysBetween(today, ev.date);
      let line = "";
      if (dd >= 0 && dd <= 14) line += `<span class="badge badge--soon">公演まであと${dd}日</span> `;
      if (ev.ticket_status) line += `<span class="badge badge--ticket">🎫 ${esc(ev.ticket_status)}</span> `;
      const extra = (line || ev.ticket_info || ev.deadline)
        ? `<div class="ev-meta" style="margin-top:7px;gap:6px">${line}` +
          (ev.ticket_info ? `<span>🎫 ${esc(ev.ticket_info)}</span>` : "") +
          (ev.deadline ? `<span>⏰ ${esc(ev.deadline)}</span>` : "") + `</div>`
        : "";
      return cardHTML(ev, false, extra);
    }).join("");
    bindCards(body);
  }

  // ---------- 物販 ----------
  function renderGoods() {
    const body = $("#goodsBody");
    const today = todayStr();
    const kw = /(特典会|物販|リリースイベント|お話し会|チェキ|サイン会|ミニライブ)/;
    let evs = visibleEvents().filter((e) => e.date >= today &&
      (kw.test(e.title) || kw.test(e.info || "") || e.category === "EVENT"))
      .sort((a, b) => a.date.localeCompare(b.date)).slice(0, 30);
    body.innerHTML = evs.length
      ? evs.map((ev) => cardHTML(ev, false)).join("")
      : emptyState("近い物販・特典会の予定が見つかりません", "公式ショップは上のボタンから", "mascot-goods.png", "🛍");
    bindCards(body);
  }

  // ---------- 誕生日 ----------
  function birthdaysInMonth(y, m) {
    const map = {};
    DATA.groups.forEach((g) => (g.members || []).forEach((mem) => {
      if (!mem.birthday) return;
      const [bm, bd] = mem.birthday.split("-").map(Number);
      if (bm === m) (map[bd] = map[bd] || []).push({ name: mem.name, gid: g.id, color: mem.colorHex });
    }));
    return map;
  }
  function renderBday() {
    const row = $("#bdayRow");
    const today = new Date();
    const list = [];
    DATA.groups.forEach((g) => (g.members || []).forEach((mem) => {
      if (!mem.birthday) return;
      const [bm, bd] = mem.birthday.split("-").map(Number);
      let next = new Date(today.getFullYear(), bm - 1, bd);
      if (next < new Date(today.getFullYear(), today.getMonth(), today.getDate())) {
        next = new Date(today.getFullYear() + 1, bm - 1, bd);
      }
      const diff = Math.round((next - new Date(today.getFullYear(), today.getMonth(), today.getDate())) / 86400000);
      if (diff <= 75) list.push({ mem, g, diff, bm, bd });
    }));
    list.sort((a, b) =>
      (STATE.oshiMembers.has(b.mem.name) ? 1 : 0) - (STATE.oshiMembers.has(a.mem.name) ? 1 : 0) ||
      a.diff - b.diff);
    $("#bdaySection").style.display = list.length ? "" : "none";
    row.innerHTML = list.slice(0, 14).map(({ mem, g, diff, bm, bd }) => {
      const oshi = STATE.oshiMembers.has(mem.name);
      return `<div class="bday-card">
        <div class="bday-card__d" style="color:${mem.colorHex}">${bm}/${bd}</div>
        <div class="bday-card__n">${oshi ? "♥ " : ""}${esc(mem.name)}</div>
        <div class="bday-card__g"><span class="chip__dot" style="background:${gColor(g.id)};width:8px;height:8px"></span>${esc(g.short)} ・ あと${diff}日</div>
      </div>`;
    }).join("");
  }

  // =========================================================
  //  詳細ドロワー
  // =========================================================
  function openDay(ds, evs, ci) {
    if (evs.length === 1) return openDrawer(evs[0], ci.conflict);
    // 複数 → その日の一覧を簡易ドロワーで
    const d = parseYMD(ds);
    let html = `<div class="dr-date">${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 (${WDAY[d.getDay()]})</div>
      <div class="dr-title">この日の予定 ${evs.length}件</div>`;
    if (ci.conflict) html += `<div class="dr-conflict">⚡ ライブ・イベントがかぶっています。行きたい現場を選んで、移動を計画しましょう。</div>`;
    html += `<div style="margin-top:12px">` + evs.map((ev) =>
      cardHTML(ev, false)).join("") + `</div>`;
    showDrawer(html);
    bindCards($("#drawerBody"));
  }

  function openDrawer(ev, conflict) {
    const d = parseYMD(ev.date);
    const groups = (ev.groups && ev.groups.length ? ev.groups : [NONE])
      .map((id) => `<span class="gtag" style="background:${gColor(id)};color:${readableText(gColor(id))}">${esc(gName(id))}</span>`).join("");
    const rows = [];
    rows.push(row("🕐", "時間", [ev.open ? "開場 " + ev.open : "", ev.start ? "開演 " + ev.start : ""].filter(Boolean).join(" / ") || "未定"));
    if (ev.venue) rows.push(row("📍", "会場", (ev.prefecture ? ev.prefecture + " " : "") + ev.venue));
    if (ev.ticket_status) rows.push(row("🎫", "チケット状況", ev.ticket_status));
    if (ev.ticket_info && ev.ticket_info !== ev.ticket_status) rows.push(row("🎟", "チケット情報", ev.ticket_info));
    if (ev.price) rows.push(row("💴", "料金", ev.price));
    if (ev.deadline) rows.push(row("⏰", "締切・申込", ev.deadline));

    const q = encodeURIComponent(((ev.prefecture || "") + " " + (ev.venue || "")).trim());
    const mapSearch = ev.venue ? `https://www.google.com/maps/search/?api=1&query=${q}` : "";
    const mapDir = ev.venue ? `https://www.google.com/maps/dir/?api=1&destination=${q}` : "";

    let actions = "";
    if (ev.ticket_url) actions += `<a class="btn btn--ticket2 btn--full" href="${esc(ev.ticket_url)}" target="_blank" rel="noopener">🎫 チケットを見る</a>`;
    if (mapDir) actions += `<a class="btn btn--map" href="${mapDir}" target="_blank" rel="noopener">🚗 行き方(ドライブ)</a>`;
    if (mapSearch) actions += `<a class="btn btn--map" href="${mapSearch}" target="_blank" rel="noopener">🗺 会場の地図</a>`;
    actions += `<button class="btn btn--ics" id="icsBtn">➕ カレンダーに追加</button>`;
    if (ev.detail_url) actions += `<a class="btn btn--src" href="${esc(ev.detail_url)}" target="_blank" rel="noopener">🔗 詳細ページ</a>`;

    let html = `<div class="dr-date">${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 (${WDAY[d.getDay()]})　<span class="ev-cat ${ev.category || "OTHER"}">${esc(ev.category || "予定")}</span></div>
      <div class="dr-title">${esc(ev.title)}</div>
      <div class="dr-groups">${groups}</div>
      ${rows.join("")}`;
    if (conflict) html += `<div class="dr-conflict">⚡ この日は他にもライブ・イベントがあります。「リスト」で同じ日を見比べて、行く現場と移動を決めましょう。</div>`;
    if (ev.info) html += `<div class="dr-row"><span class="dr-row__ico">📝</span><div><div class="dr-row__k">インフォ</div><div class="dr-info">${esc(ev.info)}</div></div></div>`;
    html += `<div class="dr-actions">${actions}</div>`;
    html += `<textarea class="dr-note" id="drNote" placeholder="この現場のメモ（同行者・集合・持ち物・宿など）"></textarea>`;
    showDrawer(html);

    const note = $("#drNote");
    if (note) {
      note.value = getNote(ev.id);
      note.addEventListener("input", () => setNote(ev.id, note.value));
    }
    const ics = $("#icsBtn");
    if (ics) ics.addEventListener("click", () => downloadICS(ev));
  }
  function row(ico, k, v) {
    return `<div class="dr-row"><span class="dr-row__ico">${ico}</span><div><div class="dr-row__k">${k}</div><div class="dr-row__v">${esc(v)}</div></div></div>`;
  }
  function showDrawer(html) {
    $("#drawerBody").innerHTML = html;
    $("#drawer").hidden = false;
    $("#backdrop").hidden = false;
  }
  function closeDrawer() {
    $("#drawer").hidden = true;
    $("#backdrop").hidden = true;
  }

  // ---------- .ics 生成 ----------
  function downloadICS(ev) {
    const dt = ev.date.replace(/-/g, "");
    let dtstart, dtend;
    if (ev.start && /^\d{1,2}:\d{2}$/.test(ev.start)) {
      const [h, mi] = ev.start.split(":").map(Number);
      const eh = (h + 3) % 24;
      dtstart = `DTSTART:${dt}T${pad(h)}${pad(mi)}00`;
      // 終演が日付をまたぐ(例 22:00開演)場合は翌日にする
      let edt = dt;
      if (h + 3 >= 24) {
        const nx = new Date(parseYMD(ev.date).getTime() + 86400000);
        edt = `${nx.getFullYear()}${pad(nx.getMonth() + 1)}${pad(nx.getDate())}`;
      }
      dtend = `DTEND:${edt}T${pad(eh)}${pad(mi)}00`;
    } else {
      const nx = new Date(parseYMD(ev.date).getTime() + 86400000);
      const nxs = `${nx.getFullYear()}${pad(nx.getMonth() + 1)}${pad(nx.getDate())}`;
      dtstart = `DTSTART;VALUE=DATE:${dt}`;
      dtend = `DTEND;VALUE=DATE:${nxs}`;
    }
    const loc = ((ev.prefecture || "") + " " + (ev.venue || "")).trim();
    const desc = [ev.groups.map(gName).join("・"), ev.ticket_url ? "チケット: " + ev.ticket_url : "", ev.detail_url || ""].filter(Boolean).join("\\n");
    const ics = [
      "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//kawalab-calendar//JP",
      "BEGIN:VEVENT", `UID:kawalab-${ev.id}@local`,
      dtstart, dtend,
      `SUMMARY:${icsEsc("♡ " + ev.title)}`,
      loc ? `LOCATION:${icsEsc(loc)}` : "",
      desc ? `DESCRIPTION:${desc}` : "",
      ev.detail_url ? `URL:${ev.detail_url}` : "",
      "END:VEVENT", "END:VCALENDAR",
    ].filter(Boolean).join("\r\n");
    const blob = new Blob([ics], { type: "text/calendar;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `kawalab_${ev.date}_${ev.id}.ics`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1500);
  }
  const icsEsc = (s) => (s || "").replace(/([,;\\])/g, "\\$1").replace(/\n/g, "\\n");

  function emptyState(t, sub, img, emoji) {
    const m = img || "mascot.png";
    const e = emoji || "🌸";
    return `<div class="empty-state">` +
      `<img class="empty-state__img" src="assets/img/${m}" alt="" ` +
      `onerror="this.outerHTML='<span class=\\'empty-state__emoji\\'>${e}</span>'">` +
      `${esc(t)}<br><span style="font-size:12px">${esc(sub)}</span></div>`;
  }

  // =========================================================
  //  推し設定モーダル
  // =========================================================
  function openOshi() {
    const body = $("#oshiBody");
    body.innerHTML = DATA.groups.filter((g) => g.members && g.members.length).map((g) => {
      const gOn = STATE.oshiGroups.has(g.id);
      const mems = g.members.map((mem) => {
        const on = STATE.oshiMembers.has(mem.name);
        return `<button class="oshi-mem" data-mem="${esc(mem.name)}" data-gid="${g.id}" aria-pressed="${on}">
          <span class="chip__dot" style="background:${mem.colorHex}"></span>${esc(mem.name)}</button>`;
      }).join("");
      return `<div class="oshi-group">
        <div class="oshi-group__h">
          <button class="chip oshi-grp-toggle" data-gid="${g.id}" aria-pressed="${gOn}">
            <span class="chip__dot" style="background:${g.color}"></span>${esc(g.name)}</button>
        </div>
        <div class="oshi-members">${mems}</div>
      </div>`;
    }).join("");
    $$(".oshi-grp-toggle", body).forEach((b) => b.addEventListener("click", () => {
      const id = b.dataset.gid;
      const on = b.getAttribute("aria-pressed") === "true";
      b.setAttribute("aria-pressed", on ? "false" : "true");
      if (on) STATE.oshiGroups.delete(id); else STATE.oshiGroups.add(id);
    }));
    $$(".oshi-mem", body).forEach((b) => b.addEventListener("click", () => {
      const name = b.dataset.mem;
      const on = b.getAttribute("aria-pressed") === "true";
      b.setAttribute("aria-pressed", on ? "false" : "true");
      if (on) STATE.oshiMembers.delete(name);
      else { STATE.oshiMembers.add(name); STATE.oshiGroups.add(b.dataset.gid); }
    }));
    $("#oshiBackdrop").hidden = false;
  }
  function closeOshi() { $("#oshiBackdrop").hidden = true; }

  // =========================================================
  //  初期化
  // =========================================================
  function bindUI() {
    $$(".tab").forEach((t) => t.addEventListener("click", () => {
      STATE.view = t.dataset.view; renderView();
    }));
    $("#prevMonth").addEventListener("click", () => { shiftMonth(-1); });
    $("#nextMonth").addEventListener("click", () => { shiftMonth(1); });
    $("#todayBtn").addEventListener("click", () => {
      const d = new Date(); STATE.calY = d.getFullYear(); STATE.calM = d.getMonth() + 1;
      STATE.view = "calendar"; renderView();
    });
    $("#oshiOnly").addEventListener("change", (e) => { STATE.oshiOnly = e.target.checked; render(); });
    $("#refreshBtn").addEventListener("click", async () => {
      $("#refreshBtn").style.transform = "rotate(360deg)";
      await loadData(); render();
      setTimeout(() => ($("#refreshBtn").style.transform = ""), 400);
    });
    $("#drawerClose").addEventListener("click", closeDrawer);
    $("#backdrop").addEventListener("click", closeDrawer);
    $("#oshiBtn").addEventListener("click", openOshi);
    $("#oshiClose").addEventListener("click", closeOshi);
    $("#oshiBackdrop").addEventListener("click", (e) => { if (e.target.id === "oshiBackdrop") closeOshi(); });
    $("#oshiSave").addEventListener("click", () => { saveOshi(); closeOshi(); render(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeDrawer(); closeOshi(); } });
    window.addEventListener("resize", syncHeaderH);
    window.addEventListener("load", syncHeaderH);
  }
  function shiftMonth(delta) {
    let m = STATE.calM + delta, y = STATE.calY;
    if (m < 1) { m = 12; y--; } else if (m > 12) { m = 1; y++; }
    STATE.calY = y; STATE.calM = m; renderCalendar();
  }
  // ヘッダー実高を測ってCSS変数に渡す(ノッチ機のsafe-areaでタブが重ならないように)
  function syncHeaderH() {
    const h = document.querySelector(".app-header");
    if (h) document.documentElement.style.setProperty("--header-h", h.offsetHeight + "px");
  }

  async function init() {
    bindUI();
    loadOshi();
    try {
      await loadData();
    } catch (e) {
      document.querySelector(".container").innerHTML =
        emptyState("データを読み込めませんでした", "「カワラボを開く.command」から起動してください");
      return;
    }
    STATE.active = new Set(groupsPresent());
    // 最初に表示する月: 今日の予定があればその月、無ければ最初の予定の月
    const today = todayStr();
    const future = DATA.events.filter((e) => e.date >= today).sort((a, b) => a.date.localeCompare(b.date));
    const base = future.length ? future[0].date : (DATA.events[0] && DATA.events[0].date) || today;
    STATE.calY = Number(base.slice(0, 4));
    STATE.calM = Number(base.slice(5, 7));
    render();
    syncHeaderH();
    setTimeout(syncHeaderH, 300);
    // 1時間ごとの自動更新を画面側でも取りに行く(15分間隔で再読込)
    setInterval(async () => { try { await loadData(); render(); } catch (e) {} }, 15 * 60 * 1000);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
