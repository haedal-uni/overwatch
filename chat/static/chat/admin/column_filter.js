// Django admin의 사이드바 "필터" 패널(#changelist-filter) 대신, 표 컬럼
// 헤더를 클릭하면 그 컬럼에 해당하는 필터 값을 드롭다운 박스로 고를 수 있게
// 한다(엑셀 자동 필터와 비슷한 동작). 모바일에서 사이드바를 스크롤해서
// 찾는 게 불편하다는 피드백으로 추가했다.
//
// 서버 쪽 필터링 로직(list_filter, ?field=value 쿼리스트링)은 전혀 건드리지
// 않는다 — Django가 이미 렌더링해둔 사이드바 필터 링크를 그대로 재사용해서
// th 아래에 뜨는 팝업으로 옮겨 보여주기만 하는 순수 UI 레이어다. 그래서
// 사이드바가 없는 페이지(필터가 아예 없는 admin)에서는 아무 것도 하지 않는다.
(function () {
  "use strict";

  // list_display에 실제 필드명이 아니라 커스텀 표시 메서드(예: role_badge)가
  // 쓰이는 컬럼은 <th class="column-role_badge">처럼 필터 파라미터명과
  // 클래스명이 어긋난다. 이런 경우만 예외적으로 매핑해준다.
  var EXTRA_COLUMN_TO_FILTER_KEY = {
    role_badge: "role",
    resolved_badge: "is_resolved",
  };

  // 날짜 필터는 "오늘/지난 7일/이번 달/기간 선택(직접 입력 폼)"처럼 단순
  // 목록형 선택이 아니라서 컬럼 팝업으로 옮기지 않고 사이드바에 남겨둔다.
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

  // Django admin은 필터 링크의 쿼리스트링을 항상 "정렬(o)"·"다른 필터"까지
  // 포함해 알파벳순으로 합쳐서 만든다(get_query_string이 sorted(p.items())를
  // 씀). 그래서 링크 하나의 "첫 번째 파라미터"가 그 필터 자신의 파라미터라는
  // 보장이 없다 — 정렬이나 다른 필터가 함께 걸려 있으면 그게 알파벳상 앞설
  // 수 있다(예: "intent" < "o"). 예전엔 그 첫 파라미터를 그대로 필터 키로
  // 썼는데, 그러면 여러 필터 블록이 전부 같은(잘못된) 키로 뒤섞여
  // filtersByKey를 서로 덮어써서 필터 버튼 자체가 사라지는 문제가 있었다.
  // 대신 이 필터 블록 안의 옵션 링크들을 전부 비교해서, "모든 옵션에 공통으로
  // 있는" 배경 파라미터(정렬·다른 필터)를 제외하고 "옵션마다 있거나 없는"
  // 파라미터를 이 필터 자신의 파라미터로 판별한다("전체" 옵션은 이 파라미터가
  // 빠져 있고, 나머지 값 옵션들은 있으므로 항상 count < 전체 옵션 수가 된다).
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

    // th 하나 처리 중 예외가 나도(예상 못 한 DOM 구조 등) forEach 전체가
    // 중단되지 않게 컬럼마다 독립적으로 처리한다 — 안 그러면 컬럼 하나
    // 실패가 이후 모든 컬럼의 필터 버튼을 통째로 사라지게 만든다.
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

      // "선택됨" li의 링크가 문자 그대로 "?"인지로 활성 여부를 판단하면,
      // 정렬(o=...)이나 다른 필터가 함께 걸려 있을 때 "전체" 옵션의 링크도
      // "?o=2"처럼 물음표만이 아니게 돼 오작동한다 — 이 필터 자신의 파라미터
      // (filterKey)가 실제로 그 링크에 있는지로 판단해야 한다.
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
