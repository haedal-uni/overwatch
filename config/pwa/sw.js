// 홈 화면 설치용 서비스 워커: 아무것도 저장하지 않아 배포하면 바로 새 화면이 나온다.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate") return;
  e.respondWith(
    fetch(e.request).catch(() => new Response(
      '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
      '<body style="font-family:sans-serif;text-align:center;padding:30vh 24px;color:#16233b">인터넷 연결을 확인하고 다시 열어 주세요.</body>',
      { headers: { "Content-Type": "text/html; charset=utf-8" } }
    ))
  );
});
