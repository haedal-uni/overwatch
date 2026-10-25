// 카운터 카드 대결: 상대가 영웅 카드를 내면 손패 5장 중 맞받아칠 카드를 낸다. 7라운드, 이긴 라운드가 많으면 승리.
// 승패는 상성표(/chat/api/matchups/?hero=상대) 판정 그대로다: 상대에게 강한 영웅이면 승, 상대가 강한 영웅이면 패, 그 밖은 무승부.
// 손패에는 이기는 카드와 지는 카드를 1~2장씩 섞고, 다음 라운드 상대는 절반쯤 내가 낸 카드에 강한 영웅으로 바꾼다.
(function () {
  const STORE_KEY = "owcoach.cardgame.v1";
  const ROUNDS = 7;
  const HAND = 5;
  const ROLE_LABELS = { tank: "탱커", damage: "딜러", support: "힐러" };

  let opts = null;
  let roles = null;
  let overlay = null;
  const charts = {};
  let g = null;

  function load() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch (e) { return {}; }
  }
  function save(data) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(data)); } catch (e) { /* 저장 못 해도 동작은 같다 */ }
  }

  const rand = (n) => Math.floor(Math.random() * n);
  const shuffle = (arr) => {
    for (let i = arr.length - 1; i > 0; i -= 1) {
      const j = rand(i + 1);
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
  };
  const sample = (arr, n) => shuffle(arr.slice()).slice(0, n);
  // 라운드 사이에 읽을 만큼만: 앞 두 문장(길면 한 문장).
  function shortNote(note) {
    const parts = String(note || "").split(/(?<=[.!?])\s+/).filter(Boolean);
    const two = parts.slice(0, 2).join(" ");
    return two.length > 110 ? parts[0] : two;
  }
  const esc = (t) => opts.escapeHtml(t);
  const icon = (name) => opts.iconBase + encodeURIComponent(name) + ".png";

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

  function roleOf(name) {
    const r = roles.find((x) => x.heroes.some((h) => h.name === name));
    return r ? r.code : "";
  }
  const pool = () => roles.flatMap((r) => r.heroes.filter((h) => h.has_data).map((h) => h.name));

  async function chart(name) {
    if (!charts[name]) charts[name] = (await opts.fetchHero(name)).hero;
    return charts[name];
  }
  const names = (list) => (list || []).map((e) => e.hero);

  // 이 상대를 맞받아칠 수 있는 카드가 있는 상대만 낸다.
  async function pickOpponent(prevMine) {
    if (prevMine && Math.random() < 0.55) {
      const strong = names((await chart(prevMine)).hard);
      const choices = strong.filter((n) => pool().includes(n) && n !== g.opp);
      if (choices.length) return { name: choices[rand(choices.length)], swapped: true };
    }
    for (let i = 0; i < 12; i += 1) {
      const name = pool()[rand(pool().length)];
      if (name !== g.opp && names((await chart(name)).hard).length) return { name, swapped: false };
    }
    return { name: pool()[0], swapped: false };
  }

  async function deal() {
    const c = await chart(g.opp);
    const win = sample(names(c.hard).filter((n) => pool().includes(n)), 1 + rand(2));
    const lose = sample(names(c.easy).filter((n) => pool().includes(n)), 1 + rand(2));
    const used = new Set([g.opp].concat(win, lose));
    const rest = sample(pool().filter((n) => !used.has(n)), HAND - win.length - lose.length);
    g.hand = shuffle(win.concat(lose, rest)).slice(0, HAND);
    g.sel = null;
  }

  function judge(mine) {
    const c = charts[g.opp];
    const find = (key) => (c[key] || []).find((e) => e.hero === mine);
    let e = find("hard");
    if (e) return { res: "win", badge: mine + " 유리 " + e.score + "/5", note: e.note };
    e = find("easy");
    if (e) return { res: "lose", badge: g.opp + " 유리 " + e.score + "/5", note: e.note };
    e = find("mixed");
    if (e) return { res: "draw", badge: "상황에 따라 갈림", note: e.note };
    return { res: "draw", badge: "비슷한 상성", note: "" };
  }

  // 이번 손패에서 상대를 이기는 카드(유리 점수가 큰 순).
  function answersInHand() {
    const hard = charts[g.opp].hard || [];
    return hard.filter((e) => g.hand.includes(e.hero)).sort((a, b) => b.score - a.score);
  }
  const firstSentence = (note) => String(note || "").split(/(?<=[.!?])\s+/)[0] || "";

  function answerBlock(r) {
    const others = r.answers.filter((e) => e.hero !== r.mine);
    if (r.res === "win") {
      if (!others.length) return "";
      return '<div class="cb-answer"><small>이 카드로도 이길 수 있었어요</small><div class="cb-answer-faces">' +
        others.map((e) => '<span><img src="' + icon(e.hero) + '" alt="">' + esc(e.hero) + "</span>").join("") + "</div></div>";
    }
    if (!others.length) return '<div class="cb-answer"><small>이번 손패에는 이기는 카드가 없었어요</small></div>';
    const best = others[0];
    return '<div class="cb-answer"><small>정답 카드</small><div class="cb-answer-faces">' +
      others.map((e) => '<span><img src="' + icon(e.hero) + '" alt="">' + esc(e.hero) + ' <em>유리 ' + e.score + "/5</em></span>").join("") + "</div>" +
      (best.note ? "<p>" + esc(firstSentence(best.note)) + "</p>" : "") + "</div>";
  }

  // ---------- 화면 ----------
  function top(title) {
    return '<div class="cb-top"><button type="button" class="cb-icon" data-cb="close" aria-label="닫기"><svg viewBox="0 0 18 18" aria-hidden="true"><path d="M4 4 L14 14 M14 4 L4 14" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg></button>' +
      "<h3>" + esc(title) + '</h3><span class="cb-pill">일반적인 상성 기준</span></div>';
  }
  function scoreLine() {
    return '<div class="cb-score"><span>라운드 <b>' + g.round + "</b> / " + ROUNDS + '</span><span class="cb-pts"><small>나</small><b class="me">' + g.win +
      '</b><i>:</i><b class="op">' + g.lose + "</b><small>상대</small></span></div>";
  }
  function card(name, cls, extra) {
    return '<div class="cb-card ' + (cls || "") + '"><img src="' + icon(name) + '" alt=""><b>' + esc(name) + "</b><small>" + (ROLE_LABELS[roleOf(name)] || "") + "</small>" + (extra || "") + "</div>";
  }

  function renderIntro() {
    overlay.querySelector(".cb-panel").innerHTML = top("카운터 카드 대결") +
      '<div class="cb-intro"><div class="cb-vsfaces"><img src="' + icon("디바") + '" alt=""><span>VS</span><img src="' + icon("파라") + '" alt=""></div>' +
      "<h4>일반적인 상성 기준으로 진행돼요</h4><p>승패는 공략 문서에 적힌 상성으로 정해요.<br>실제 경기에서는 실력과 상황에 따라<br>결과가 달라질 수 있어요.</p>" +
      '<ol><li>상대가 영웅 카드를 내요</li><li>손패 5장 중 맞받아칠 카드를 골라요</li><li>' + ROUNDS + "라운드 중 더 많이 이기면 승리</li></ol>" +
      '<button type="button" class="cb-main" data-cb="start">시작하기</button>' +
      '<label class="cb-skip"><input type="checkbox" id="cbSkip">다음부터 안 보기</label></div>';
  }

  function renderPlay() {
    const panel = overlay.querySelector(".cb-panel");
    panel.innerHTML = top("카운터 카드 대결") + scoreLine() +
      '<div class="cb-stage"><small class="cb-op-label">' + (g.swapped ? "상대가 영웅을 바꿨어요" : "상대가 낸 카드") + "</small>" +
      card(g.opp, "op big") + "<p>" + esc(g.opp) + eulReul(g.opp) + " 받아칠 카드를 골라요</p></div>" +
      '<div class="cb-hand-label">내 손패</div><div class="cb-hand">' + g.hand.map((n) =>
        '<button type="button" class="cb-pick' + (g.sel === n ? " on" : "") + '" data-cb-card="' + esc(n) + '">' + card(n) + "</button>").join("") + "</div>" +
      '<button type="button" class="cb-main" data-cb="play"' + (g.sel ? "" : " disabled") + ">" + (g.sel ? esc(g.sel) + " 내기" : "카드를 골라 주세요") + "</button>";
  }

  function renderResult() {
    const r = g.last;
    const title = r.res === "win" ? "이겼어요!" : r.res === "lose" ? "졌어요" : "비겼어요";
    overlay.querySelector(".cb-panel").innerHTML = top("카운터 카드 대결") + scoreLine() +
      '<div class="cb-result ' + r.res + '"><div class="cb-res-title">' + title + "</div>" +
      '<div class="cb-duel">' + card(r.mine, "mine", '<em>내 카드</em>') + '<span class="cb-vs">VS</span>' + card(r.opp, "op", '<em>상대 카드</em>') + "</div>" +
      '<span class="cb-badge ' + r.res + '">' + esc(r.badge) + "</span>" + (r.note ? '<p class="cb-note">' + esc(r.res === "win" ? shortNote(r.note) : firstSentence(r.note)) + "</p>" : "") + answerBlock(r) + "</div>" +
      '<button type="button" class="cb-ask" data-cb="ask">' + esc(r.mine + euro(r.mine) + " " + r.opp + eulReul(r.opp) + " 상대하는 법 물어보기") +
      '<svg viewBox="0 0 18 18" aria-hidden="true"><path d="M7 3 L13 9 L7 15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg></button>' +
      '<div class="cb-fill"></div><button type="button" class="cb-main" data-cb="next">' + (g.round >= ROUNDS ? "결과 보기" : "다음 라운드") + "</button>";
  }

  function renderEnd() {
    const res = g.win > g.lose ? "승리" : g.win < g.lose ? "패배" : "무승부";
    const lost = g.log.filter((r) => r.res === "lose");
    overlay.querySelector(".cb-panel").innerHTML = top("카운터 카드 대결") +
      '<div class="cb-end"><small>한 판 끝</small><b class="' + (g.win >= g.lose ? "good" : "bad") + '">' + res + "</b>" +
      '<div class="cb-final"><span class="me">' + g.win + '</span> : <span class="op">' + g.lose + "</span></div></div>" +
      '<div class="cb-scroll">' + (lost.length ? '<div class="cb-hand-label">진 라운드 다시 보기</div>' + lost.map((r) =>
        '<div class="cb-lost"><div class="cb-lost-head"><img src="' + icon(r.mine) + '" alt="' + esc(r.mine) + '"><span>vs</span><img src="' + icon(r.opp) + '" alt="' + esc(r.opp) + '"><span class="cb-badge lose">' +
        esc(r.badge) + "</span></div>" + (r.note ? "<p>" + esc(shortNote(r.note)) + "</p>" : "") +
        (r.answers.length ? '<p class="cb-lost-answer">정답: ' + r.answers.map((e) => esc(e.hero)).join(", ") + "</p>" : "") + "</div>").join("") : '<p class="cb-perfect">진 라운드가 없어요!</p>') + "</div>" +
      '<button type="button" class="cb-main" data-cb="again">한 판 더</button><button type="button" class="cb-sub" data-cb="save">결과 카드 저장</button>';
  }

  // ---------- 흐름 ----------
  async function startGame() {
    g = { round: 1, win: 0, lose: 0, log: [], opp: null, hand: [], sel: null, swapped: false, last: null };
    const o = await pickOpponent(null);
    g.opp = o.name;
    await deal();
    renderPlay();
  }

  async function nextRound() {
    if (g.round >= ROUNDS) {
      const data = load();
      data.played = (data.played || 0) + 1;
      if (g.win > g.lose) data.won = (data.won || 0) + 1;
      save(data);
      return renderEnd();
    }
    g.round += 1;
    const o = await pickOpponent(g.last.mine);
    g.opp = o.name;
    g.swapped = o.swapped;
    await deal();
    renderPlay();
  }

  function playCard() {
    if (!g.sel) return;
    const r = Object.assign({ mine: g.sel, opp: g.opp, answers: answersInHand() }, judge(g.sel));
    if (r.res === "win") g.win += 1;
    else if (r.res === "lose") g.lose += 1;
    g.log.push(r);
    g.last = r;
    renderResult();
  }

  // 결과 카드: 1080px 정사각 PNG.
  function saveImage() {
    const cv = document.createElement("canvas");
    cv.width = cv.height = 1080;
    const x = cv.getContext("2d");
    x.fillStyle = "#eef5ff";
    x.fillRect(0, 0, 1080, 1080);
    x.fillStyle = "#132033";
    x.textAlign = "center";
    x.font = "800 64px 'Noto Sans KR', sans-serif";
    x.fillText("카운터 카드 대결", 540, 230);
    x.font = "800 150px 'Noto Sans KR', sans-serif";
    x.fillStyle = g.win >= g.lose ? "#2563eb" : "#c2410c";
    x.fillText(g.win > g.lose ? "승리" : g.win < g.lose ? "패배" : "무승부", 540, 480);
    x.font = "800 120px 'Noto Sans KR', sans-serif";
    x.fillStyle = "#132033";
    x.fillText(g.win + " : " + g.lose, 540, 660);
    x.font = "400 40px 'Noto Sans KR', sans-serif";
    x.fillStyle = "#6b7a92";
    x.fillText("일반적인 상성 기준 · " + new Date().toLocaleDateString("ko-KR"), 540, 860);
    const a = document.createElement("a");
    a.download = "카운터카드대결.png";
    a.href = cv.toDataURL("image/png");
    a.click();
  }

  function onClick(e) {
    const t = e.target.closest("[data-cb], [data-cb-card]");
    if (!t || overlay.classList.contains("busy")) return;
    if (t.dataset.cbCard) { g.sel = t.dataset.cbCard; return renderPlay(); }
    const act = t.dataset.cb;
    if (act === "close") return close();
    if (act === "play") return playCard();
    if (act === "save") return saveImage();
    if (act === "ask") {
      const r = g.last;
      if (opts.isBusy()) return;
      close();
      opts.send(r.mine + euro(r.mine) + " 플레이하는데 상대 " + r.opp + eulReul(r.opp) + " 어떻게 상대해야 할까? 스킬 활용법 위주로 알려줘",
        { me: r.mine, enemies: [r.opp] });
      return;
    }
    const step = act === "start" ? () => {
      if (overlay.querySelector("#cbSkip")?.checked) save(Object.assign(load(), { skipIntro: true }));
      return startGame();
    } : act === "next" ? nextRound : act === "again" ? startGame : null;
    if (!step) return;
    overlay.classList.add("busy");
    Promise.resolve(step()).catch(() => opts.showError("상성표를 불러오지 못했어요.")).finally(() => overlay && overlay.classList.remove("busy"));
  }

  async function open() {
    if (!roles) {
      try { roles = await opts.loadRoles(); } catch (e) { return; }
    }
    overlay?.remove();
    overlay = document.createElement("div");
    overlay.className = "cb-overlay";
    overlay.innerHTML = '<div class="cb-panel" role="dialog" aria-modal="true" aria-label="카운터 카드 대결"></div>';
    overlay.addEventListener("click", onClick);
    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add("show"));
    if (load().skipIntro) {
      overlay.classList.add("busy");
      try { await startGame(); } catch (e) { close(); opts.showError("상성표를 불러오지 못했어요."); return; }
      overlay?.classList.remove("busy");
    } else renderIntro();
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
    document.getElementById("cardBattleBtn")?.addEventListener("click", open);
  }

  window.CardBattle = { init, open };
})();
