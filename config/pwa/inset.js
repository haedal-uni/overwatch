// 아래 여백(--sab): 설치한 앱(PWA)이 안드로이드에서 화면을 시스템 내비게이션 막대 뒤까지 그리는데
// env(safe-area-inset-bottom)이 0으로 나와 아래 탭·입력창이 막대에 가려진다. 이때 막대 높이만큼 비운다.
// - 자동: env가 있으면 env, 없고 안드로이드에서 앱 모드이거나(display-mode) 브라우저 주소창이 없는 높이면 48px.
// - 수동: 홈 화면의 "화면 맞춤"에서 고른 값(localStorage ow.sab)이 있으면 그 값을 쓴다.
(function () {
  "use strict";

  const root = document.documentElement;
  const KEY = "ow.sab";

  function envBottom() {
    const probe = document.createElement("div");
    probe.style.cssText = "position:fixed;left:0;bottom:0;width:0;height:0;padding-bottom:env(safe-area-inset-bottom,0px);visibility:hidden";
    root.appendChild(probe);
    const px = parseFloat(getComputedStyle(probe).paddingBottom) || 0;
    probe.remove();
    return px;
  }

  function mode() {
    for (const m of ["fullscreen", "standalone", "minimal-ui"]) {
      if (window.matchMedia("(display-mode: " + m + ")").matches) return m;
    }
    return window.navigator.standalone === true ? "standalone" : "browser";
  }

  function manual() {
    try {
      const v = localStorage.getItem(KEY);
      return v === null ? null : Number(v);
    } catch (e) {
      return null;
    }
  }

  function info() {
    const env = envBottom();
    const gap = Math.round(window.screen.height - window.innerHeight);
    const android = /Android/i.test(navigator.userAgent);
    const m = mode();
    const auto = env >= 1 ? env : android && (m !== "browser" || gap < 56) ? 48 : 0;
    const fixed = manual();
    return { mode: m, android, env, gap, inner: window.innerHeight, screen: window.screen.height, dpr: window.devicePixelRatio, auto, manual: fixed, sab: fixed !== null ? fixed : auto };
  }

  function apply() {
    root.style.setProperty("--sab", info().sab + "px");
  }

  function set(px) {
    try {
      if (px === null) localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, String(Math.max(0, Math.min(160, Math.round(px)))));
    } catch (e) { /* 저장이 막힌 브라우저 */ }
    apply();
  }

  // 예전 방식(?sab=숫자)도 받아 준다.
  try {
    const q = new URLSearchParams(location.search).get("sab");
    if (q === "auto") set(null);
    else if (q !== null && /^\d{1,3}$/.test(q)) set(Number(q));
  } catch (e) { /* 무시 */ }

  apply();
  window.addEventListener("resize", apply);
  window.addEventListener("orientationchange", () => setTimeout(apply, 200));
  window.OwInset = { info, apply, set };
})();
