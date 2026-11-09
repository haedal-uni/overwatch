// PC·폰 기록 연결: 6자리 코드로 기기를 묶고 내 기록·이벤트 횟수·저장한 대화를 서버를 거쳐 합친다.
(function () {
  "use strict";

  const DEVICE_KEY = "owcoach.device.v1";
  const STATE_KEY = "owcoach.sync.v1";
  const RECORDS_KEY = "owcoach.records.v1";
  const SAVED_KEY = "owcoach_saved_chats";
  const SAVED_TS_KEY = "owcoach_saved_chats_ts";

  const get = (k, d) => { try { const v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; } catch (e) { return d; } };
  const put = (k, v) => { try { localStorage.setItem(k, typeof v === "string" ? v : JSON.stringify(v)); } catch (e) { /* 저장이 막힌 브라우저 */ } };

  function deviceId() {
    let id = get(DEVICE_KEY, "");
    if (!id) {
      id = window.crypto && crypto.randomUUID ? crypto.randomUUID() : "d-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 14);
      put(DEVICE_KEY, JSON.stringify(id));
    }
    return id;
  }
  const state = () => get(STATE_KEY, { linked: false, devices: [] });
  const setState = (s) => put(STATE_KEY, s);

  function csrf() {
    const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  }
  async function post(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(Object.assign({ device: deviceId() }, body || {})),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "연결에 실패했어요.");
    return data;
  }

  let running = null;
  async function syncNow() {
    if (running) return running;
    running = (async () => {
      try {
        // 이 기기 몫이 아직 없으면 합계로 덮어쓰기 전에 지금 값을 이 기기 몫으로 남긴다(처음 연결한 기기의 기존 횟수).
        if (get("owcoach.events.self.v1", null) == null) put("owcoach.events.self.v1", get("owcoach.events.v1", { days: {}, chat: 0, chat1103: 0, chat1104: 0, chat11: 0, shots11: [], shots1104: [], cards: 0, cardWins: 0, cardPerfect: 0, shots: [] }));
        const self = get("owcoach.events.self.v1", { days: {}, chat: 0, chat1103: 0, chat1104: 0, chat11: 0, shots11: [], shots1104: [], cards: 0, cardWins: 0, cardPerfect: 0, shots: [] });
        const data = await post("/chat/api/sync/", {
          records: get(RECORDS_KEY, []),
          saved_chats: get(SAVED_KEY, []),
          saved_chats_ts: Number(get(SAVED_TS_KEY, 0)) || 0,
          events_self: self,
        });
        if (!data.linked) {
          setState({ linked: false, devices: [] });
          return data;
        }
        put(RECORDS_KEY, data.records || []);
        if ((data.saved_chats_ts || 0) > (Number(get(SAVED_TS_KEY, 0)) || 0)) {
          put(SAVED_KEY, data.saved_chats || []);
          put(SAVED_TS_KEY, String(data.saved_chats_ts));
        }
        if (data.events_total) put("owcoach.events.v1", data.events_total);
        setState({ linked: true, devices: data.devices || [], at: Date.now() });
        return data;
      } catch (e) {
        return null;
      } finally {
        running = null;
        window.dispatchEvent(new Event("owsync:done"));
      }
    })();
    return running;
  }

  let timer = null;
  function dirty() {
    if (!state().linked) return;
    clearTimeout(timer);
    timer = setTimeout(syncNow, 1500);
  }

  // 연결된 기기면 들어올 때·화면으로 돌아올 때·1분마다 맞춘다.
  if (state().linked) syncNow();
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && state().linked) syncNow(); });
  setInterval(() => { if (document.visibilityState === "visible" && state().linked) syncNow(); }, 60000);

  // ---------- 연결 화면 ----------
  const CSS = `
  .sy-back { position: fixed; inset: 0; z-index: 1000; display: flex; align-items: center; justify-content: center; padding: 16px; background: rgba(15, 20, 35, 0.55); }
  .sy-card { width: min(380px, 100%); max-height: calc(100dvh - 32px); overflow-y: auto; box-sizing: border-box; padding: 20px 18px; border-radius: 20px; background: var(--surface, #fff); color: var(--text, #1f2433); box-shadow: 0 20px 50px rgba(0, 0, 0, 0.25); font-size: 14px; line-height: 1.55; word-break: keep-all; }
  .sy-card h2 { margin: 0 0 6px; font-size: 18px; }
  .sy-card p { margin: 0 0 12px; color: var(--muted, #6b7285); }
  .sy-code { margin: 6px 0 4px; font-size: 38px; font-weight: 800; letter-spacing: 0.18em; text-align: center; font-variant-numeric: tabular-nums; }
  .sy-sub { text-align: center; color: var(--muted, #6b7285); font-size: 13px; margin-bottom: 12px; }
  .sy-row { display: flex; gap: 8px; margin-top: 10px; }
  .sy-row input { flex: 1; min-width: 0; height: 44px; padding: 0 12px; border: 1px solid var(--line, #d9dce8); border-radius: 12px; background: transparent; color: inherit; font-size: 20px; letter-spacing: 0.15em; text-align: center; }
  .sy-btn { height: 44px; padding: 0 14px; border: 0; border-radius: 12px; background: #2f6df6; color: #fff; font: inherit; font-weight: 700; cursor: pointer; }
  .sy-btn.gray { background: var(--line, #e8eaf2); color: inherit; }
  .sy-btn.red { background: #fde2e2; color: #b42318; }
  .sy-btn.full { width: 100%; margin-top: 8px; }
  .sy-note { margin: 10px 0 0; font-size: 12px; text-align: center; }
  .sy-sep { margin: 16px 0 10px; border-top: 1px solid var(--line, #e8eaf2); }
  .sy-msg { min-height: 20px; margin-top: 8px; font-weight: 700; }
  .sy-msg.err { color: #d14343; }
  .sy-dev { display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0 10px; }
  .sy-dev span { padding: 4px 10px; border-radius: 99px; background: var(--line, #eef1f8); font-size: 13px; }
  `;

  let pollTimer = null;
  function close(back) {
    clearInterval(pollTimer);
    back.remove();
  }

  function open() {
    if (!document.getElementById("sy-style")) {
      const st = document.createElement("style");
      st.id = "sy-style";
      st.textContent = CSS;
      document.head.appendChild(st);
    }
    document.querySelector(".sy-back")?.remove();
    const back = document.createElement("div");
    back.className = "sy-back";
    back.innerHTML = '<div class="sy-card" role="dialog" aria-label="PC·폰 기록 연결"></div>';
    document.body.appendChild(back);
    back.addEventListener("click", (e) => { if (e.target === back || e.target.closest("[data-sy-close]")) close(back); });
    render(back);
  }

  function render(back) {
    const card = back.querySelector(".sy-card");
    const s = state();
    const closeBtn = '<button class="sy-btn gray full" data-sy-close>닫기</button>';
    if (s.linked && (s.devices || []).length > 1) {
      const devs = s.devices.map((d) => `<span>${d.label}${d.me ? " (이 기기)" : ""}</span>`).join("");
      card.innerHTML =
        `<h2>기기 ${s.devices.length}대가 연결돼 있어요</h2>` +
        `<p>내 기록과 이벤트 횟수를 함께 써요.</p><div class="sy-dev">${devs}</div>` +
        '<button class="sy-btn gray full" data-sy="code">다른 기기 더 연결하기</button>' +
        '<button class="sy-btn gray full" data-sy="unlink">이 기기 연결 끊기</button>' +
        '<button class="sy-btn red full" data-sy="wipe">모든 기기 연결 끊고 기록 지우기</button>' +
        '<div class="sy-msg"></div><div class="sy-sep"></div>' + closeBtn;
    } else {
      card.innerHTML =
        "<h2>PC·폰 기록 연결</h2>" +
        "<p>두 기기에서 같은 기록을 볼 수 있어요.</p>" +
        "<b>① 지금 쓰는 기기에서 코드를 받으세요</b>" +
        '<button class="sy-btn full" data-sy="code">코드 받기</button>' +
        '<div class="sy-sep"></div><b>② 연결할 기기에서 코드를 입력해 주세요</b>' +
        '<div class="sy-row"><input inputmode="numeric" maxlength="6" placeholder="000000" aria-label="6자리 코드"><button class="sy-btn" data-sy="link">연결</button></div>' +
        '<div class="sy-msg"></div><p class="sy-note">3개월 동안 쓰지 않으면 연결이 끊기고 기록이 지워져요.</p>' + closeBtn;
    }
    const msg = card.querySelector(".sy-msg");
    const say = (text, err) => { msg.textContent = text; msg.classList.toggle("err", !!err); };
    card.querySelectorAll("[data-sy]").forEach((btn) => btn.addEventListener("click", async () => {
      const act = btn.dataset.sy;
      btn.disabled = true;
      try {
        if (act === "code") {
          const data = await post("/chat/api/sync/code/");
          setState({ linked: true, devices: state().devices || [] });
          showCode(back, data.code, data.minutes);
          return;
        }
        if (act === "link") {
          const code = (card.querySelector("input").value || "").replace(/\D/g, "");
          if (code.length !== 6) { say("6자리 숫자를 입력해 주세요.", true); return; }
          await post("/chat/api/sync/link/", { code });
          setState({ linked: true, devices: [] });
          await syncNow();
          say("연결됐어요!");
          setTimeout(() => render(back), 900);
          return;
        }
        if (act === "unlink" || act === "wipe") {
          if (act === "wipe" && !confirm("모든 기기의 연결을 끊고 연결에 저장된 기록을 지울까요? 각 기기에 있는 기록은 그대로 남아요.")) return;
          await post("/chat/api/sync/unlink/", { wipe: act === "wipe" });
          setState({ linked: false, devices: [] });
          if (window.OwEvents) window.OwEvents.setTotal(null);
          render(back);
        }
      } catch (e) {
        say(e.message || "연결에 실패했어요.", true);
      } finally {
        btn.disabled = false;
      }
    }));
  }

  function showCode(back, code, minutes) {
    const card = back.querySelector(".sy-card");
    const until = Date.now() + (minutes || 10) * 60000;
    card.innerHTML =
      "<h2>연결할 기기에서 이 코드를 입력해 주세요</h2>" +
      `<div class="sy-code">${code}</div><div class="sy-sub"></div>` +
      '<button class="sy-btn gray full" data-sy-close>닫기</button>';
    const sub = card.querySelector(".sy-sub");
    const tick = async (checkLink) => {
      const left = Math.max(0, Math.round((until - Date.now()) / 1000));
      sub.textContent = left ? `${Math.floor(left / 60)}분 ${String(left % 60).padStart(2, "0")}초 남음` : "시간이 지났어요. 다시 받아 주세요.";
      if (!left) { clearInterval(pollTimer); return; }
      if (checkLink) {
        const data = await syncNow();
        if (data && data.linked && (data.devices || []).length > 1) {
          clearInterval(pollTimer);
          sub.textContent = "연결됐어요!";
          setTimeout(() => render(back), 900);
        }
      }
    };
    let n = 0;
    clearInterval(pollTimer);
    tick(false);
    pollTimer = setInterval(() => { n += 1; tick(n % 3 === 0); }, 1000);
  }

  window.OwSync = { open, syncNow, dirty, deviceId, isLinked: () => !!state().linked };
})();
