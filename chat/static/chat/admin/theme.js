// 관리자 테마 보조: 폰 카드 목록에 칸 이름을 붙이고, 필터를 단추로 여닫는다.
(function () {
  "use strict";

  // 폰 카드에서 줄 전체를 쓰는 칸과 숨기는 칸(필드 이름).
  const WIDE = ["message_preview", "short_message", "feedback_reason_preview", "enemy_team_display", "message"];
  const HIDE_ON_PHONE = ["turn_link"];

  function labelCells() {
    const table = document.getElementById("result_list");
    if (!table) return;
    const heads = [...table.querySelectorAll("thead th")].map((th) => (th.querySelector(".text") || th).textContent.trim());
    table.querySelectorAll("tbody tr").forEach((tr) => {
      [...tr.children].forEach((cell, i) => {
        if (cell.tagName !== "TD" || cell.classList.contains("action-checkbox")) return;
        const name = [...cell.classList].find((c) => c.startsWith("field-"));
        const field = name ? name.slice(6) : "";
        if (heads[i]) cell.dataset.label = heads[i];
        if (WIDE.includes(field)) cell.classList.add("ad-wide");
        if (HIDE_ON_PHONE.includes(field)) cell.classList.add("ad-hide-m");
        const text = cell.textContent.trim();
        const hasControl = !!cell.querySelector("input, select, textarea, button");
        if (!hasControl && (!text || text === "-")) cell.classList.add("ad-hide-m");
      });
    });
  }

  function filterToggle() {
    const box = document.getElementById("changelist");
    const filter = document.getElementById("changelist-filter");
    if (!box || !filter) return;
    const active = [...new URLSearchParams(location.search).keys()].filter((k) => !["o", "p", "q", "e", "all"].includes(k)).length;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ad-filter-btn";
    btn.innerHTML = "필터" + (active > 0 ? ` <span class="n">${active}</span>` : "");
    btn.addEventListener("click", () => box.classList.toggle("ad-filter-open"));
    box.parentNode.insertBefore(btn, box);
  }

  // 목록 줄 아무 데나 누르면 그 로그의 상세 화면으로 간다(날짜 글자만 링크라서 누르기 어려웠다). 체크칸·링크·입력칸은 그대로 동작한다.
  function clickableRows() {
    const table = document.getElementById("result_list");
    if (!table) return;
    table.querySelectorAll("tbody tr").forEach((tr) => {
      const link = tr.querySelector("th a, td a");
      if (!link) return;
      tr.classList.add("ad-row-link");
      tr.addEventListener("click", (e) => {
        if (e.target.closest("a, button, input, select, textarea, label, .action-checkbox")) return;
        if (window.getSelection && String(window.getSelection())) return;
        window.location.href = link.href;
      });
    });
  }

  // 게임 의견: 반영한 줄은 흐리게 초록, 아직 안 한 줄은 주황 띠로 구분한다.
  function feedbackRows() {
    const table = document.getElementById("result_list");
    if (!table) return;
    table.querySelectorAll("tbody tr").forEach((tr) => {
      const box = tr.querySelector("td.field-is_resolved input[type=checkbox]");
      if (!box) return;
      const apply = () => { tr.classList.toggle("ad-done", box.checked); tr.classList.toggle("ad-open", !box.checked); };
      box.addEventListener("change", apply);
      apply();
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    clickableRows();
    feedbackRows();
    labelCells();
    filterToggle();
  });
})();
