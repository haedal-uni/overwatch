"""영웅 가이드 화면의 짧은 문구를 원본 문서에서 정리해 JSON으로 저장한다.

    python manage.py build_hero_guides [--hero 이름] [--limit N] [--dry-run] [--delay 초]

문서를 고쳤거나 STYLE_VERSION을 올렸으면 다시 돌려야 화면에 반영된다(바뀐 영웅만 다시 만든다).
"""

import json
import logging
import os
import time

from django.core.management.base import BaseCommand

from chat.domain.heroes import HERO_TO_ROLE, ROLE_LABELS, normalize_hero_name
from chat.rag.hero_guides import GUIDE_PATH, guide_source_text, source_hash
from chat.rag.llm_utils import call_llm_text, safe_json_loads
from chat.rag.vectorstore import ChatBot

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 3
STYLE_VERSION = 1

PROMPT = """너는 오버워치2 코칭 앱의 문구 담당이다. 아래는 영웅 "{hero}"({role})에 대한 문서 원문이다.
이걸 휴대폰 한 화면에 들어가는 "영웅 가이드" 문구로 짧게 정리해라.

규칙:
1. **원문에 있는 내용만 쓴다.** 원문에 없는 사실, 수치, 스킬, 특전, 영웅 이름을 지어내지 마라.
   원문에 해당 내용이 없으면 그 칸은 빈 문자열이나 빈 배열로 둔다.
2. 지난 패치·시즌 이야기("~로 바뀌었다", "예전에는")는 쓰지 마라. 지금 상태만 쓴다.
3. 줄표(—, –), 화살표, 세미콜론, 슬래시를 쓰지 마라. 줄임말("궁", "딜", "힐")은 풀어 쓴다.
   수치의 "%"와 "m"는 기호 그대로 쓴다("50%", "30m").
4. 역할은 "탱커/딜러/힐러"로 쓴다. 원문의 "돌격", "공격", "지원가", "돌격군"도 이렇게 바꾼다.
5. 감도·키 설정·그래픽 설정·장비 이야기는 넣지 마라.
6. 영웅 이름은 원문 표기 그대로 쓴다.
7. 글자 수 제한은 반드시 지킨다. 넘으면 꾸밈말과 이유를 빼고 핵심만 남긴다.

칸별 형식:
- concept: 이 영웅이 어떤 영웅인지 25자 안팎 명사구 한 줄. 예: "중거리 지속 화력, 기동성, 회복을 갖춘 올라운더"
- position: 포지션 잡는 법 최대 3단계. 한 단계는 40자 이내 "~한다" 문장. 원문이 상황별로 나눠 적었으면 그 순서를 따른다.
  각 단계는 어디에 서는지, 어떻게 움직이는지여야 한다. 잘하고 있는지 확인하는 법이나 설명 문장은 넣지 않는다.
- position_key: 포지션에서 가장 중요한 한 가지, 30자 이내. 원문에 핵심이라고 할 만한 게 없으면 "".
- priorities: 먼저 처리할 대상·일, 최대 4개, 각 18자 이내 명사구. 원문 "처리 우선" 항목에서 고른다.
- perks.minor / perks.major: 보조 특전과 주요 특전 각각, 원문에 있는 특전을 빠짐없이 모두(원문이 덜 쓰인다고 적은 특전도 빼지 마라).
  - name: 특전 이름(원문의 한국어 이름 그대로, 영어 병기 빼고)
  - effect: 무엇이 바뀌는지 30자 이내 명사구. 예: "나선 로켓 투사체 속도 50% 증가"
  - when: 언제 고르면 좋은지 35자 이내 "~때" 또는 "~한다" 한 줄. 원문에 없으면 "".
  - popular: 원문이 "선호된다", "많이 쓴다", "반드시 선택"처럼 더 많이 고른다고 적은 특전만 true.
- allies: 같이 하면 좋은 아군 묶음, 최대 3개. 원문 "협업" 항목의 분류를 따른다.
  한 묶음의 영웅은 모두 label이 말하는 성격이어야 한다. 성격이 다른 영웅을 한 묶음에 섞지 말고,
  묶음이 3개를 넘으면 중요한 3개만 남기고 나머지는 버린다.
  - label: 그 영웅들이 어떤 영웅인지 15자 이내로. "~하는 영웅", "~ 탱커"처럼 영웅을 가리키는 말로 끝낸다.
    예: "방벽 탱커", "궁극기를 키워 주는 힐러", "처치를 마무리해 주는 딜러"
  - heroes: 그 묶음의 영웅 이름 배열(원문에 적힌 영웅만)
- avoid: 같이 쓰면 손해인 조합, 최대 3개, 각 45자 이내 "~다" 문장. 원문 "중복 주의" 항목에서 고른다.
- weak: 이 영웅이 약한 상황 한 줄, 45자 이내 명사구 나열(끝에 "~에 취약하다"를 붙이지 않는다). 원문 "취약한 상대 판정/상황"에서 상황(영웅 이름 목록 말고)을 고른다.

원문:
{source}

출력은 JSON 객체 하나만. 다른 말은 쓰지 마라.
{{"concept": "", "position": [], "position_key": "", "priorities": [],
 "perks": {{"minor": [{{"name": "", "effect": "", "when": "", "popular": false}}], "major": []}},
 "allies": [{{"label": "", "heroes": []}}], "avoid": [], "weak": ""}}
"""


class Command(BaseCommand):
    help = "영웅 가이드 문구를 원본 문서에서 정리해 저장한다."

    def add_arguments(self, parser):
        parser.add_argument("--hero", action="append", default=[], help="이 영웅만(여러 번 가능)")
        parser.add_argument("--limit", type=int, default=0, help="새로 만들 영웅을 앞에서 N명만(시험용)")
        parser.add_argument("--dry-run", action="store_true", help="LLM을 부르지 않고 대상 수만 센다")
        parser.add_argument("--force", action="store_true", help="원문이 그대로여도 다시 만든다(--hero와 함께)")
        parser.add_argument("--delay", type=float, default=10.0, help="요청 사이 대기 초(무료 계정은 10 이상)")

    def handle(self, *args, **options):
        heroes = sorted(HERO_TO_ROLE)
        if options["hero"]:
            heroes = [normalize_hero_name(name) for name in options["hero"]]
            heroes = [name for name in heroes if name in HERO_TO_ROLE]

        entries = self._load_existing()
        removed = [name for name in entries if name not in HERO_TO_ROLE]
        for name in removed:
            del entries[name]

        sources = {name: guide_source_text(name) for name in heroes}
        todo = [
            name for name in heroes
            if sources[name]
            and (options["force"]
                 or entries.get(name, {}).get("source") != source_hash(sources[name])
                 or entries.get(name, {}).get("style") != STYLE_VERSION)
        ]
        if options["limit"]:
            todo = todo[:options["limit"]]
        self.stdout.write(f"대상 {len(heroes)}명, 새로 만들 것 {len(todo)}명, 지운 것 {len(removed)}명")
        if options["dry_run"]:
            return
        if not todo:
            self._save(entries)
            self.stdout.write(self.style.SUCCESS("문서와 이미 같습니다."))
            return

        llm = ChatBot().get_llm()
        failed = []
        consecutive_failures = 0
        for idx, name in enumerate(todo):
            prompt = PROMPT.format(
                hero=name,
                role=ROLE_LABELS.get(HERO_TO_ROLE[name], ""),
                source=sources[name],
            )
            try:
                parsed = safe_json_loads(call_llm_text(llm, prompt), {})
            except Exception as exc:
                logger.warning("가이드 실패(%s): %s", name, exc)
                parsed = {}

            if isinstance(parsed, dict) and parsed.get("concept"):
                parsed["source"] = source_hash(sources[name])
                parsed["style"] = STYLE_VERSION
                entries[name] = parsed
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
        self.stdout.write(self.style.SUCCESS(f"저장 완료: {GUIDE_PATH} ({len(entries)}명, 실패 {len(failed)}명)"))
        if failed:
            self.stdout.write("실패한 영웅은 다시 실행하면 이어서 처리한다: " + ", ".join(failed))

    def _load_existing(self):
        if not os.path.exists(GUIDE_PATH):
            return {}
        with open(GUIDE_PATH, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, entries):
        with open(GUIDE_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=1, sort_keys=True)
