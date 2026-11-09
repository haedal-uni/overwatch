"""홈 화면 아이콘(PNG)을 만든다: python config/pwa/make_icons.py (Chrome 필요).

icon_source.html은 달빛 옷장의 특별 친구 유니콘 루미를 그대로 그린 512px 장면이고,
크기마다 Chrome으로 다시 찍는다."""
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHROME = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "google-chrome", "chromium",
]


def find_browser():
    for c in CHROME:
        if Path(c).exists() or shutil.which(c):
            return c
    sys.exit("Chrome이나 Edge를 찾지 못했다.")


def shoot(size):
    out = HERE / f"icon-{size}.png"
    subprocess.run([
        find_browser(), "--headless", "--disable-gpu", "--hide-scrollbars",
        f"--screenshot={out}", f"--window-size={size},{size}", "--force-device-scale-factor=1",
        "--virtual-time-budget=2000", (HERE / "icon_source.html").as_uri() + f"#{size}",
    ], check=True)
    return out


if __name__ == "__main__":
    for n in (180, 192, 512):
        shoot(n)
