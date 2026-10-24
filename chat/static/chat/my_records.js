// 스탯창 분석 결과로 만드는 "결과 카드"(이미지로 저장·공유)와 "내 기록"(이 브라우저에만 쌓이는 판 기록).
// 서버에는 아무것도 저장하지 않는다. 기록은 localStorage에만 있어 브라우저 데이터를 지우거나 기기를 바꾸면 사라진다.
// 판마다 승패(result)를 남길 수 있다(티어는 받지 않는다: 친구와 파티로 한 판은 내 티어와 다른 판이 잡힌다).
// 승패를 남긴 판이 모이면 "이긴 판 vs 진 판"에서 같은 판 안 상대와 비교한 값으로 차이를 보여 준다.
(function () {
  "use strict";

  const KEY = "owcoach.records.v1";
  const MAX_RECORDS = 100;
  const ROLE_LABELS = { tank: "탱커", damage: "딜러", support: "힐러" };
  const RESULT_LABELS = { win: "승리", loss: "패배", draw: "무승부" };
  // 이긴 판 vs 진 판을 여는 기준: 승패가 갈린 판 10판, 그중 이긴 판·진 판 각각 3판 이상.
  const COMPARE_MIN = 10;
  const COMPARE_MIN_EACH = 3;
  let iconBase = "";
  let escapeHtml = (s) => String(s);

  // ---------- 저장 ----------
  function load() {
    try {
      const list = JSON.parse(localStorage.getItem(KEY) || "[]");
      return Array.isArray(list) ? list : [];
    } catch (e) {
      return [];
    }
  }
  function store(list) {
    try { localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX_RECORDS))); } catch (e) { /* 저장이 막힌 브라우저 */ }
  }

  function setResult(id, result) {
    const list = load();
    const record = list.find((r) => r.id === id);
    if (!record) return null;
    record.result = result || undefined;
    store(list);
    return record;
  }

  const hasNumbers = (match) => !!match && (match.ally || []).some((p) => p.kills != null || p.damage != null);

  // 숫자를 읽은 판만 남긴다(같은 분석을 두 번 넣지 않는다).
  function save(match, id) {
    if (!hasNumbers(match)) return null;
    const list = load();
    if (id && list.some((r) => r.id === id)) return list.find((r) => r.id === id);
    const record = { id: id || String(Date.now()), ts: Date.now(), match };
    list.unshift(record);
    store(list);
    return record;
  }

  const selfOf = (match) => (match.ally || []).find((p) => p.is_self) || null;
  const n = (v) => (v == null ? "-" : Number(v).toLocaleString("ko-KR"));
  const avg = (arr) => (arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null);
  const fmt1 = (v) => (v == null ? "-" : (Math.round(v * 10) / 10).toString());
  const iconUrl = (hero) => (hero && !hero.startsWith("미확인") ? iconBase + encodeURIComponent(hero) + ".png" : "");
  const dateText = (ts) => {
    const d = new Date(ts);
    return (d.getMonth() + 1) + "/" + d.getDate() + " " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  };

  // ---------- 결과 카드(캔버스로 그려 이미지로 저장) ----------
  function loadImage(src) {
    return new Promise((resolve) => {
      if (!src) { resolve(null); return; }
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => resolve(null);
      img.src = src;
    });
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  async function drawCard(record) {
    const match = record.match;
    const me = selfOf(match);
    // 순위가 있으면 순위 순서, 없으면 스탯창 순서(우리팀만)
    const byHero = {};
    (match.ally || []).forEach((p) => { if (p.hero) byHero[p.hero] = p; });
    const rows = match.ranking && match.ranking.length
      ? match.ranking.map((r) => ({ rank: r.rank, hero: r.hero, label: r.type_label, final: r.final, isMe: r.is_me, p: byHero[r.hero] || null }))
      : (match.ally || []).map((p, i) => ({ rank: null, hero: p.hero || "미확인", label: ROLE_LABELS[p.role] || "", final: null, isMe: p.is_self, p }));

    const W = 1080;
    const ROW_H = 138;
    const top = me ? 400 : 250;
    const H = top + rows.length * ROW_H + 60;
    const canvas = document.createElement("canvas");
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext("2d");
    const font = (w, px) => w + " " + px + "px 'Noto Sans KR', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif";

    const bg = ctx.createLinearGradient(0, 0, 0, H);
    bg.addColorStop(0, "#1d2740");
    bg.addColorStop(1, "#111827");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = "#f99e1a";
    ctx.fillRect(0, 0, W, 14);

    ctx.fillStyle = "#f99e1a";
    ctx.font = font(800, 34);
    ctx.fillText("오버워치 코치 AI", 72, 96);
    ctx.fillStyle = "#9aa4b8";
    ctx.font = font(600, 30);
    ctx.textAlign = "right";
    ctx.fillText(dateText(record.ts), W - 72, 96);
    ctx.textAlign = "left";
    ctx.fillStyle = "#ffffff";
    ctx.font = font(800, 60);
    ctx.fillText("이번 판 우리팀 순위", 72, 186);

    const icons = await Promise.all(rows.map((r) => loadImage(iconUrl(r.p ? r.p.hero : r.hero))));
    const meIcon = me ? await loadImage(iconUrl(me.hero)) : null;

    if (me) {
      roundRect(ctx, 72, 226, W - 144, 140, 28);
      ctx.fillStyle = "rgba(249, 158, 26, 0.16)";
      ctx.fill();
      if (meIcon) {
        ctx.save();
        roundRect(ctx, 100, 248, 96, 96, 22);
        ctx.clip();
        ctx.drawImage(meIcon, 100, 248, 96, 96);
        ctx.restore();
      }
      ctx.fillStyle = "#ffffff";
      ctx.font = font(800, 44);
      ctx.fillText((me.hero || "내 영웅") + "(나)", 222, 292);
      ctx.fillStyle = "#ffcf7a";
      ctx.font = font(700, 32);
      const rankText = match.self_rank ? match.self_rank + "위   " : "";
      ctx.fillText(rankText + n(me.kills) + " / " + n(me.deaths) + " / " + n(me.assists), 222, 340);
    }

    rows.forEach((r, i) => {
      const y = top + i * ROW_H;
      roundRect(ctx, 72, y, W - 144, ROW_H - 18, 24);
      ctx.fillStyle = r.isMe ? "rgba(249, 158, 26, 0.22)" : "rgba(255, 255, 255, 0.06)";
      ctx.fill();
      if (r.isMe) { ctx.strokeStyle = "#f99e1a"; ctx.lineWidth = 3; ctx.stroke(); }
      ctx.fillStyle = r.rank === 1 ? "#ffcf3e" : "#c7cfdd";
      ctx.font = font(800, 46);
      ctx.textAlign = "center";
      ctx.fillText(r.rank ? String(r.rank) : "·", 130, y + 76);
      ctx.textAlign = "left";
      if (icons[i]) {
        ctx.save();
        roundRect(ctx, 176, y + 16, 88, 88, 20);
        ctx.clip();
        ctx.drawImage(icons[i], 176, y + 16, 88, 88);
        ctx.restore();
      } else {
        roundRect(ctx, 176, y + 16, 88, 88, 20);
        ctx.fillStyle = "#2c3650";
        ctx.fill();
      }
      ctx.fillStyle = "#ffffff";
      ctx.font = font(800, 38);
      ctx.fillText(r.hero + (r.isMe ? "(나)" : ""), 290, y + 58);
      ctx.fillStyle = "#9aa4b8";
      ctx.font = font(600, 28);
      const p = r.p;
      const main = p ? (p.role === "support" ? "치유 " + n(p.healing) : p.role === "tank" && p.mitigation ? "경감 " + n(p.mitigation) : "피해 " + n(p.damage)) : "";
      ctx.fillText([r.label, p ? n(p.kills) + "/" + n(p.deaths) + "/" + n(p.assists) : "", main].filter(Boolean).join("  ·  "), 290, y + 100);
    });
    return canvas;
  }

  async function openCard(record) {
    const overlay = modal("🏅 결과 카드", "이미지로 저장해서 친구에게 보내 보세요.", '<div class="rec-empty">카드를 만드는 중...</div>');
    const canvas = await drawCard(record);
    const url = canvas.toDataURL("image/png");
    const body = overlay.querySelector(".rec-body");
    const canShare = !!(navigator.canShare && window.isSecureContext);
    body.innerHTML = '<img class="rec-card-img" alt="이번 판 우리팀 순위 카드" src="' + url + '">' +
      '<div class="rec-card-actions"><a class="rec-btn primary" download="overwatch-result-' + record.id + '.png" href="' + url + '">이미지 저장</a>' +
      (canShare ? '<button type="button" class="rec-btn" id="recShareBtn">공유하기</button>' : "") + "</div>";
    const share = body.querySelector("#recShareBtn");
    if (share) {
      share.addEventListener("click", () => {
        canvas.toBlob((blob) => {
          const file = new File([blob], "overwatch-result.png", { type: "image/png" });
          if (navigator.canShare({ files: [file] })) navigator.share({ files: [file], title: "이번 판 우리팀 순위" }).catch(() => {});
        });
      });
    }
  }

  // 분석 결과 말풍선에 붙이는 승패 칸과 버튼
  function resultBoxHtml(record) {
    if (!record.result) {
      return '<div class="rec-ask"><b>이번 판 결과는?</b><span>눌러 두면 이긴 판과 진 판을 비교해 드려요</span>' +
        '<div class="rec-ask-btns"><button type="button" class="win" data-rec-result="win">이겼어요</button>' +
        '<button type="button" class="loss" data-rec-result="loss">졌어요</button>' +
        '<button type="button" class="draw" data-rec-result="draw">무승부</button></div></div>';
    }
    const decided = load().filter((r) => r.result === "win" || r.result === "loss").length;
    const note = decided >= COMPARE_MIN
      ? "승패를 기록한 판: " + decided + "판\n'내 기록'에서 이긴 판과 진 판을 비교해 보세요"
      : "승패를 기록한 판: " + decided + "판\n" + COMPARE_MIN + "판이 모이면 이긴 판과 진 판을 비교해 드려요";
    return '<div class="rec-saved ' + record.result + '"><span>' + RESULT_LABELS[record.result] + "로 기록했어요</span>" +
      '<button type="button" data-rec-result="">바꾸기</button></div><div class="rec-saved-note">' + escapeHtml(note) + "</div>";
  }

  function actionBar(match, id) {
    let record = save(match, id);
    if (!record) return null;
    const wrap = document.createElement("div");
    wrap.className = "rec-wrap";
    const box = document.createElement("div");
    box.innerHTML = resultBoxHtml(record);
    const bar = document.createElement("div");
    bar.className = "rec-actions";
    bar.innerHTML = '<button type="button" class="rec-chip" data-rec="card">🏅 결과 카드 만들기</button>' +
      '<button type="button" class="rec-chip" data-rec="list">📈 내 기록 보기</button>';
    wrap.append(box, bar);
    wrap.addEventListener("click", (e) => {
      const r = e.target.closest("[data-rec-result]");
      if (r) {
        record = setResult(record.id, r.dataset.recResult) || record;
        box.innerHTML = resultBoxHtml(record);
        return;
      }
      const b = e.target.closest("[data-rec]");
      if (!b) return;
      if (b.dataset.rec === "card") openCard(record);
      else openRecords();
    });
    return wrap;
  }

  // ---------- 내 기록 ----------
  function modal(title, sub, bodyHtml) {
    document.querySelector(".rec-overlay")?.remove();
    const overlay = document.createElement("div");
    overlay.className = "save-modal-overlay rec-overlay";
    overlay.innerHTML = '<div class="mx-modal rec-modal"><div class="mx-head"><div class="mx-head-top"><h3>' + title +
      '</h3><button type="button" class="save-modal-close" data-rec-close>×</button></div>' +
      (sub ? '<div class="mx-head-sub">' + sub + "</div>" : "") + '</div><div class="mx-body rec-body">' + bodyHtml + "</div></div>";
    overlay.addEventListener("click", (e) => { if (e.target === overlay || e.target.closest("[data-rec-close]")) overlay.remove(); });
    document.body.appendChild(overlay);
    return overlay;
  }

  function heroImg(hero, cls) {
    return hero ? '<img class="' + cls + '" src="' + iconUrl(hero) + '" alt="" onerror="this.style.visibility=\'hidden\'">' : '<span class="' + cls + ' none">?</span>';
  }

  function recordsHtml(list) {
    if (!list.length) {
      return '<div class="rec-empty">아직 기록이 없어요.<br>판이 끝나고 TAB 스탯창을 캡처해서 올리면 판마다 여기에 쌓여요.</div>';
    }
    const mine = list.filter((r) => selfOf(r.match));
    const ranked = mine.filter((r) => r.match.self_rank);
    const sum = (key) => avg(mine.map((r) => selfOf(r.match)[key]).filter((v) => v != null));
    const heroCount = {};
    mine.forEach((r) => { const h = selfOf(r.match).hero; if (h) heroCount[h] = (heroCount[h] || 0) + 1; });
    const topHero = Object.keys(heroCount).sort((a, b) => heroCount[b] - heroCount[a])[0];

    const wins = list.filter((r) => r.result === "win").length;
    const losses = list.filter((r) => r.result === "loss").length;
    const draws = list.filter((r) => r.result === "draw").length;
    let html = '<div class="rec-tiles">' +
      tile("판 수", list.length + "판") +
      tile("승률", wins + losses ? Math.round((wins / (wins + losses)) * 100) + '%<small class="rec-wl">' + wins + "승 " + losses + "패" + (draws ? " " + draws + "무" : "") + "</small>" : "-") +
      tile("평균 순위", ranked.length ? fmt1(avg(ranked.map((r) => r.match.self_rank))) + "위" : "-") +
      tile("평균 K / D / A", mine.length ? fmt1(sum("kills")) + " / " + fmt1(sum("deaths")) + " / " + fmt1(sum("assists")) : "-") +
      tile("많이 한 영웅", topHero ? heroImg(topHero, "rec-tile-ico") + escapeHtml(topHero) : "-") + "</div>" +
      '<button type="button" class="rec-compare-btn" data-rec-view="compare">이긴 판 vs 진 판 비교<svg viewBox="0 0 18 18" width="16" height="16" aria-hidden="true"><path d="M7 3 L13 9 L7 15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg></button>';

    // 최근 5판과 그 전을 비교한다(내 줄을 인식한 판만).
    if (ranked.length >= 6) {
      const recent = ranked.slice(0, 5);
      const before = ranked.slice(5);
      const rA = avg(recent.map((r) => r.match.self_rank));
      const rB = avg(before.map((r) => r.match.self_rank));
      const dA = avg(recent.map((r) => selfOf(r.match).deaths).filter((v) => v != null));
      const dB = avg(before.map((r) => selfOf(r.match).deaths).filter((v) => v != null));
      const lines = [];
      if (rA != null && rB != null) lines.push("평균 순위 " + fmt1(rB) + "위 → <b>" + fmt1(rA) + "위</b>" + (rA < rB ? " 올랐어요" : rA > rB ? " 내려갔어요" : ""));
      if (dA != null && dB != null) lines.push("평균 데스 " + fmt1(dB) + " → <b>" + fmt1(dA) + "</b>" + (dA < dB ? " 줄었어요" : dA > dB ? " 늘었어요" : ""));
      if (lines.length) html += '<div class="rec-trend"><div class="rec-sec">최근 5판 vs 그 전</div>' + lines.map((l) => "<div>" + l + "</div>").join("") + "</div>";
    }

    const heroes = Object.keys(heroCount).sort((a, b) => heroCount[b] - heroCount[a]);
    if (heroes.length) {
      html += '<div class="rec-sec">영웅별</div><div class="rec-heroes">' + heroes.map((h) => {
        const games = mine.filter((r) => selfOf(r.match).hero === h);
        const rk = games.filter((r) => r.match.self_rank);
        const a = (key) => fmt1(avg(games.map((r) => selfOf(r.match)[key]).filter((v) => v != null)));
        return '<div class="rec-hero">' + heroImg(h, "rec-hero-ico") + '<div class="rec-hero-main"><b>' + escapeHtml(h) + "</b><span>" + games.length + "판" +
          (rk.length ? " · 평균 " + fmt1(avg(rk.map((r) => r.match.self_rank))) + "위" : "") + "</span></div>" +
          '<div class="rec-hero-kda">' + a("kills") + " / " + a("deaths") + " / " + a("assists") + "</div></div>";
      }).join("") + "</div>";
    }

    html += '<div class="rec-sec">최근 판</div><div class="rec-list">' + list.slice(0, 30).map((r) => {
      const me = selfOf(r.match);
      const main = me ? (me.role === "support" ? "치유 " + n(me.healing) : "피해 " + n(me.damage)) : "";
      return '<div class="rec-row">' + heroImg(me && me.hero, "rec-row-ico") + '<div class="rec-row-main"><b>' +
        (me ? escapeHtml(me.hero || "내 영웅 미확인") : "내 줄을 찾지 못한 판") + (r.match.self_rank ? ' <em class="rec-rank">' + r.match.self_rank + "위</em>" : "") +
        "</b><span>" + dateText(r.ts) + (me ? " · " + n(me.kills) + "/" + n(me.deaths) + "/" + n(me.assists) + " · " + main : "") + "</span></div>" +
        (r.result
          ? '<button type="button" class="rec-res ' + r.result + '" data-rec-cycle="' + escapeHtml(r.id) + '">' + RESULT_LABELS[r.result] + "</button>"
          : '<span class="rec-res-pick"><button type="button" class="win" data-rec-set="' + escapeHtml(r.id) + '" data-to="win">승</button>' +
            '<button type="button" class="loss" data-rec-set="' + escapeHtml(r.id) + '" data-to="loss">패</button></span>') +
        '<button type="button" class="rec-mini" data-rec-card="' + escapeHtml(r.id) + '">카드</button></div>';
    }).join("") + "</div>" + '<div class="rec-hint">승리·패배 표시를 누르면 바꿀 수 있어요</div>' +
      '<button type="button" class="rec-clear" data-rec-clear>기록 모두 지우기</button>';
    return html;
  }

  // 판 하나에서 뽑는 비교 지표. 같은 판 안 상대와 비교한 값이라 티어 차이의 영향을 덜 받는다.
  const sumOf = (rows, key, role) => (rows || []).filter((p) => !role || p.role === role).reduce((a, p) => a + (Number(p[key]) || 0), 0);
  const METRICS = [
    { key: "deaths", label: () => "내 죽음", value: (m, me) => me.deaths,
      say: (w, l) => (l > w ? "진 판에서 내가\n평균 " + fmt1(l - w) + "번 더 죽었어요" : "이긴 판에서 내가\n평균 " + fmt1(w - l) + "번 더 죽었어요") },
    { key: "tank", diff: true, label: () => "우리 탱커 − 상대 탱커 죽음", value: (m) => sumOf(m.ally, "deaths", "tank") - sumOf(m.enemy, "deaths", "tank"),
      say: (w, l) => (l > w ? "진 판에서 우리 탱커가\n상대 탱커보다 평균 " + fmt1(l) + "번 더 죽었어요" : "이긴 판에서 우리 탱커가\n상대 탱커보다 평균 " + fmt1(w) + "번 더 죽었어요") },
    { key: "share", label: (role) => (role === "support" ? "우리팀 치유 중 내 몫" : role ? "우리팀 피해 중 내 몫" : "우리팀 피해·치유 중 내 몫"),
      value: (m, me) => { const k = me.role === "support" ? "healing" : "damage"; const t = sumOf(m.ally, k); return t ? (Number(me[k]) || 0) / t * 100 : null; },
      unit: "%", say: (w, l) => (l < w ? "진 판에서 우리팀 안 내 몫이\n평균 " + fmt1(w - l) + "%p 적었어요" : "진 판에서 우리팀 안 내 몫이\n평균 " + fmt1(l - w) + "%p 많았어요") },
    { key: "team", diff: true, label: () => "우리팀 − 상대팀 죽음", value: (m) => sumOf(m.ally, "deaths") - sumOf(m.enemy, "deaths"),
      say: (w, l) => "진 판에서 우리팀이\n상대팀보다 평균 " + fmt1(l) + "번 더 죽었어요" },
  ];
  // 차이 지표는 부호를 붙이고, 개수·비율은 그대로 쓴다.
  const shown = (mt, v) => (v == null ? "-" : mt.unit === "%" ? fmt1(v) + "%" : (mt.diff && v > 0 ? "+" : "") + fmt1(v));
  const sd = (arr) => { const a = avg(arr); return a == null ? 0 : Math.sqrt(avg(arr.map((x) => (x - a) * (x - a)))); };

  function compareHtml(role) {
    const tabs = '<div class="rec-tabs">' + [["", "전체"], ["tank", "탱커"], ["damage", "딜러"], ["support", "힐러"]].map(([k, t]) =>
      '<button type="button" class="' + (k === role ? "on" : "") + '" data-rec-role="' + k + '">' + t + "</button>").join("") + "</div>";
    const games = load().filter((r) => (r.result === "win" || r.result === "loss") && selfOf(r.match) && (!role || selfOf(r.match).role === role));
    const win = games.filter((r) => r.result === "win");
    const loss = games.filter((r) => r.result === "loss");
    if (games.length < COMPARE_MIN || win.length < COMPARE_MIN_EACH || loss.length < COMPARE_MIN_EACH) {
      const pct = Math.min(100, Math.round((games.length / COMPARE_MIN) * 100));
      return tabs + '<div class="rec-locked"><b>승패를 남긴 판이 ' + COMPARE_MIN + "판 모이면\n비교해 드려요</b>" +
        '<div class="rec-progress"><i style="width:' + pct + '%"></i></div><span>지금 ' + games.length + "판 (이긴 판 " + win.length + " · 진 판 " + loss.length + ")</span>" +
        "<p>이긴 판과 진 판이 각각 " + COMPARE_MIN_EACH + "판 이상 있어야 해요.\n이긴 판만, 진 판만 올리면 비교가 치우쳐요.</p></div>";
    }
    const rows = METRICS.map((mt) => {
      const vals = (list) => list.map((r) => mt.value(r.match, selfOf(r.match))).filter((v) => v != null && !Number.isNaN(v));
      const w = avg(vals(win));
      const l = avg(vals(loss));
      const spread = sd(vals(games)) || 1;
      return { mt, w, l, gap: w == null || l == null ? 0 : Math.abs(l - w) / spread };
    });
    const top = rows.slice().sort((a, b) => b.gap - a.gap)[0];
    const scope = role ? ROLE_LABELS[role] + "로 한 판만 봤어요" : "모든 판을 봤어요";
    // 차이 지표는 가운데 선에서 왼쪽(우리가 덜 죽음)·오른쪽(더 죽음)으로 그린다.
    const bar = (cls, v, max, diff) => {
      const len = Math.max(2, (Math.abs(v || 0) / max) * (diff ? 50 : 100));
      const left = diff ? (v < 0 ? 50 - len : 50) : 0;
      return '<i class="' + cls + '" style="width:' + len + "%;margin-left:" + left + '%"></i>';
    };
    const bars = rows.map(({ mt, w, l }) => {
      const max = Math.max(Math.abs(w || 0), Math.abs(l || 0)) || 1;
      return '<div class="rec-metric' + (mt.diff ? " diff" : "") + '"><div class="rec-metric-head"><span>' + mt.label(role) + "</span><em>" + shown(mt, w) + " · " + shown(mt, l) + "</em></div>" +
        bar("w", w, max, mt.diff) + bar("l", l, max, mt.diff) + (mt.diff ? '<div class="rec-axis"><span>덜 죽음</span><span>더 죽음</span></div>' : "") + "</div>";
    }).join("");
    return tabs + '<div class="rec-legend"><span><i class="w"></i>이긴 판 ' + win.length + '판</span><span><i class="l"></i>진 판 ' + loss.length + "판</span></div>" +
      (top && top.gap > 0 ? '<div class="rec-top"><small>가장 크게 갈린 것</small><b>' + escapeHtml(top.mt.say(top.w, top.l)) + "</b><span>" + escapeHtml(scope) + "</span></div>" : "") +
      bars + '<p class="rec-note">내가 승패를 남긴 ' + games.length + "판 기준이에요.\n판이 쌓일수록 정확해져요.\n같은 판 안에서 상대와 비교한 값이라\n상대 수준이 판마다 달라도 비교할 수 있어요.</p>";
  }

  function tile(label, value) {
    return '<div class="rec-tile"><span>' + label + "</span><b>" + value + "</b></div>";
  }

  function openRecords() {
    const list = load();
    const overlay = modal("📈 내 기록", "", recordsHtml(list));
    const body = overlay.querySelector(".rec-body");
    const title = overlay.querySelector(".mx-head h3");
    let role = "";
    const show = (view) => {
      title.textContent = view === "compare" ? "이긴 판 vs 진 판" : "📈 내 기록";
      body.innerHTML = view === "compare" ? compareHtml(role) : recordsHtml(load());
      body.dataset.view = view;
      body.scrollTop = 0;
      if (view !== "list") body.insertAdjacentHTML("afterbegin", '<button type="button" class="rec-back" data-rec-view="list">← 내 기록</button>');
    };
    overlay.addEventListener("click", (e) => {
      const view = e.target.closest("[data-rec-view]");
      if (view) return show(view.dataset.recView);
      const rl = e.target.closest("[data-rec-role]");
      if (rl) { role = rl.dataset.recRole; return show("compare"); }
      const cyc = e.target.closest("[data-rec-cycle]");
      if (cyc) {
        const r = load().find((x) => x.id === cyc.dataset.recCycle);
        const next = { win: "loss", loss: "draw", draw: "win" }[r && r.result] || "win";
        setResult(cyc.dataset.recCycle, next);
        return show("list");
      }
      const set = e.target.closest("[data-rec-set]");
      if (set) { setResult(set.dataset.recSet, set.dataset.to); return show("list"); }
      const card = e.target.closest("[data-rec-card]");
      if (card) {
        const record = load().find((r) => r.id === card.dataset.recCard);
        if (record) openCard(record);
        return;
      }
      if (e.target.closest("[data-rec-clear]") && window.confirm("이 브라우저에 쌓인 기록을 모두 지울까요?")) {
        store([]);
        overlay.querySelector(".rec-body").innerHTML = recordsHtml([]);
      }
    });
  }

  window.MatchRecords = {
    init(opts) {
      iconBase = opts.iconBase || "";
      if (opts.escapeHtml) escapeHtml = opts.escapeHtml;
    },
    actionBar,
    openRecords,
  };
})();
