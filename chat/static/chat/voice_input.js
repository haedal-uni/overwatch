// 음성 입력: 마이크 버튼을 누르고 말하면 입력창에 글이 채워진다(보내는 건 직접 한다).
// 브라우저의 음성 인식(Web Speech API)을 쓰며 HTTPS에서만 되고, 지원하지 않는 브라우저에서는 버튼을 만들지 않는다.
(function () {
  "use strict";

  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const input = document.getElementById("userInput");
  const upload = document.getElementById("uploadBtn");
  if (!Recognition || !window.isSecureContext || !input || !upload) return;

  const st = document.createElement("style");
  st.textContent = ".mic-btn.listening { background: #ff4f6e; border-color: #ff4f6e; color: #fff; animation: mic-pulse 1.1s ease-in-out infinite; }" +
    "@keyframes mic-pulse { 50% { box-shadow: 0 0 0 6px rgba(255, 79, 110, .25); } }";
  document.head.appendChild(st);

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "upload-btn mic-btn";
  btn.title = "음성으로 입력";
  btn.setAttribute("aria-label", "음성으로 입력");
  btn.innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';
  upload.before(btn);

  let rec = null;
  let base = "";

  function fill(text) {
    input.value = (base ? base + " " : "") + text;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dataset.source = "voice";
  }

  function stop() {
    btn.classList.remove("listening");
    rec = null;
  }

  btn.addEventListener("click", () => {
    if (rec) { rec.stop(); return; }
    rec = new Recognition();
    rec.lang = "ko-KR";
    rec.interimResults = true;
    rec.continuous = false;
    base = input.value.trim();
    rec.onresult = (e) => {
      let text = "";
      for (let i = 0; i < e.results.length; i += 1) text += e.results[i][0].transcript;
      fill(text.trim());
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") window.alert("마이크 사용을 허용해 주세요.");
    };
    rec.onend = stop;
    btn.classList.add("listening");
    try { rec.start(); } catch (err) { stop(); }
  });
})();
