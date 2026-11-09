// 이 브라우저의 기기 ID 하나를 챗봇·게임·홈이 함께 쓴다. 홈(/)이나 챗봇이나 게임 중 어디든 처음 여는 곳에서 한 번 만든다.
// 이미 게임 기기 ID(game.player)가 있으면 그 값을 이어받고(선물·이용 기록이 끊기지 않게), 없으면 예전 챗봇 기기 ID를, 그것도 없으면 새로 만든다.
(function () {
  var RE = /^[A-Za-z0-9_-]{8,64}$/;
  function read(key) { try { return localStorage.getItem(key) || ""; } catch (e) { return ""; } }
  function gameId() { try { var p = JSON.parse(read("game.player") || "{}"); return (p && p.id) || ""; } catch (e) { return ""; } }
  function fresh() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "d-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
  }
  var cached = "";
  function id() {
    if (cached) return cached;
    var v = read("ow.device");
    if (!RE.test(v)) v = gameId();
    if (!RE.test(v)) v = read("owcoach.device.v1");
    if (!RE.test(v)) v = fresh();
    try { localStorage.setItem("ow.device", v); } catch (e) { /* 저장이 막혀도 이번 접속 동안은 같은 값을 쓴다 */ }
    cached = v;
    return v;
  }
  window.OwDevice = { id: id };
  id();
})();
