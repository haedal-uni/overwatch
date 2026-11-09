// 챗봇 이용 횟수를 이 브라우저에만 세어 둔다. 달빛 옷장 이벤트가 같은 주소의 localStorage에서 이 키를 읽는다.
// 스탯창은 같은 판(양 팀 모든 줄의 영웅·숫자가 같음)을 이 브라우저에서 다시 올린 것이면 세지 않는다. 내 줄 위치는 보지 않는다.
// 듀오가 같은 판을 각자 자기 기기에서 올린 것은 기기 저장소가 달라 따로 센다.
(function () {
  "use strict";

  // KEY는 연결된 기기 합계(달빛 옷장이 읽는다), SELF_KEY는 이 기기에서 센 몫이다.
  const KEY = "owcoach.events.v1";
  const SELF_KEY = "owcoach.events.self.v1";
  const MAX_SHOTS = 300;
  const MAX_DAYS = 20; // 연속 질문 이벤트용 날짜별 질문 수를 남기는 날 수
  const dayKey = (d) => d.getFullYear() + "-" + (d.getMonth() + 1) + "-" + d.getDate();
  function cleanDays(v) {
    const out = {};
    if (v && typeof v === "object") Object.keys(v).forEach((k) => { if (/^\d{4}-\d{1,2}-\d{1,2}$/.test(k)) out[k] = Math.max(0, Math.min(Number(v[k]) || 0, 10000)); });
    return trimDays(out);
  }
  function trimDays(days) {
    const t = (k) => { const p = k.split("-").map(Number); return new Date(p[0], p[1] - 1, p[2]).getTime(); };
    Object.keys(days).sort((a, b) => t(b) - t(a)).slice(MAX_DAYS).forEach((k) => { delete days[k]; });
    return days;
  }
  const CHAT_SINCE = new Date(2026, 10, 3).getTime();
  const COUNT_SINCE = new Date(2026, 10, 4).getTime(); // chat1104·shots1104는 이날부터 센다(0에서 시작)
  const COUNT11_SINCE = 1793779777751; // chat11·shots11(11월 미션)은 이 시각부터 센다(0에서 시작)

  function read(key) {
    try {
      const d = JSON.parse(localStorage.getItem(key) || "null");
      if (!d) return null;
      return { days: cleanDays(d.days), chat: Number(d.chat) || 0, chat1103: Number(d.chat1103) || 0, chat1104: Number(d.chat1104) || 0, chat11: Number(d.chat11) || 0, shots11: Array.isArray(d.shots11) ? d.shots11 : [], shots1104: Array.isArray(d.shots1104) ? d.shots1104 : [], cards: Number(d.cards) || 0, cardWins: Number(d.cardWins) || 0, cardPerfect: Number(d.cardPerfect) || 0, shots: Array.isArray(d.shots) ? d.shots : [] };
    } catch (e) {
      return null;
    }
  }
  const empty = () => ({ days: {}, chat: 0, chat1103: 0, chat1104: 0, chat11: 0, shots11: [], shots1104: [], cards: 0, cardWins: 0, cardPerfect: 0, shots: [] });
  const load = () => read(KEY) || empty();
  // 이 기기 몫이 아직 없으면(동기화 전부터 쓰던 기기) 지금 합계가 곧 이 기기 몫이다.
  const loadSelf = () => read(SELF_KEY) || load();
  function write(key, d) {
    try { localStorage.setItem(key, JSON.stringify(d)); } catch (e) { /* 저장이 막힌 브라우저 */ }
  }
  // 합계와 이 기기 몫을 같은 방식으로 함께 바꾼다.
  function bump(change) {
    const total = load();
    const self = loadSelf();
    change(total);
    change(self);
    write(KEY, total);
    write(SELF_KEY, self);
    if (window.OwSync) window.OwSync.dirty();
  }

  const row = (p) => [p.hero, p.kills, p.deaths, p.assists, p.damage, p.healing, p.mitigation].join(",");

  function fingerprint(match) {
    const ally = match.ally || [];
    const enemy = match.enemy || [];
    return ally.map(row).join("|") + "#" + enemy.map(row).join("|");
  }

  // 간단한 32비트 해시(저장 크기를 줄이려는 용도).
  function hash(text) {
    let h = 5381;
    for (let i = 0; i < text.length; i += 1) h = ((h << 5) + h + text.charCodeAt(i)) | 0;
    return (h >>> 0).toString(36) + text.length.toString(36);
  }

  // 같은 질문은 하루에 한 번만 센다. 오늘 센 질문의 해시만 이 브라우저에 두고(연결 기기 합계와는 따로, 날짜가 바뀌면 비운다) 공백·문장부호·대소문자는 무시한다.
  const ASKED_KEY = "owcoach.events.asked.v1";
  const MAX_ASKED = 300;
  function firstAskToday(text) {
    const q = String(text || "").toLowerCase().replace(/[\s.,!?~·'"“”‘’()\[\]{}:;_\/\\-]+/g, "");
    if (!q) return true; // 질문 글을 모르면(옛 호출) 그대로 센다
    const k = dayKey(new Date());
    let d = null;
    try { d = JSON.parse(localStorage.getItem(ASKED_KEY) || "null"); } catch (e) { d = null; }
    if (!d || d.day !== k || !Array.isArray(d.h)) d = { day: k, h: [] };
    const id = hash(q);
    if (d.h.includes(id)) return false;
    d.h = d.h.concat(id).slice(-MAX_ASKED);
    write(ASKED_KEY, d);
    return true;
  }

  window.OwEvents = {
    chat(text) {
      if (!firstAskToday(text)) return false;
      // chat1103: 11월 3일부터 센 질문 수(오로라 조각 이벤트용, 0에서 시작). chat은 처음 세기 시작한 때부터의 총합이다.
      bump((d) => {
        d.chat += 1;
        if (Date.now() >= CHAT_SINCE) d.chat1103 += 1;
        if (Date.now() >= COUNT_SINCE) d.chat1104 += 1;
        if (Date.now() >= COUNT11_SINCE) d.chat11 += 1;
        const k = dayKey(new Date());
        d.days = trimDays(Object.assign({}, d.days, { [k]: ((d.days || {})[k] || 0) + 1 }));
      });
      return true;
    },
    // 카운터 카드 대결을 7라운드 끝까지(결과 화면까지) 마쳤을 때만 센다. 승리·전승(7라운드 모두 이김)도 따로 센다.
    cardGame(result) {
      const r = result || {};
      bump((d) => {
        d.cards += 1;
        if (r.won) d.cardWins += 1;
        if (r.perfect) d.cardPerfect += 1;
      });
    },
    scoreboard(match) {
      if (!match) return false;
      const fp = hash(fingerprint(match));
      if (load().shots.includes(fp)) return false;
      bump((d) => {
        if (!d.shots.includes(fp)) d.shots.push(fp);
        d.shots = d.shots.slice(-MAX_SHOTS);
        if (Date.now() >= COUNT11_SINCE && !(d.shots11 || []).includes(fp)) d.shots11 = (d.shots11 || []).concat(fp).slice(-MAX_SHOTS);
        if (Date.now() >= COUNT_SINCE && !(d.shots1104 || []).includes(fp)) d.shots1104 = (d.shots1104 || []).concat(fp).slice(-MAX_SHOTS);
      });
      return true;
    },
    selfCounts: loadSelf,
    // 동기화로 받은 연결 기기 합계를 쓴다(연결을 끊으면 이 기기 몫으로 되돌린다).
    setTotal(total) { write(KEY, total || loadSelf()); },
  };
})();
