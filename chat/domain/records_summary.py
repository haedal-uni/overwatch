"""브라우저 "내 기록"을 요약한 숫자로 내 플레이 스타일을 묻는 질문을 다루는 순수 파이썬 모듈."""

import re
from typing import Any, Dict, List, Optional

from chat.domain.heroes import resolve_hero_name

# 영웅 한 명의 스타일을 말하려면 필요한 최소 판 수. 그보다 적으면 숫자만 보여 주고 단정하지 않는다.
MIN_GAMES_FOR_STYLE = 3
MAX_HEROES = 5
# 같은 판 같은 역할 상대 평균 대비 몇 % 벗어나야 "많다/적다"로 보는지(실측값이 아니다).
STYLE_GAP_PCT = 25

_ROLE_LABELS = {"tank": "탱커", "damage": "딜러", "support": "힐러"}
_PEER_KEYS = ("damage", "healing", "mitigation", "kills", "deaths")

_RECORDS_SUBJECT_RE = re.compile(
    r"(?:내|나의|제|저의)\s*(?:전체\s*)?(?:판\s*)?"
    r"(?:기록|전적|스[탯텟]창?|플레이\s*스타일|스타일|성향|플레이|실력|장단점)"
)
_CUMULATIVE_RE = re.compile(r"(?:지금까지|그동안|여태|평소|누적|전체적|전반적|내\s*기록|내\s*전적|내\s*스타일|플레이\s*스타일)")


def _num(value: Any, lo: float = -1000.0, hi: float = 1_000_000.0) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 1) if lo <= value <= hi else None


def clean_records_summary(raw: Any) -> Optional[Dict[str, Any]]:
    """클라이언트가 보낸 요약을 숫자와 등록된 영웅 이름만 남기고 정리한다. 쓸 게 없으면 None."""
    if not isinstance(raw, dict):
        return None
    games = _num(raw.get("games"), 0, 1000)
    if not games:
        return None
    summary: Dict[str, Any] = {
        "games": int(games),
        "wins": int(_num(raw.get("wins"), 0, 1000) or 0),
        "losses": int(_num(raw.get("losses"), 0, 1000) or 0),
        "draws": int(_num(raw.get("draws"), 0, 1000) or 0),
        "avg_rank": _num(raw.get("avg_rank"), 1, 10),
        "heroes": [],
    }
    for item in (raw.get("heroes") or [])[:MAX_HEROES]:
        if not isinstance(item, dict):
            continue
        hero = resolve_hero_name(str(item.get("hero") or ""))
        count = _num(item.get("games"), 1, 1000)
        if not hero or not count:
            continue
        peers = item.get("vs_peers") if isinstance(item.get("vs_peers"), dict) else {}
        avg_kda = item.get("avg_kda") if isinstance(item.get("avg_kda"), dict) else {}
        summary["heroes"].append({
            "hero": hero,
            "role": item.get("role") if item.get("role") in _ROLE_LABELS else None,
            "games": int(count),
            "wins": int(_num(item.get("wins"), 0, 1000) or 0),
            "losses": int(_num(item.get("losses"), 0, 1000) or 0),
            "avg_rank": _num(item.get("avg_rank"), 1, 10),
            "avg_kda": {k: _num(avg_kda.get(k), 0, 1000) for k in ("kills", "deaths", "assists")},
            "vs_peers": {k: _num(peers.get(k), -100, 1000) for k in _PEER_KEYS},
        })
    return summary if summary["heroes"] else None


def is_records_question(message: str, has_current_match_stats: bool = False) -> bool:
    """"내 기록·스탯·플레이 스타일" 같은 내 누적 기록을 묻는 질문인지.

    방금 올린 판의 스탯이 세션에 있으면 그 판을 묻는 말일 수 있어, 누적을 가리키는 표현이 있을 때만 인정한다.
    """
    if not message or not _RECORDS_SUBJECT_RE.search(message):
        return False
    return not has_current_match_stats or bool(_CUMULATIVE_RE.search(message))


def _signed(pct: Optional[float]) -> str:
    return "-" if pct is None else f"{pct:+.0f}%"


def _hero_flags(hero: Dict[str, Any]) -> List[str]:
    """영웅 하나의 숫자에서 코드가 먼저 판정한 특징. 판 수가 모자라면 판정하지 않는다."""
    if hero["games"] < MIN_GAMES_FOR_STYLE:
        return [f"{hero['games']}판뿐이라 스타일을 판정하지 않는다"]
    peers = hero["vs_peers"]
    flags: List[str] = []
    dmg, kills, deaths, heal = peers.get("damage"), peers.get("kills"), peers.get("deaths"), peers.get("healing")
    if dmg is not None and kills is not None and dmg - kills >= STYLE_GAP_PCT:
        flags.append("피해량에 비해 처치가 적다(피해는 쌓지만 마무리가 적다)")
    elif dmg is not None and kills is not None and kills - dmg >= STYLE_GAP_PCT:
        flags.append("피해량에 비해 처치가 많다(마무리에 강하다)")
    if deaths is not None and deaths >= STYLE_GAP_PCT:
        flags.append("같은 판 같은 역할보다 데스가 많다(적극적으로 들어가 죽는 편)")
    elif deaths is not None and deaths <= -STYLE_GAP_PCT:
        low_output = [v for v in (dmg, heal) if v is not None and v <= -STYLE_GAP_PCT]
        flags.append(
            "데스가 적은데 피해·치유도 적다(소극적으로 물러나 있는 편)" if low_output
            else "데스가 적다(안정적으로 산다)"
        )
    wl = hero["wins"] + hero["losses"]
    if wl >= 5:
        rate = hero["wins"] / wl * 100
        flags.append(f"승률 {rate:.0f}%({hero['wins']}승 {hero['losses']}패)")
    elif wl:
        flags.append(f"승패 기록 {wl}판뿐이라 승률은 말하지 않는다")
    return flags or ["또래와 눈에 띄게 다른 점은 없다"]


def records_prompt_block(summary: Optional[Dict[str, Any]]) -> str:
    """답변 프롬프트에 넣는 기록 요약(숫자와 코드가 먼저 판정한 특징)."""
    if not summary:
        return ""
    wl = summary["wins"] + summary["losses"]
    head = f"총 {summary['games']}판"
    if wl:
        head += f", 승패 기록 {wl}판 중 {summary['wins']}승 {summary['losses']}패(승패는 사용자가 입력한 판만)"
    else:
        head += ", 승패 기록 없음"
    if summary.get("avg_rank"):
        head += f", 우리팀 안 평균 순위 {summary['avg_rank']}위"
    lines = [head]
    for hero in summary["heroes"]:
        role = _ROLE_LABELS.get(hero.get("role") or "", "")
        kda = hero["avg_kda"]
        peers = hero["vs_peers"]
        lines.append(
            f"- {hero['hero']}{f'({role})' if role else ''} {hero['games']}판 · 평균 K/D/A "
            f"{kda['kills']}/{kda['deaths']}/{kda['assists']} · 같은 판 같은 역할 평균 대비 "
            f"피해 {_signed(peers['damage'])}, 치유 {_signed(peers['healing'])}, 경감 {_signed(peers['mitigation'])}, "
            f"처치 {_signed(peers['kills'])}, 데스 {_signed(peers['deaths'])}"
        )
        lines.append("  판정: " + " / ".join(_hero_flags(hero)))
    return "\n".join(lines)
