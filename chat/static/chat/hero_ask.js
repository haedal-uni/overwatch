// "영웅으로 묻기": 내 영웅 → 상대 영웅 → 상황을 아이콘으로 골라 질문 문장을 만들어 보낸다.
// 문장은 규칙 분류가 알아보는 모양으로 만든다("나는 X인데" = 내 영웅, "상대 Y" = 상대, "X로 플레이하는데 … 어떻게 상대" = stay).
// 영웅 가이드의 "우리 팀 고르고 조합 평가받기"는 같은 창을 팀 모드(mode "team")로 연다.
(function () {
  const STORE_KEY = "owcoach.heroask.v1";
  const MAX_ENEMIES = 5;
  const MAX_ALLIES = 4;
  const ROLE_TABS = [["all", "전체"], ["tank", "탱커"], ["damage", "딜러"], ["support", "힐러"]];

  let opts = null;
  let roles = null;
  let overlay = null;
  const pick = { step: 1, role: "all", me: null, enemies: [], situation: "die", extra: "", mode: "foe" };

  function load() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch (e) { return {}; }
  }
  function save(data) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(data)); } catch (e) { /* 저장 못 해도 동작은 같다 */ }
  }

  // 받침 유무로 조사를 고른다(숫자는 읽는 소리 기준).
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
  const eulReul = (w) => (finalOf(w) ? "을" : "를");
  const iya = (w) => (finalOf(w) ? "이야" : "야");

  function situations() {
    const me = pick.me || "내 영웅";
    return [
      { key: "die", title: "자꾸 죽어요", sub: "살아남는 법" },
      { key: "stay", title: me + euro(me) + " 어떻게 상대해요?", sub: "영웅 그대로, 상대하는 법" },
      { key: "target", title: "누구부터 노려요?", sub: "상성표 순서로 우선 타겟" },
      { key: "swap", title: "뭘로 바꿔야 해요?", sub: "카운터 영웅 카드" },
    ];
  }

  function sentence() {
    const me = pick.me;
    if (pick.mode === "team") {
      const team = [me].concat(pick.enemies);
      return "우리팀 조합은 " + team.join(", ") + iya(team[team.length - 1]) + ". 이 조합의 강점이랑 약점 알려줘";
    }
    const list = pick.enemies.join(", ");
    const last = pick.enemies[pick.enemies.length - 1] || "";
    let text = "";
    if (pick.situation === "stay") text = me + euro(me) + " 플레이하는데 상대 " + list + eulReul(last) + " 어떻게 상대해야 할까?";
    else if (pick.situation === "target") text = "나는 " + me + "인데 상대 조합이 " + list + iya(last) + ". 누구부터 노려야 해?";
    else if (pick.situation === "swap") text = "나는 " + me + "인데 상대 " + list + " 때문에 힘들어. 뭘로 바꾸는 게 좋을까?";
    else text = "나는 " + me + "인데 상대 " + list + " 때문에 자꾸 죽어. 어떻게 살아남아야 해?";
    const extra = pick.extra.trim();
    return extra ? text + " " + extra : text;
  }

  // 자주 고른 영웅이 앞에 오게 정렬한다.
  function heroesFor(role, countKey) {
    const counts = load()[countKey] || {};
    const list = role === "all" ? roles.flatMap((r) => r.heroes) : (roles.find((r) => r.code === role) || { heroes: [] }).heroes;
    return list.map((h, i) => ({ name: h.name, i, n: counts[h.name] || 0 })).sort((a, b) => b.n - a.n || a.i - b.i).map((h) => h.name);
  }

  const esc = (t) => opts.escapeHtml(t);
  const icon = (name) => opts.iconBase + encodeURIComponent(name) + ".png";
  const face = (name, cls) => '<img class="' + cls + '" src="' + icon(name) + '" alt="' + esc(name) + '">';

  function header(back) {
    const bars = (pick.mode === "team" ? [1, 2] : [1, 2, 3]).map((n) => '<i class="' + (n <= pick.step ? "on" : "") + '"></i>').join("");
    return '<div class="ha-grip"></div><div class="ha-head">' +
      (back ? '<button type="button" class="ha-icon-btn" data-ha="back" aria-label="뒤로"><svg viewBox="0 0 18 18" aria-hidden="true"><path d="M11 3 L5 9 L11 15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg></button>' : "") +
      '<h3>영웅으로 물어보기</h3><button type="button" class="ha-icon-btn" data-ha="close" aria-label="닫기"><svg viewBox="0 0 18 18" aria-hidden="true"><path d="M4 4 L14 14 M14 4 L4 14" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg></button></div>' +
      '<div class="ha-bars">' + bars + "</div>";
  }

  function summary() {
    const sep = pick.mode === "team" ? '<span class="ha-vs">+</span>' : '<span class="ha-vs">vs</span>';
    return '<div class="ha-summary"><span class="ha-me">' + face(pick.me, "ha-me-ico") + "나 · " + esc(pick.me) + "</span>" +
      (pick.enemies.length ? sep + pick.enemies.map((n) => face(n, "ha-foe-ico")).join("") : "") + "</div>";
  }

  function tabs() {
    return '<div class="ha-tabs">' + ROLE_TABS.map(([code, label]) =>
      '<button type="button" class="' + (pick.role === code ? "on" : "") + '" data-ha-role="' + code + '">' + label + "</button>").join("") + "</div>";
  }

  function grid(names, isOn) {
    return '<div class="ha-grid">' + names.map((n) =>
      '<button type="button" class="ha-hero' + (isOn(n) ? " on" : "") + '" data-ha-hero="' + esc(n) + '">' + face(n, "ha-hero-ico") + "<span>" + esc(n) + "</span></button>").join("") + "</div>";
  }

  function render() {
    const sheet = overlay.querySelector(".ha-sheet");
    let html = "";
    if (pick.step === 1) {
      html = header(false) + '<div class="ha-title"><small>1 / 3</small><b>지금 하는 영웅은?</b></div>' + tabs() +
        '<div class="ha-scroll">' + grid(heroesFor(pick.role, "me"), (n) => n === pick.me) + "</div>" +
        '<button type="button" class="ha-next" data-ha="next"' + (pick.me ? "" : " disabled") + ">" + (pick.me ? esc(pick.me + euro(pick.me)) + " 다음" : "영웅을 골라 주세요") + "</button>";
    } else if (pick.step === 2 && pick.mode === "team") {
      html = header(true) + '<div class="ha-title"><small>2 / 2</small><b>같은 팀 영웅은?</b><em>나를 뺀 팀원, 최대 ' + MAX_ALLIES + "명</em></div>" + summary() + tabs() +
        '<div class="ha-scroll">' + grid(heroesFor(pick.role, "ally").filter((n) => n !== pick.me), (n) => pick.enemies.includes(n)) + "</div>" +
        '<button type="button" class="ha-next" data-ha="send"' + (pick.enemies.length ? "" : " disabled") + ">" + (pick.enemies.length ? "팀원 " + pick.enemies.length + "명으로 보내기" : "팀원을 골라 주세요") + "</button>";
    } else if (pick.step === 2) {
      html = header(true) + '<div class="ha-title"><small>2 / 3</small><b>신경 쓰이는 상대는?</b><em>여러 명 골라도 돼요 (최대 ' + MAX_ENEMIES + "명)</em></div>" + summary() + tabs() +
        '<div class="ha-scroll">' + grid(heroesFor(pick.role, "enemy"), (n) => pick.enemies.includes(n)) + "</div>" +
        '<button type="button" class="ha-next" data-ha="next"' + (pick.enemies.length ? "" : " disabled") + ">" + (pick.enemies.length ? "상대 " + pick.enemies.length + "명으로 다음" : "상대를 골라 주세요") + "</button>";
    } else {
      html = header(true) + '<div class="ha-title"><small>3 / 3</small><b>무엇이 궁금해요?</b></div>' + summary() +
        '<div class="ha-scroll"><div class="ha-sits">' + situations().map((s) =>
          '<button type="button" class="ha-sit' + (pick.situation === s.key ? " on" : "") + '" data-ha-sit="' + s.key + '"><b>' + esc(s.title) + "</b><span>" + esc(s.sub) + "</span></button>").join("") + "</div>" +
        '<div class="ha-preview"><small>이렇게 보내져요</small><p id="haPreview">' + esc(sentence()) + "</p></div>" +
        '<label class="ha-extra"><span>덧붙일 말 (선택)</span><input type="text" id="haExtra" maxlength="120" placeholder="예: 우리 탱커는 라인이야" value="' + esc(pick.extra) + '"></label></div>' +
        '<button type="button" class="ha-next" data-ha="send">보내기</button>';
    }
    sheet.innerHTML = html;
    const extra = sheet.querySelector("#haExtra");
    if (extra) extra.addEventListener("input", () => { pick.extra = extra.value; sheet.querySelector("#haPreview").textContent = sentence(); });
  }

  function onClick(e) {
    if (e.target === overlay) return close();
    const t = e.target.closest("[data-ha], [data-ha-role], [data-ha-hero], [data-ha-sit]");
    if (!t) return;
    if (t.dataset.haRole) { pick.role = t.dataset.haRole; return render(); }
    if (t.dataset.haSit) { pick.situation = t.dataset.haSit; return render(); }
    if (t.dataset.haHero) {
      const name = t.dataset.haHero;
      if (pick.step === 1) pick.me = name;
      else if (pick.enemies.includes(name)) pick.enemies = pick.enemies.filter((n) => n !== name);
      else if (pick.enemies.length < (pick.mode === "team" ? MAX_ALLIES : MAX_ENEMIES)) pick.enemies.push(name);
      return render();
    }
    const act = t.dataset.ha;
    if (act === "close") return close();
    if (act === "back") { pick.step -= 1; return render(); }
    if (act === "next") { pick.step += 1; pick.role = "all"; return render(); }
    if (act === "send") send();
  }

  function send() {
    if (opts.isBusy && opts.isBusy()) return;
    const data = load();
    const team = pick.mode === "team";
    const countKey = team ? "ally" : "enemy";
    data.me = data.me || {};
    data[countKey] = data[countKey] || {};
    data.me[pick.me] = (data.me[pick.me] || 0) + 1;
    pick.enemies.forEach((n) => { data[countKey][n] = (data[countKey][n] || 0) + 1; });
    // "같은 영웅으로 묻기"는 상대를 고른 질문만 이어받는다.
    if (!team) data.last = { me: pick.me, enemies: pick.enemies.slice() };
    save(data);
    const text = sentence();
    const heroes = team ? { me: pick.me, allies: pick.enemies.slice() } : { me: pick.me, enemies: pick.enemies.slice() };
    close();
    pick.extra = "";
    renderButtons();
    opts.send(text, heroes);
  }

  // preset: { me, mode } — 영웅 가이드에서 내 영웅을 고른 채로 2단계부터 연다.
  async function open(step, preset) {
    if (!roles) {
      try { roles = await opts.loadRoles(); } catch (e) { return; }
    }
    const mode = (preset && preset.mode) || "foe";
    // 팀 모드에서 고른 팀원이 상대 목록으로 넘어가지 않게 한다.
    if (mode !== pick.mode) pick.enemies = [];
    pick.mode = mode;
    if (preset && preset.me) { pick.me = preset.me; pick.enemies = []; pick.extra = ""; }
    if (step === 3) {
      const last = load().last;
      if (!last || !last.me) step = 1;
      else { pick.me = last.me; pick.enemies = last.enemies.slice(); }
    }
    pick.step = step || 1;
    pick.role = "all";
    overlay?.remove();
    overlay = document.createElement("div");
    overlay.className = "ha-overlay";
    overlay.innerHTML = '<div class="ha-sheet" role="dialog" aria-modal="true" aria-label="영웅으로 물어보기"></div>';
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

  // 입력창 위 버튼. 고른 적이 있으면 첫 버튼은 "새 영웅으로 묻기"(처음부터),
  // 둘째 버튼은 지난번 영웅 얼굴을 단 "같은 영웅으로 묻기"(상황 고르기부터)가 된다.
  function renderButtons() {
    const last = load().last;
    const used = !!(last && last.me);
    const label = document.getElementById("heroAskLabel");
    const again = document.getElementById("heroAgainBtn");
    const faces = document.getElementById("heroAgainFaces");
    if (label) label.textContent = used ? "새 영웅으로 묻기" : "영웅으로 묻기";
    if (faces) faces.innerHTML = used ? [last.me].concat(last.enemies.slice(0, 2)).map((n) => face(n, "ha-face")).join("") : "";
    if (again) again.hidden = !used;
  }

  // 보낸 말풍선 위에 붙일 영웅 얼굴 줄.
  function bubbleFaces(heroes) {
    if (!heroes || !heroes.me) return "";
    if (heroes.allies) return '<div class="ha-bubble-faces">' + [heroes.me].concat(heroes.allies).map((n) => face(n, "ha-bf")).join("") + "</div>";
    return '<div class="ha-bubble-faces">' + face(heroes.me, "ha-bf") + '<span>vs</span>' + heroes.enemies.map((n) => face(n, "ha-bf")).join("") + "</div>";
  }

  function init(o) {
    opts = o;
    document.getElementById("heroAskBtn")?.addEventListener("click", () => open(1));
    document.getElementById("heroAgainBtn")?.addEventListener("click", () => open(3));
    renderButtons();
  }

  window.HeroAsk = { init, open, bubbleFaces };
})();
