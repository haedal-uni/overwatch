"""영웅마다 문서의 "처리 우선" 줄을 영웅 이름 목록으로 정리해 JSON으로 저장한다.

    python manage.py build_target_priorities [--hero 이름] [--limit N] [--dry-run] [--delay 초]

문서의 "처리 우선" 줄을 고쳤으면 다시 돌려야 우선 타겟 순서에 반영된다(바뀐 영웅만 다시 만든다).
"""

import json
import logging
import os
import time

from django.core.management.base import BaseCommand

from chat.domain.heroes import HERO_TO_ROLE, ROLE_HEROES, ROLE_LABELS, normalize_hero_name
from chat.rag.llm_utils import call_llm_text, safe_json_loads
from chat.rag.target_priorities import PRIORITY_PATH, clean_priority_entry, source_hash, target_rule_line
from chat.rag.vectorstore import ChatBot

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 3
STYLE_VERSION = 2
# 사람(또는 Claude)이 문서를 읽고 직접 정리한 항목의 model 값.
MANUAL_MODEL = "manual"

PROMPT = """너는 오버워치2 문서를 정리하는 담당이다. 아래는 영웅 "{hero}"({role})로 플레이할 때 상대 중 누구를 먼저 처리할지 적은 문서 원문이다.
이 원문을 상대 영웅 이름 목록으로 바꿔라.

영웅 목록:
{roster}

칸:
- first: 원문이 그 영웅을 두고 "최우선", "1순위", "첫 번째 목표", "가장 먼저", "최대한 일찍"이라고 적은 영웅만. 대부분 0~1명이다.
  quote에는 그 표현이 반드시 들어가야 한다.
- prefer: 원문이 이름을 대며 먼저 처치하라고 한 영웅, 또는 원문이 고정된 성질로 묶어 말한 무리(예: "공중 영웅", "저격수")에
  누가 봐도 드는 영웅. "미러전에서는", "스킬이 빠진"처럼 조건이 붙은 영웅도 여기까지만 넣는다.
- avoid: 원문이 그 영웅을 노리지 말라, 지나치라, 무시하라, 무리하게 쫓지 말라고 한 영웅. quote에 그 표현이 들어가야 한다.
- prefer_roles / avoid_roles: 원문이 역할 전체("힐러", "지원가", "딜러", "탱커", "돌격 영웅", "비돌격 영웅")를 먼저 노리라고 하거나 노리지 말라고 한 경우.
  role 값은 tank, damage, support 중 하나다. "비돌격 영웅"은 damage와 support 둘 다다.

규칙:
1. 원문에 없는 영웅을 넣지 마라. 무리를 영웅으로 바꿀 때는 그 무리가 원문에 적혀 있어야 한다.
2. 역할 전체를 말한 경우 그 역할 영웅 이름을 늘어놓지 말고 prefer_roles/avoid_roles에만 넣어라.
3. "생명력이 낮은", "이동기를 쓴", "아군 진영에 들어온", "궁극기를 쓰려는"처럼 판마다 바뀌는 상태는 영웅으로 바꾸지 마라.
4. 처치하라는 말이 아니면 넣지 마라. "견제한다", "밀쳐낸다", "방해한다", "막는다", "받아친다", "띄운다", 구조물(포탑, 방벽 생성기)을
   "제거한다", 기본 공격을 "한 발 먼저 넣는다", 그 영웅이 "~할 때 대응한다"는 처치 순서가 아니다.
   예: "공중 영웅은 연발총으로 견제하고" → 넣지 않는다. "윈스턴의 방벽 생성기를 제거한다" → 윈스턴을 넣지 않는다.
5. quote는 그 항목의 근거가 되는 원문 구절을 글자 그대로 옮긴다(띄어쓰기까지 같게, 20자 안팎).
6. 해당하는 항목이 없으면 빈 배열로 둔다.

원문:
{source}

출력은 JSON 객체 하나만. 다른 말은 쓰지 마라.
{{"first": [{{"hero": "", "quote": ""}}], "prefer": [], "avoid": [],
 "prefer_roles": [{{"role": "", "quote": ""}}], "avoid_roles": []}}
"""


def roster_text() -> str:
    return "\n".join(f"- {ROLE_LABELS[role]}: {', '.join(heroes)}" for role, heroes in ROLE_HEROES.items())


class Command(BaseCommand):
    help = "영웅마다 처리 우선 원칙을 영웅 이름 목록으로 정리해 저장한다."

    def add_arguments(self, parser):
        parser.add_argument("--hero", action="append", default=[], help="이 영웅만(여러 번 가능)")
        parser.add_argument("--limit", type=int, default=0, help="새로 만들 영웅을 앞에서 N명만(시험용)")
        parser.add_argument("--dry-run", action="store_true", help="LLM을 부르지 않고 대상 수만 센다")
        parser.add_argument("--force", action="store_true", help="원문이 그대로여도 다시 만든다")
        parser.add_argument("--delay", type=float, default=10.0, help="요청 사이 대기 초(무료 계정은 10 이상)")
        parser.add_argument("--model", default=None, help="정리에 쓸 Gemini 모델(기본은 채팅과 같은 모델)")

    def handle(self, *args, **options):
        heroes = sorted(HERO_TO_ROLE)
        if options["hero"]:
            heroes = [name for name in (normalize_hero_name(h) for h in options["hero"]) if name in HERO_TO_ROLE]

        entries = self._load_existing()
        for name in [name for name in entries if name not in HERO_TO_ROLE]:
            del entries[name]

        sources = {name: target_rule_line(name) for name in heroes}
        model = options["model"] or ChatBot().llm_model
        # --model을 주면 다른 모델로 만든 영웅도 다시 만든다(손으로 정리한 항목은 원문이 바뀌었거나 --force일 때만).
        todo = [
            name for name in heroes
            if sources[name]
            and (options["force"]
                 or entries.get(name, {}).get("source") != source_hash(sources[name])
                 or (entries.get(name, {}).get("model") != MANUAL_MODEL
                     and (entries.get(name, {}).get("style") != STYLE_VERSION
                          or (options["model"] and entries.get(name, {}).get("model") != model))))
        ]
        if options["limit"]:
            todo = todo[:options["limit"]]
        missing = [name for name in heroes if not sources[name]]
        self.stdout.write(f"대상 {len(heroes)}명, 새로 만들 것 {len(todo)}명, 처리 우선 줄 없음 {len(missing)}명 {missing}")
        if options["dry_run"] or not todo:
            self._save(entries)
            return

        llm = ChatBot(llm_model=model).get_llm()
        # 영웅마다 근거 구절이 붙어 채팅 답변보다 출력이 길다.
        llm.max_output_tokens = 4096
        failed = []
        consecutive_failures = 0
        for idx, name in enumerate(todo):
            prompt = PROMPT.format(
                hero=name, role=ROLE_LABELS.get(HERO_TO_ROLE[name], ""),
                roster=roster_text(), source=sources[name],
            )
            try:
                parsed = safe_json_loads(call_llm_text(llm, prompt), None)
            except Exception as exc:
                logger.warning("처리 우선 정리 실패(%s): %s", name, exc)
                parsed = None

            if isinstance(parsed, dict):
                entry = clean_priority_entry(parsed, sources[name])
                entry["source"] = source_hash(sources[name])
                entry["style"] = STYLE_VERSION
                entry["model"] = model
                entries[name] = entry
                consecutive_failures = 0
                self._save(entries)
            else:
                failed.append(name)
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    failed.extend(todo[idx + 1:])
                    self.stdout.write(self.style.WARNING(
                        f"{MAX_CONSECUTIVE_FAILURES}번 연속 실패해 멈춥니다(할당량 소진 가능성). 다시 실행하면 이어서 처리합니다."
                    ))
                    break

            self.stdout.write(f"  {idx + 1}/{len(todo)} {name}")
            if idx + 1 < len(todo):
                time.sleep(options["delay"])

        self._save(entries)
        self.stdout.write(self.style.SUCCESS(f"저장 완료: {PRIORITY_PATH} ({len(entries)}명, 실패 {len(failed)}명)"))
        if failed:
            self.stdout.write("실패한 영웅은 다시 실행하면 이어서 처리한다: " + ", ".join(failed))

    def _load_existing(self):
        if not os.path.exists(PRIORITY_PATH):
            return {}
        with open(PRIORITY_PATH, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, entries):
        with open(PRIORITY_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=1, sort_keys=True)
