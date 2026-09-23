// 표 컬럼 헤더에서 필터 값을 고르는 드롭다운(엑셀 자동 필터 형태).
// 사이드바 필터 링크를 그대로 재사용하는 순수 UI 레이어라 서버 쪽
// 필터링 로직은 건드리지 않는다.
(function () {
  "use strict";

  // 커스텀 표시 메서드를 쓰는 컬럼은 클래스명과 필터 파라미터명이 달라
  // 여기에 매핑을 추가해야 드롭다운이 뜬다.
  var EXTRA_COLUMN_TO_FILTER_KEY = {
    role_badge: "role",
    resolved_badge: "is_resolved",
  };

  // 날짜 필터는 단순 목록형이 아니라 사이드바에 남겨둔다.
  var SKIP_FILTER_KEYS = ["created_at"];

  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  // 링크 하나의 쿼리스트링을 {파라미터명: true} 형태로 파싱한다.
  function parseParamKeys(href) {
    var qs = (href || "").split("?")[1] || "";
    var keys = {};
    qs.split("&").forEach(function (pair) {
      if (!pair) return;
      var eqIndex = pair.indexOf("=");
      var key = eqIndex === -1 ? pair : pair.slice(0, eqIndex);
      if (key) keys[decodeURIComponent(key)] = true;
    });
    return keys;
  }

  // 필터 링크의 쿼리스트링에는 정렬·다른 필터까지 섞여 들어오므로, 링크
  // 하나의 첫 파라미터를 그 필터의 키로 볼 수 없다. 블록 안 옵션 링크들을
  // 비교해 "옵션마다 있거나 없는" 파라미터를 그 필터의 키로 판별한다.
  function filterKeyFromDetails(details) {
    var anchors = details.querySelectorAll("ul > li > a[href]");
    if (!anchors.length) return null;

    var keySets = [];
    anchors.forEach(function (a) {
      keySets.push(parseParamKeys(a.getAttribute("href")));
    });

    var counts = {};
    keySets.forEach(function (keys) {
      Object.keys(keys).forEach(function (k) {
        counts[k] = (counts[k] || 0) + 1;
      });
    });

    var total = keySets.length;
    for (var key in counts) {
      if (counts[key] < total) {
        return key.replace(/__exact$/, "").replace(/__isnull$/, "");
      }
    }
    return null;
  }

  function buildPopup(details) {
    var popup = document.createElement("div");
    popup.className = "col-filter-popup";

    var list = document.createElement("ul");
    details.querySelectorAll("ul > li").forEach(function (li) {
      var a = li.querySelector("a[href]");
      if (!a) return;
      var item = document.createElement("li");
      if (li.classList.contains("selected")) item.className = "selected";
      var link = document.createElement("a");
      link.href = a.getAttribute("href");
      link.textContent = a.textContent;
      item.appendChild(link);
      list.appendChild(item);
    });
    popup.appendChild(list);
    return popup;
  }

  function closeAllPopups(except) {
    document.querySelectorAll(".col-filter-popup.open").forEach(function (p) {
      if (p !== except) p.classList.remove("open");
    });
  }

  ready(function () {
    var sidebar = document.getElementById("changelist-filter");
    var table = document.querySelector("#changelist .results table, #result_list");
    if (!sidebar || !table) return;

    var detailsBlocks = sidebar.querySelectorAll("details[data-filter-title]");
    if (!detailsBlocks.length) return;

    var filtersByKey = {};
    detailsBlocks.forEach(function (details) {
      var key = filterKeyFromDetails(details);
      if (key && SKIP_FILTER_KEYS.indexOf(key) === -1) {
        filtersByKey[key] = details;
      }
    });

    // 컬럼 하나가 실패해도 나머지 컬럼 처리가 멈추지 않게 한다.
    var headerCells = table.querySelectorAll("thead th");
    headerCells.forEach(function (th) {
      try {
        buildColumnFilterToggle(th);
      } catch (err) {
        if (window.console && console.warn) {
          console.warn("[column_filter] 컬럼 필터 버튼 생성 실패:", th, err);
        }
      }
    });

    function buildColumnFilterToggle(th) {
      var columnClass = Array.prototype.find.call(th.classList, function (c) {
        return c.indexOf("column-") === 0;
      });
      if (!columnClass) return;
      var columnName = columnClass.slice("column-".length);
      var filterKey = EXTRA_COLUMN_TO_FILTER_KEY[columnName] || columnName;
      var details = filtersByKey[filterKey];
      if (!details) return;

      // 링크가 "?"인지가 아니라 이 필터의 파라미터가 실제로 들어 있는지로
      // 활성 여부를 판단한다(정렬이 걸리면 "전체" 링크도 "?"가 아니다).
      var activeLi = details.querySelector("li.selected");
      var activeAnchor = activeLi && activeLi.querySelector("a[href]");
      var isActive = false;
      if (activeAnchor) {
        var activeKeys = parseParamKeys(activeAnchor.getAttribute("href"));
        isActive =
          !!activeKeys[filterKey] ||
          !!activeKeys[filterKey + "__exact"] ||
          !!activeKeys[filterKey + "__isnull"];
      }

      var toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "col-filter-toggle" + (isActive ? " active" : "");
      toggle.setAttribute("aria-label", "필터");
      toggle.textContent = "▾";

      var popup = buildPopup(details);

      toggle.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        var willOpen = !popup.classList.contains("open");
        closeAllPopups(willOpen ? popup : null);
        popup.classList.toggle("open", willOpen);
      });

      th.classList.add("col-filter-th");
      th.appendChild(toggle);
      th.appendChild(popup);
    }

    document.addEventListener("click", function () {
      closeAllPopups(null);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeAllPopups(null);
    });
  });
})();
