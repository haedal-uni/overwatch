// 스탯창 일괄 올리기. 이미지를 서버에 올려 두면 서버가 한 장씩 천천히 분석하고,
// 이 파일은 진행 상황을 주기적으로 확인해 끝난 판을 "내 기록"에 승패와 함께 넣는다.
// 작업 번호는 이 브라우저(localStorage)에만 둔다.
(function () {
  "use strict";

  const KEY = "owcoach.batch.v1";
  const MAX_FILES = 30;
  const POLL_MS = 6000;
  const RESULT_LABELS = { win: "이긴 판", loss: "진 판", draw: "무승부" };

  let timer = null;
  let bar = null;

  const csrf = () => {
    const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  };
  const load = () => {
    try {
      const v = JSON.parse(localStorage.getItem(KEY) || "[]");
      return Array.isArray(v) ? v : [];
    } catch (e) { return []; }
  };
  const store = (jobs) => { try { localStorage.setItem(KEY, JSON.stringify(jobs.slice(-5))); } catch (e) { /* 저장이 막힌 브라우저 */ } };

  function injectStyle() {
    if (document.getElementById("batchStyle")) return;
    const st = document.createElement("style");
    st.id = "batchStyle";
    st.textContent = `
      .bt-choice { display: flex; flex-direction: column; gap: 10px; margin-top: 4px; }
      .bt-choice button { text-align: left; padding: 14px 16px; border-radius: 14px; border: 1px solid var(--line-strong); background: var(--soft); color: var(--text); cursor: pointer; font: inherit; }
      .bt-choice button:hover { border-color: var(--primary); }
      .bt-choice b { display: block; font-size: 15px; margin-bottom: 2px; }
      .bt-choice span { font-size: 12.5px; color: var(--muted); line-height: 1.5; }
      .bt-results { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 6px 0 12px; }
      .bt-results button { padding: 14px 4px; border-radius: 12px; border: 2px solid var(--line-strong); background: var(--soft); color: var(--text); font: inherit; font-weight: 800; cursor: pointer; }
      .bt-results button.on { border-color: var(--primary); background: var(--primary-soft); color: var(--primary); }
      .bt-note { font-size: 12.5px; color: var(--muted); line-height: 1.6; margin-bottom: 14px; }
      .bt-bar { position: fixed; left: 50%; transform: translateX(-50%); bottom: calc(var(--sab, 0px) + 128px); z-index: 120; width: min(440px, calc(100vw - 24px)); padding: 12px 14px; border-radius: 16px; background: var(--card-solid); color: var(--text); border: 1px solid var(--line-strong); box-shadow: var(--shadow); font-size: 13.5px; line-height: 1.5; }
      .bt-bar .bt-top { display: flex; align-items: center; gap: 8px; justify-content: space-between; }
      .bt-bar .bt-title { font-weight: 800; }
      .bt-bar .bt-track { height: 6px; border-radius: 3px; background: var(--soft); overflow: hidden; margin: 8px 0 4px; }
      .bt-bar .bt-fill { height: 100%; background: var(--primary); transition: width .4s; }
      .bt-bar .bt-sub { color: var(--muted); font-size: 12.5px; }
      .bt-bar .bt-actions { display: flex; gap: 8px; margin-top: 8px; }
      .bt-bar .bt-actions button { height: 34px; padding: 0 14px; border-radius: 10px; border: 1px solid var(--line-strong); background: var(--primary-soft); color: var(--primary); font-weight: 800; font-size: 13px; cursor: pointer; }
      .bt-bar .bt-x { border: none; background: transparent; color: var(--muted); font-size: 18px; cursor: pointer; padding: 0 4px; }
    `;
    document.head.appendChild(st);
  }

  function modal(title, sub, bodyHtml) {
    const overlay = document.createElement("div");
    overlay.className = "save-modal-overlay";
    const box = document.createElement("div");
    box.className = "save-modal";
    box.innerHTML = '<div class="save-modal-header"><h3>' + title + '</h3><button type="button" class="save-modal-close" aria-label="닫기">×</button></div>' +
      (sub ? '<div class="save-modal-sub">' + sub + "</div>" : "") + bodyHtml;
    overlay.appendChild(box);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
    box.querySelector(".save-modal-close").addEventListener("click", () => overlay.remove());
    document.body.appendChild(overlay);
    return { overlay, box };
  }

  // 업로드 버튼을 누르면 하나만 분석할지 일괄로 올릴지 먼저 묻는다.
  function choose(handlers) {
    injectStyle();
    const { overlay, box } = modal("스탯창 올리기", "", '<div class="bt-choice">' +
      '<button type="button" data-mode="single"><b>하나만 분석하기</b><span>스탯창 한 장을 올리고 바로 분석 결과와 피드백을 봐요.</span></button>' +
      '<button type="button" data-mode="batch"><b>일괄 올리기</b><span>여러 장을 한 번에 올려 내 기록에 쌓아요. 이긴 판과 진 판을 따로 올리면 승패가 함께 기록돼요.</span></button></div>');
    box.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => {
      overlay.remove();
      if (b.dataset.mode === "single") handlers.single();
      else openBatch();
    }));
  }

  function openBatch() {
    injectStyle();
    let result = "";
    const { overlay, box } = modal("스탯창 일괄 올리기", "먼저 이번에 올릴 판의 결과를 골라 주세요.",
      '<div class="bt-results"><button type="button" data-r="win">이겼어요</button><button type="button" data-r="loss">졌어요</button><button type="button" data-r="draw">무승부</button></div>' +
      '<div class="bt-note">고른 결과가 올리는 <b>모든 이미지</b>에 적용돼요. 이긴 판과 진 판은 따로 올려 주세요.<br>' +
      '한 번에 ' + MAX_FILES + '장까지 올릴 수 있어요. 올린 뒤에는 서버가 한 장씩 천천히 분석하니 창을 닫아도 돼요. 끝나면 내 기록에 자동으로 들어가요.<br>' +
      '휴대폰으로 찍은 사진이 아니라 PC 화면 캡쳐(Tab 스탯창)만 분석돼요.</div>' +
      '<button type="button" class="save-modal-save-btn" id="btPick" disabled>이미지 고르기</button>' +
      '<input type="file" id="btFiles" accept="image/png,image/jpeg,image/webp" multiple hidden>');
    const pick = box.querySelector("#btPick");
    const files = box.querySelector("#btFiles");
    box.querySelectorAll("[data-r]").forEach((b) => b.addEventListener("click", () => {
      result = b.dataset.r;
      box.querySelectorAll("[data-r]").forEach((x) => x.classList.toggle("on", x === b));
      pick.disabled = false;
      pick.textContent = RESULT_LABELS[result] + "로 이미지 고르기";
    }));
    pick.addEventListener("click", () => files.click());
    files.addEventListener("change", async () => {
      const list = Array.from(files.files || []);
      if (!list.length) return;
      if (list.length > MAX_FILES) { alert("한 번에 " + MAX_FILES + "장까지 올릴 수 있어요."); files.value = ""; return; }
      pick.disabled = true;
      pick.textContent = list.length + "장 올리는 중...";
      try {
        await upload(result, list);
        overlay.remove();
      } catch (err) {
        pick.disabled = false;
        pick.textContent = RESULT_LABELS[result] + "로 이미지 고르기";
        files.value = "";
        alert(err.message || "올리지 못했어요. 잠시 뒤에 다시 해 주세요.");
      }
    });
  }

  async function upload(result, list) {
    const form = new FormData();
    form.append("result", result);
    list.forEach((f) => form.append("images", f, f.name || "scoreboard.png"));
    const res = await fetch("/chat/api/scoreboard/batch/", { method: "POST", headers: { "X-CSRFToken": csrf() }, body: form });
    let data = {};
    try { data = await res.json(); } catch (e) { /* 본문이 JSON이 아닌 경우 */ }
    if (!res.ok || !data.job_id) throw new Error(data.error || "올리지 못했어요. 잠시 뒤에 다시 해 주세요.");
    const jobs = load();
    jobs.push({ id: data.job_id, result, total: data.total, saved: [], done: 0, failed: 0, ts: Date.now() });
    store(jobs);
    start();
  }

  // 끝난 판을 내 기록에 넣는다(같은 판은 id가 같아 두 번 들어가지 않는다).
  function collect(job, data) {
    let added = false;
    (data.images || []).forEach((img) => {
      if (img.status !== "done" || !img.match || job.saved.includes(img.i)) return;
      if (window.MatchRecords && window.MatchRecords.save) window.MatchRecords.save(img.match, "batch-" + job.id + "-" + img.i, job.result);
      job.saved.push(img.i);
      added = true;
    });
    return added;
  }

  async function poll() {
    const jobs = load();
    let open = false;
    for (const job of jobs) {
      if (job.dismissed) continue;
      try {
        const res = await fetch("/chat/api/scoreboard/batch/" + job.id + "/");
        if (res.status === 404) { job.gone = true; continue; }
        if (!res.ok) { open = true; continue; }
        const data = await res.json();
        collect(job, data);
        job.done = data.done;
        job.failed = data.failed;
        job.total = data.total;
        job.errors = (data.images || []).filter((i) => i.status === "failed").map((i) => i.error).filter(Boolean);
        if (data.done + data.failed < data.total) open = true;
      } catch (e) { open = true; }
    }
    store(jobs.filter((j) => !j.gone));
    render();
    if (!open) stop();
  }

  function start() {
    if (!timer) timer = setInterval(poll, POLL_MS);
    poll();
  }
  function stop() { if (timer) { clearInterval(timer); timer = null; } }

  function dismiss(id) {
    store(load().filter((j) => j.id !== id));
    render();
  }

  function render() {
    injectStyle();
    const jobs = load().filter((j) => !j.dismissed);
    if (!jobs.length) { if (bar) { bar.remove(); bar = null; } return; }
    const job = jobs[jobs.length - 1];
    const finished = (job.done || 0) + (job.failed || 0) >= job.total;
    if (!bar) { bar = document.createElement("div"); bar.className = "bt-bar"; document.body.appendChild(bar); }
    const pct = job.total ? Math.round((((job.done || 0) + (job.failed || 0)) / job.total) * 100) : 0;
    let html = '<div class="bt-top"><span class="bt-title">' + (finished ? "일괄 분석을 마쳤어요" : "일괄 분석 중") + " · " + RESULT_LABELS[job.result] +
      '</span>' + (finished ? '<button type="button" class="bt-x" data-bt-close aria-label="닫기">×</button>' : "") + "</div>";
    if (finished) {
      html += '<div class="bt-sub">' + job.total + "장 중 " + (job.done || 0) + "장을 내 기록에 넣었어요" + (job.failed ? " · " + job.failed + "장은 분석하지 못했어요" + (job.errors && job.errors[0] ? " (" + job.errors[0] + ")" : "") : "") + "</div>" +
        '<div class="bt-actions"><button type="button" data-bt-records>내 기록 보기</button></div>';
    } else {
      html += '<div class="bt-track"><div class="bt-fill" style="width:' + pct + '%"></div></div><div class="bt-sub">' + ((job.done || 0) + (job.failed || 0)) + " / " + job.total + "장 분석됨 · 창을 닫아도 서버가 계속 분석해요</div>";
    }
    bar.innerHTML = html;
    const closeBtn = bar.querySelector("[data-bt-close]");
    if (closeBtn) closeBtn.addEventListener("click", () => dismiss(job.id));
    const rec = bar.querySelector("[data-bt-records]");
    if (rec) rec.addEventListener("click", () => { dismiss(job.id); if (window.MatchRecords) window.MatchRecords.openRecords(); });
  }

  // 들어올 때 끝나지 않은 작업이 있으면 이어서 확인한다.
  function resume() {
    if (!load().length) return;
    render();
    start();
  }

  window.OwBatch = { choose, resume };
})();
