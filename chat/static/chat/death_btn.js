// "죽었어요" 버튼: 누구한테 죽었는지 고르면(내 영웅은 처음 한 번만) 상성표로 만든 두 줄 답을 바로 보여 준다.
// 답은 /chat/api/death/가 만든다(그래프를 타지 않는다). 상성표에 없는 짝이면 평소 채팅 질문으로 넘긴다.
// 창 모양은 "영웅으로 묻기"(hero_ask.js)의 ha-* 클래스를 그대로 쓴다.
(function () {
  const STORE_KEY = "owcoach.death.v1";
  const HERO_ASK_KEY = "owcoach.heroask.v1";
  const ROLE_TABS = [["all", "전체"], ["tank", "탱커"], ["damage", "딜러"], ["support", "힐러"]];

  let opts = null;
  let roles = null;
  let overlay = null;
  const pick = { step: "killer", me: null, enemies: [], enemiesLabel: "", role: "all" };

  function read(key) {
    try { return JSON.parse(localStorage.getItem(key)) || {}; } catch (e) { return {}; }
  }
  function save(data) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(data)); } catch (e) { /* 저장 못 해도 동작은 같다 */ }
  }

  const DIGIT_FINAL = { 0: 1, 1: 1, 2: 0, 3: 1, 4: 0, 5: 0, 6: 1, 7: 1, 8: 1, 9: 0 };
  function finalOf(word) {
    const ch = String(word).slice(-1);
    if (ch in DIGIT_FINAL) return DIGIT_FINAL[ch] ? (ch === "1" || ch === "7" || ch === "8" ? "ㄹ" : "o") : "";
    const code = ch.charCodeAt(0) - 0xac00;
    if (code < 0 || code > 11171) return "";
    const f = code % 28;
    return f === 0 ? "" : f === 8 ? "ㄹ" : "o";
  }
  const euro = (w) => (finalOf(w) && finalOf(w) !== "ㄹ" ? "으로" : "로");

  const esc = (t) => opts.escapeHtml(t);
  const icon = (name) => opts.iconBase + encodeURIComponent(name) + ".png";
  const face = (name, cls) => '<img class="' + cls + '" src="' + icon(name) + '" alt="' + esc(name) + '">';
  const allHeroes = () => roles.flatMap((r) => r.heroes.map((h) => h.name));

  // "영웅으로 묻기"에서 자주 고른 내 영웅이 앞에 온다.
  function heroesFor(role) {
    const counts = read(HERO_ASK_KEY).me || {};
    const list = role === "all" ? roles.flatMap((r) => r.heroes) : (roles.find((r) => r.code === role) || { heroes: [] }).heroes;
    return list.map((h, i) => ({ name: h.name, i, n: counts[h.name] || 0 })).sort((a, b) => b.n - a.n || a.i - b.i).map((h) => h.name);
  }

  function header() {
    return '<div class="ha-grip"></div><div class="ha-head"><h3>죽었어요</h3>' +
      '<button type="button" class="ha-icon-btn" data-dt="close" aria-label="닫기"><svg viewBox="0 0 18 18" aria-hidden="true"><path d="M4 4 L14 14 M14 4 L4 14" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg></button></div>';
  }
  function tabs() {
    return '<div class="ha-tabs">' + ROLE_TABS.map(([code, label]) =>
      '<button type="button" class="' + (pick.role === code ? "on" : "") + '" data-dt-role="' + code + '">' + label + "</button>").join("") + "</div>";
  }
  function grid(names, attr) {
    return '<div class="ha-grid">' + names.map((n) =>
      '<button type="button" class="ha-hero" ' + attr + '="' + esc(n) + '">' + face(n, "ha-hero-ico") + "<span>" + esc(n) + "</span></button>").join("") + "</div>";
  }

  function render() {
    const sheet = overlay.querySelector(".ha-sheet");
    if (pick.step === "me") {
      sheet.innerHTML = header() + '<div class="ha-title"><small>처음 한 번만 물어봐요</small><b>지금 내 영웅은?</b>' +
        "<em>앞에서 영웅을 말했으면 이 단계는 건너뛰어요</em></div>" + tabs() +
        '<div class="ha-scroll">' + grid(heroesFor(pick.role), "data-dt-me") + "</div>";
      return;
    }
    const others = heroesFor(pick.role).filter((n) => n !== pick.me);
    sheet.innerHTML = header() +
      '<div class="dt-me">' + face(pick.me, "dt-me-ico") + '<span><small>내 영웅</small><b>' + esc(pick.me) + "</b></span>" +
      '<button type="button" data-dt="change">바꾸기</button></div>' +
      '<div class="ha-title"><b>누구한테 죽었어요?</b></div>' +
      '<div class="ha-scroll">' +
      (pick.enemies.length ? '<div class="dt-label">' + esc(pick.enemiesLabel) + "</div>" + '<div class="dt-foes">' + grid(pick.enemies, "data-dt-killer") + "</div>" : "") +
      '<div class="dt-label">' + (pick.enemies.length ? "다른 영웅" : "영웅 고르기") + "</div>" + tabs() + grid(others, "data-dt-killer") + "</div>";
  }

  function onClick(e) {
    if (e.target === overlay) return close();
    const t = e.target.closest("[data-dt], [data-dt-role], [data-dt-me], [data-dt-killer]");
    if (!t) return;
    if (t.dataset.dtRole) { pick.role = t.dataset.dtRole; return render(); }
    if (t.dataset.dtMe) {
      pick.me = t.dataset.dtMe;
      save(Object.assign(read(STORE_KEY), { me: pick.me }));
      pick.step = "killer";
      pick.role = "all";
      return render();
    }
    if (t.dataset.dtKiller) return ask(pick.me, t.dataset.dtKiller);
    if (t.dataset.dt === "close") return close();
    if (t.dataset.dt === "change") { pick.step = "me"; pick.role = "all"; return render(); }
  }

  // 평소 채팅으로 보낼 문장("영웅으로 묻기"와 같은 틀이라 규칙 분류가 알아본다).
  const dieSentence = (me, killer) => "나는 " + me + "인데 상대 " + killer + " 때문에 자꾸 죽어. 어떻게 살아남아야 해?";
  const swapSentence = (me, killer) => "나는 " + me + "인데 상대 " + killer + " 때문에 힘들어. 뭘로 바꾸는 게 좋을까?";

  async function ask(me, killer) {
    if (opts.isBusy()) return;
    close();
    const heroes = { me, enemies: [killer] };
    opts.addMessage("user", me + euro(me) + " 하다가 " + killer + "한테 죽었어", { context: "죽었어요", heroes });
    opts.setBusy(true);
    opts.addTyping();
    let data = null;
    try {
      const res = await fetch("/chat/api/death/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": opts.getCsrf() },
        body: JSON.stringify({ hero: me, killer }),
      });
      data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || "답을 만들지 못했어요.");
    } catch (err) {
      opts.removeTyping();
      opts.setBusy(false);
      opts.showError(err.message || "연결에 실패했어요.");
      return;
    }
    opts.removeTyping();
    opts.setBusy(false);
    if (!data.found) {
      // 상성표에 없는 짝: 평소 채팅으로 묻는다.
      opts.send(dieSentence(me, killer), heroes);
      return;
    }
    opts.addMessage("ai", data.do + "\n" + data.why, { deathTip: data, turnId: data.turn_id });
  }

  // 답 말풍선 안의 카드(addMessage가 options.deathTip으로 부른다).
  function buildCard(tip) {
    const card = document.createElement("div");
    card.className = "dt-card";
    const tone = tip.bucket === "hard" ? "bad" : tip.bucket === "easy" ? "good" : "mid";
    const badge = tip.bucket === "hard" || tip.bucket === "easy" ? tip.hero + " " + tip.badge + " " + tip.score + "/5" : tip.badge;
    card.innerHTML = '<div class="dt-head">' + face(tip.hero, "dt-ico") + '<span class="dt-vs">vs</span>' + face(tip.killer, "dt-ico") +
      '<span class="dt-badge ' + tone + '">' + esc(badge) + "</span></div>" +
      '<p class="dt-do">' + esc(tip.do) + "</p>" + (tip.why ? '<p class="dt-why">' + esc(tip.why) + "</p>" : "") +
      '<div class="dt-actions"><button type="button" data-dt-more>더 자세히</button><button type="button" data-dt-swap>영웅을 바꾼다면?</button></div>';
    card.addEventListener("click", (e) => {
      if (opts.isBusy()) return;
      const heroes = { me: tip.hero, enemies: [tip.killer] };
      if (e.target.closest("[data-dt-more]")) opts.send(dieSentence(tip.hero, tip.killer), heroes);
      else if (e.target.closest("[data-dt-swap]")) opts.send(swapSentence(tip.hero, tip.killer), heroes);
    });
    return card;
  }

  async function open() {
    if (opts.isBusy()) return;
    if (!roles) {
      try { roles = await opts.loadRoles(); } catch (e) { return; }
    }
    // 세션이 아는 내 영웅·상대 조합이 먼저, 없으면 이 기기에 남긴 값.
    let ctx = {};
    try { ctx = await (await fetch("/chat/api/death/", { headers: { Accept: "application/json" } })).json(); } catch (e) { /* 없으면 기기 기록만 쓴다 */ }
    const known = new Set(allHeroes());
    const last = read(HERO_ASK_KEY).last || {};
    pick.me = [ctx.hero, read(STORE_KEY).me, last.me].find((n) => n && known.has(n)) || null;
    const sessionFoes = (ctx.enemies || []).filter((n) => known.has(n));
    const lastFoes = (last.enemies || []).filter((n) => known.has(n));
    pick.enemies = (sessionFoes.length ? sessionFoes : lastFoes).filter((n) => n !== pick.me);
    pick.enemiesLabel = sessionFoes.length ? "이번 판 상대" : "최근에 고른 상대";
    pick.step = pick.me ? "killer" : "me";
    pick.role = "all";
    overlay?.remove();
    overlay = document.createElement("div");
    overlay.className = "ha-overlay";
    overlay.innerHTML = '<div class="ha-sheet dt-sheet" role="dialog" aria-modal="true" aria-label="죽었어요"></div>';
    overlay.addEventListener("click", onClick);
    document.body.appendChild(overlay);
    render();
    requestAnimationFrame(() => overlay.classList.add("show"));
  }

  function close() {
    if (!overlay) return;
    const el = overlay;
    overlay = null;
    el.classList.remove("show");
    setTimeout(() => el.remove(), 220);
  }

  function init(o) {
    opts = o;
    document.getElementById("deathBtn")?.addEventListener("click", open);
  }

  window.DeathBtn = { init, open, buildCard };
})();
