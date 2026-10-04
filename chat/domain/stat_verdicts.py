"""스탯 강점/약점 판정과 유형별 종합 점수(LLM 호출 없음).

유형 분류·비중·계산 방식의 배경은 chat_모듈_구조.md "domain/stat_verdicts.py" 절.
"""

from typing import Any, Dict, Iterable, List, Optional

from chat.domain.heroes import HERO_TO_ROLE, ROLE_LABELS, normalize_hero_name

METRICS = ["kills", "assists", "deaths", "damage", "healing", "mitigation"]
METRIC_LABELS = {
    "kills": "처치", "assists": "도움", "deaths": "데스",
    "damage": "피해량", "healing": "치유량", "mitigation": "경감량",
}

# 유형별 라벨·역할·지표 비중(행마다 합 100).
STAT_TYPES: Dict[str, Dict[str, Any]] = {
    "absorb_tank": {
        "label": "경감형 탱커", "role": "tank",
        "weights": {"kills": 25, "damage": 25, "mitigation": 20, "deaths": 30},
    },
    "plain_tank": {
        "label": "비경감형 탱커", "role": "tank",
        "weights": {"kills": 35, "damage": 35, "deaths": 30},
    },
    "main_damage": {
        "label": "메인 딜러", "role": "damage",
        "weights": {"kills": 40, "damage": 35, "deaths": 25},
    },
    "sub_damage": {
        "label": "서브 딜러", "role": "damage",
        "weights": {"kills": 35, "assists": 15, "damage": 25, "deaths": 25},
    },
    "main_support": {
        "label": "메인 힐러", "role": "support",
        "weights": {"kills": 5, "assists": 20, "damage": 10, "healing": 40, "deaths": 25},
    },
    "sub_support": {
        "label": "서브 힐러", "role": "support",
        "weights": {"kills": 15, "assists": 15, "damage": 25, "healing": 25, "deaths": 20},
    },
}

# 문서에 분류가 없는 영웅의 역할별 기본 유형.
DEFAULT_TYPE_BY_ROLE = {"tank": "plain_tank", "damage": "main_damage", "support": "main_support"}

# 이 비중 이하인 지표는 강점일 때만 반영한다.
BONUS_ONLY_WEIGHT = 10

# 강점/약점 임계값(비교 대상 평균 대비).
STRENGTH_RATIO = 1.25
WEAKNESS_RATIO = 0.7
DEATH_WEAKNESS_RATIO = 1.4

# 강점/약점으로 보려면 평균과 최소 이만큼은 차이가 나야 한다.
MIN_GAP = {
    "kills": 3, "assists": 3, "deaths": 2,
    "damage": 1500, "healing": 1500, "mitigation": 1500,
}

# 종합 점수에서 우리팀 내부 비교의 몫(나머지는 상대 같은 포지션 비교).
TEAM_SCORE_SHARE = 0.5
# 같은 포지션 팀원이 없을 때 팀 전체와 비교해도 되는, 역할과 무관한 지표.
ROLE_NEUTRAL_METRICS = ("kills", "deaths")

# 힐할 기회 보정: 받은 피해 대비 치유 비율이 상대의 이 비율 이상이면 받은 피해가 적었던 것.
HEALING_OPPORTUNITY_RATIO = 0.85
# "크게 많음" = 이 배수 이상이면서 이 횟수 이상 차이.
MUCH_MORE_DEATH_RATIO = 1.4
MUCH_MORE_DEATH_GAP = 3
# 팀 상황은 피해량이 확인된 영웅이 양 팀 모두 이만큼 있을 때만 계산한다.
MIN_HEROES_FOR_TEAM_CONTEXT = 3

TEAM_LABELS = {"my": "우리팀", "enemy": "상대팀"}


def hero_stat_type(hero: str) -> Optional[str]:
    """영웅의 스탯 판정 유형 키. 모르는 영웅이면 None."""
    from chat.rag.doc_sections import get_hero_stat_types

    name = normalize_hero_name(hero)
    stat_type = get_hero_stat_types().get(name)
    if stat_type:
        return stat_type
    role = HERO_TO_ROLE.get(name)
    return DEFAULT_TYPE_BY_ROLE.get(role) if role else None


def make_entry(
    hero: str, team: str, stats: Dict[str, Any], *, is_me: bool = False, role: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """판정 단위 하나. 영웅을 모르면 역할(role) 기본 유형으로, 역할도 모르면 None."""
    name = normalize_hero_name(hero) if hero and hero != "unknown" else hero
    stat_type = hero_stat_type(name) if name and name != "unknown" else None
    if not stat_type:
        role = role or stats.get("role")
        stat_type = DEFAULT_TYPE_BY_ROLE.get(role)
        if not stat_type:
            return None
        if not name or name == "unknown":
            name = f"미확인 {ROLE_LABELS[role]}"
    values = {}
    for metric in METRICS:
        value = stats.get(metric)
        values[metric] = value if isinstance(value, (int, float)) else None
    return {
        "hero": name, "team": team, "is_me": is_me,
        "type": stat_type, "role": STAT_TYPES[stat_type]["role"], "stats": values,
    }


def entries_from_stat_dicts(
    my_team_stats: Optional[Dict[str, Dict[str, Any]]],
    enemy_stats: Optional[Dict[str, Dict[str, Any]]] = None,
    my_stats: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """채팅 세션의 스탯 dict({영웅: {kills, deaths, ...}})를 판정 단위로."""
    my_team_stats = my_team_stats or {}
    my_stats = my_stats or {}
    me = {normalize_hero_name(h) for h in my_stats}
    ally_source = dict(my_team_stats)
    for hero, stats in my_stats.items():
        ally_source.setdefault(hero, stats)

    entries = []
    for hero, stats in ally_source.items():
        entry = make_entry(hero, "my", stats or {}, is_me=normalize_hero_name(hero) in me)
        if entry:
            entries.append(entry)
    for hero, stats in (enemy_stats or {}).items():
        entry = make_entry(hero, "enemy", stats or {})
        if entry:
            entries.append(entry)
    return entries


def entries_from_scoreboard_rows(
    my_team: Iterable[Dict[str, Any]], enemy_team: Iterable[Dict[str, Any]] = (),
) -> List[Dict[str, Any]]:
    """스탯창 행(hero, kda, damage, healing, mitigation, is_me)을 판정 단위로."""
    entries = []
    for team, rows in (("my", my_team), ("enemy", enemy_team)):
        unknown_counts: Dict[str, int] = {}
        for row in rows:
            hero = row.get("hero")
            role = row.get("role_code")
            if hero in (None, "unknown") and role in ROLE_LABELS:
                # 채팅 쪽 스탯 dict(_build_stat_dict)와 같은 이름을 쓴다.
                unknown_counts[role] = unknown_counts.get(role, 0) + 1
                hero = f"미확인 {ROLE_LABELS[role]}{unknown_counts[role]}"
            kda = row.get("kda") or {}
            stats = {
                "kills": kda.get("kill"), "deaths": kda.get("death"), "assists": kda.get("assist"),
                "damage": row.get("damage"), "healing": row.get("healing"),
                "mitigation": row.get("mitigation"),
            }
            entry = make_entry(hero, team, stats, is_me=row.get("is_me") is True, role=role)
            if entry:
                entries.append(entry)
    return entries


def _mean(values: List[float]) -> float:
    return sum(values) / len(values)


def _team_sum(entries, team: str, metric: str, roles=None) -> Optional[float]:
    values = [
        e["stats"][metric] for e in entries
        if e["team"] == team and e["stats"][metric] is not None
        and (roles is None or e["role"] in roles)
    ]
    return sum(values) if values else None


def team_context(entries: List[Dict[str, Any]]) -> Optional[Dict[str, Dict[str, Any]]]:
    """양 팀의 받은 피해(상대 총 피해량으로 어림)·힐러진 치유·역할 묶음별 데스."""
    for team in ("my", "enemy"):
        known = [e for e in entries if e["team"] == team and e["stats"]["damage"] is not None]
        if len(known) < MIN_HEROES_FOR_TEAM_CONTEXT:
            return None

    context = {}
    for team, other in (("my", "enemy"), ("enemy", "my")):
        received = _team_sum(entries, other, "damage") or 0
        healing = _team_sum(entries, team, "healing", roles={"support"})
        context[team] = {
            "received_damage": received,
            "support_healing": healing,
            "heal_ratio": (healing / received) if (healing is not None and received > 0) else None,
            "support_deaths": _team_sum(entries, team, "deaths", roles={"support"}),
            "frontline_deaths": _team_sum(entries, team, "deaths", roles={"tank", "damage"}),
        }
    return context


def _much_more(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    return a >= b * MUCH_MORE_DEATH_RATIO and a - b >= MUCH_MORE_DEATH_GAP


def _healing_shortfall_reason(
    entry: Dict[str, Any], death_verdict: Optional[str], context: Optional[Dict[str, Dict[str, Any]]],
) -> Optional[str]:
    """힐러 치유량이 낮게 나온 원인이 힐할 기회 쪽에 있으면 그 사유."""
    if death_verdict == "약점":
        return "본인 데스가 많아 치유할 시간이 줄었다 — 과제는 생존"
    if not context:
        return None
    own = context[entry["team"]]
    other = context["enemy" if entry["team"] == "my" else "my"]
    if (
        own["heal_ratio"] is not None and other["heal_ratio"]
        and own["heal_ratio"] >= other["heal_ratio"] * HEALING_OPPORTUNITY_RATIO
    ):
        return "받은 피해 대비 치유 비율은 상대 힐러진과 비슷하다 — 받은 피해 자체가 적어 치유할 기회가 적었다"
    if _much_more(own["support_deaths"], other["support_deaths"]):
        return "힐러진이 상대보다 크게 많이 쓰러졌다 — 과제는 힐러진 생존·보호"
    if _much_more(own["frontline_deaths"], other["frontline_deaths"]):
        return "탱커·딜러가 상대보다 크게 많이 쓰러졌다 — 힐이 닿기 전에 쓰러지는 교전이 문제"
    return None


def _comparison_group(entry, entries, metric):
    """비교 대상(본인 제외)과, 같은 유형이 없어 같은 역할로 넓혔는지 여부."""
    others = [e for e in entries if e is not entry and e["stats"][metric] is not None]
    same_type = [e for e in others if e["type"] == entry["type"]]
    if same_type or metric == "mitigation":
        return same_type, False
    return [e for e in others if e["role"] == entry["role"]], True


def _raw_verdict(metric: str, value: float, peers: List[float]) -> str:
    mean = _mean(peers)
    gap = MIN_GAP[metric]
    if metric == "deaths":
        if value < min(peers) and mean - value >= gap:
            return "강점"
        if value >= mean * DEATH_WEAKNESS_RATIO and value - mean >= gap:
            return "약점"
        return "비슷"
    if value > max(peers) and value >= mean * STRENGTH_RATIO and value - mean >= gap:
        return "강점"
    if value <= mean * WEAKNESS_RATIO and mean - value >= gap:
        return "약점"
    return "비슷"


def judge_entry(
    entry: Dict[str, Any], entries: List[Dict[str, Any]],
    context: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """{지표: {verdict, value, mean, reason}}. verdict는 강점/비슷/약점/판정 제외."""
    weights = STAT_TYPES[entry["type"]]["weights"]
    result: Dict[str, Dict[str, Any]] = {}

    for metric in METRICS:
        value = entry["stats"][metric]
        if value is None:
            continue
        weight = weights.get(metric)
        if not weight:
            result[metric] = {"verdict": "판정 제외", "value": value, "mean": None, "reason": None}
            continue
        group, widened = _comparison_group(entry, entries, metric)
        if not group:
            result[metric] = {"verdict": "판정 제외", "value": value, "mean": None, "reason": "비교 대상 없음"}
            continue
        peers = [e["stats"][metric] for e in group]
        verdict = _raw_verdict(metric, value, peers)
        reason = None
        if verdict == "약점" and widened and metric != "deaths":
            verdict, reason = "비슷", "같은 유형 비교 대상이 없어 다른 유형과 비교함"
        if verdict == "약점" and weight <= BONUS_ONLY_WEIGHT:
            verdict, reason = "비슷", "이 유형에선 가점으로만 보는 지표"
        result[metric] = {"verdict": verdict, "value": value, "mean": _mean(peers), "reason": reason}

    def _verdict(metric):
        return result.get(metric, {}).get("verdict")

    def _soften(metric, reason):
        if _verdict(metric) == "약점":
            result[metric]["verdict"] = "비슷"
            result[metric]["reason"] = reason

    if entry["type"] == "sub_damage" and _verdict("kills") != "약점":
        _soften("damage", "서브 딜러는 처치가 부족하지 않으면 피해 총량을 약점으로 보지 않음")
    if entry["type"] == "sub_support" and _verdict("healing") == "강점":
        _soften("kills", "치유량이 강점인 서브 힐러")
        _soften("damage", "치유량이 강점인 서브 힐러")
    if entry["role"] == "support" and _verdict("healing") == "약점":
        reason = _healing_shortfall_reason(entry, _verdict("deaths"), context)
        if reason:
            _soften("healing", reason)

    return result


def _score_value(entry, metric, context) -> Optional[float]:
    """종합 점수에 쓰는 값. 힐러 치유량은 그 팀이 받은 피해로 보정한다."""
    value = entry["stats"][metric]
    if value is None:
        return None
    if metric == "healing" and entry["role"] == "support" and context:
        received = context[entry["team"]]["received_damage"]
        if received:
            return value / received
    return value


def _pair_score(metric: str, value: float, reference: float) -> float:
    """비교 대상 평균과 견준 0~100점(50 = 동등). 데스는 낮을수록 높다."""
    if value + reference <= 0:
        return 50.0
    if metric == "deaths":
        return reference / (value + reference) * 100
    return value / (value + reference) * 100


def _score_peers(entry, entries, metric, team, *, solo_fallback: bool) -> List[Dict[str, Any]]:
    """점수 비교 대상: 그 팀의 같은 포지션(본인 제외). 경감량은 같은 유형끼리만.

    solo_fallback이면 같은 포지션이 없을 때 역할과 무관한 지표(처치·데스)만 팀 전체와 비교한다.
    """
    candidates = [
        e for e in entries
        if e is not entry and e["team"] == team and e["stats"][metric] is not None
    ]
    if metric == "mitigation":
        return [e for e in candidates if e["type"] == entry["type"]]
    same_role = [e for e in candidates if e["role"] == entry["role"]]
    if same_role or not solo_fallback or metric not in ROLE_NEUTRAL_METRICS:
        return same_role
    return candidates


def _component_score(entry, entries, context, team, *, solo_fallback: bool):
    """한 비교 축(우리팀 내부 / 상대 같은 포지션)의 가중 점수와 지표별 점수."""
    weights = STAT_TYPES[entry["type"]]["weights"]
    per_metric: Dict[str, float] = {}
    for metric, weight in weights.items():
        value = _score_value(entry, metric, context)
        if value is None:
            continue
        peers = _score_peers(entry, entries, metric, team, solo_fallback=solo_fallback)
        if not peers:
            continue
        score = _pair_score(metric, value, _mean([_score_value(e, metric, context) for e in peers]))
        if weight <= BONUS_ONLY_WEIGHT and score < 50:
            continue
        per_metric[metric] = score
    weight_sum = sum(weights[m] for m in per_metric)
    if not weight_sum:
        return None, per_metric
    return sum(weights[m] * s for m, s in per_metric.items()) / weight_sum, per_metric


def score_breakdown(
    entries: List[Dict[str, Any]], context: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[int, Dict[str, Any]]:
    """{id(entry): {team, enemy, final, metrics}} — 우리팀 영웅만. 50 = 비교 대상과 동등.

    final = 우리팀 내부 50% + 상대 같은 포지션 50%. 상대 쪽을 계산할 수 없으면 우리팀 내부 100%.
    """
    result: Dict[int, Dict[str, Any]] = {}
    has_enemy = any(e["team"] == "enemy" for e in entries)
    for entry in entries:
        if entry["team"] != "my":
            continue
        team_score, team_metrics = _component_score(entry, entries, context, "my", solo_fallback=True)
        enemy_score, enemy_metrics = (
            _component_score(entry, entries, context, "enemy", solo_fallback=False)
            if has_enemy else (None, {})
        )
        if team_score is not None and enemy_score is not None:
            final = team_score * TEAM_SCORE_SHARE + enemy_score * (1 - TEAM_SCORE_SHARE)
        else:
            final = team_score if team_score is not None else enemy_score
        if final is None:
            continue
        weights = STAT_TYPES[entry["type"]]["weights"]
        result[id(entry)] = {
            "team": team_score, "enemy": enemy_score, "final": final,
            "metrics": {
                m: {"weight": w, "team": team_metrics.get(m), "enemy": enemy_metrics.get(m)}
                for m, w in weights.items()
            },
        }
    return result


def composite_scores(
    entries: List[Dict[str, Any]], context: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[int, float]:
    """{id(entry): 최종 점수} — 우리팀 영웅만(score_breakdown의 final)."""
    return {key: info["final"] for key, info in score_breakdown(entries, context).items()}


def _tie_break_key(entry, info):
    """동점이면 그 유형의 핵심 지표 점수 → 데스가 적은 쪽 → 나머지 지표 평균."""
    metrics = info["metrics"]
    top_metric = max(metrics, key=lambda m: metrics[m]["weight"])

    def _avg(m):
        values = [v for v in (metrics[m]["team"], metrics[m]["enemy"]) if v is not None]
        return sum(values) / len(values) if values else 0.0

    deaths = entry["stats"]["deaths"]
    rest = [_avg(m) for m in metrics if m != top_metric]
    return (
        round(info["final"], 2), _avg(top_metric),
        -(deaths if deaths is not None else 0), sum(rest) / len(rest) if rest else 0.0,
    )


def ranked_scores(
    entries: List[Dict[str, Any]], context: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """우리팀 순위 목록: [{rank, entry, team, enemy, final, metrics}]."""
    breakdown = score_breakdown(entries, context)
    ranked = sorted(
        (e for e in entries if id(e) in breakdown),
        key=lambda e: _tie_break_key(e, breakdown[id(e)]), reverse=True,
    )
    return [{"rank": i, "entry": e, **breakdown[id(e)]} for i, e in enumerate(ranked, 1)]


def score_basis_label(entries: List[Dict[str, Any]]) -> str:
    if any(e["team"] == "enemy" for e in entries):
        return "우리팀 내부 50% + 상대 같은 포지션 50%"
    return "상대 정보 없음 — 우리팀 내부 100%"


def format_score_ranking(entries: List[Dict[str, Any]], context=None) -> str:
    """우리팀 최종 점수 순위 줄."""
    ranked = ranked_scores(entries, context)
    if not ranked:
        return ""
    parts = [
        f"{r['rank']}위 {r['entry']['hero']}({STAT_TYPES[r['entry']['type']]['label']}) {r['final']:.1f}"
        for r in ranked
    ]
    return (
        f"- {TEAM_LABELS['my']} 종합 점수 순위(50 = 비교 대상과 동등, {score_basis_label(entries)}): "
        + ", ".join(parts)
    )


def _fmt(value: Optional[float]) -> str:
    if value is None:
        return "?"
    return f"{value:,.0f}"


def format_team_context(context: Optional[Dict[str, Dict[str, Any]]]) -> str:
    if not context:
        return ""
    parts = []
    for team in ("my", "enemy"):
        c = context[team]
        ratio = f"{c['heal_ratio']:.2f}" if c["heal_ratio"] is not None else "?"
        parts.append(
            f"{TEAM_LABELS[team]} 받은 피해 {_fmt(c['received_damage'])} 대비 힐러진 치유 "
            f"{_fmt(c['support_healing'])}(비율 {ratio}), 힐러진 데스 {_fmt(c['support_deaths'])}, "
            f"탱커·딜러 데스 {_fmt(c['frontline_deaths'])}"
        )
    return "- 팀 상황(받은 피해는 상대 총 피해량으로 어림): " + " / ".join(parts)


def _format_entry_verdicts(entry, verdicts) -> str:
    parts = []
    for metric in METRICS:
        info = verdicts.get(metric)
        if not info:
            continue
        text = f"{METRIC_LABELS[metric]} {_fmt(info['value'])} {info['verdict']}"
        if info["mean"] is not None and info["verdict"] in ("강점", "약점"):
            text += f"(비교 평균 {_fmt(info['mean'])})"
        if info["reason"]:
            text += f" — {info['reason']}"
        parts.append(text)
    me = ", 본인" if entry["is_me"] else ""
    label = STAT_TYPES[entry["type"]]["label"]
    return f"- {entry['hero']}({TEAM_LABELS[entry['team']]}, {label}{me}): " + " / ".join(parts)


def stat_verdict_text(
    entries: List[Dict[str, Any]], *, only_me: bool = False, include_ranking: bool = True,
) -> str:
    """팀 상황 + 종합 점수 순위 + 영웅별 판정. only_me면 본인 판정만(순위 생략)."""
    if not entries:
        return ""
    targets = [e for e in entries if e["is_me"]] if only_me else [e for e in entries if e["team"] == "my"]
    if not targets:
        return ""

    context = team_context(entries)
    lines = []
    context_line = format_team_context(context)
    if context_line:
        lines.append(context_line)
    if not only_me and include_ranking:
        ranking = format_score_ranking(entries, context)
        if ranking:
            lines.append(ranking)
    for entry in targets:
        lines.append(_format_entry_verdicts(entry, judge_entry(entry, entries, context)))
    return "\n".join(lines)


def score_report_rows(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """스탯창 순위 표·관리자 진단용 직렬화 결과(우리팀, 순위순)."""
    rows = []
    for r in ranked_scores(entries, team_context(entries)):
        entry = r["entry"]
        rows.append({
            "rank": r["rank"], "hero": entry["hero"], "type_label": STAT_TYPES[entry["type"]]["label"],
            "is_me": entry["is_me"], "team_score": r["team"], "enemy_score": r["enemy"], "final": r["final"],
            "metrics": {
                metric: {**info, "label": METRIC_LABELS[metric]} for metric, info in r["metrics"].items()
            },
        })
    return rows
