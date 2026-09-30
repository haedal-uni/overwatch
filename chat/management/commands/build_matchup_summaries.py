"""상성표 설명을 사용자에게 보여줄 문장으로 다듬어 JSON으로 저장한다.

    python manage.py build_matchup_summaries [--limit N] [--dry-run] [--delay 초] [--batch-size N]

문서를 고쳤거나 STYLE_VERSION을 올렸으면 다시 돌려야 화면에 반영된다.
"""

import json
import logging
import os
import time

from django.core.management.base import BaseCommand

from chat.rag.doc_sections import read_source_markdown
from chat.rag.llm_utils import call_llm_text, safe_json_loads
from chat.rag.matchup_tables import SUMMARY_PATH, _parse_hero_tables, clean_note
from chat.rag.vectorstore import ChatBot

logger = logging.getLogger(__name__)

# 너무 크게 잡으면 응답이 출력 한도에서 잘려 JSON이 깨진다.
BATCH_SIZE = 8
# 이 길이 이하는 말투만 바꾸고, 넘으면 줄인다.
SHORT_CHARS = 60
LONG_MAX_CHARS = 90
# 이만큼 연달아 배치가 실패하면 할당량 소진으로 보고 멈춘다(다시 실행하면 이어서 처리).
MAX_CONSECUTIVE_FAILURES = 3
# 말투 규칙을 바꾸면 올린다(옛 규칙으로 만든 줄을 다시 만든다).
STYLE_VERSION = 3

PROMPT = """너는 오버워치2 코칭 앱의 문구 담당이다. 아래 상성 설명은 문서 작성자가
메모처럼 적어둔 원문이다. 이걸 앱 사용자에게 보여줄 자연스러운 문장으로 바꿔라.

규칙:
1. **원문에 있는 내용만 쓴다.** 새로운 사실, 수치, 스킬 이름을 지어내지 마라.
2. 공식 공략 가이드의 문체로 써라. 모든 문장은 "~다"로 끝나는 평서문이고, 명사형 종결("~됨", "~불가",
   "확정 사망")과 체언으로 끝나는 토막 문장을 쓰지 마라. 원인과 결과는 "~때문에", "~로 인해",
   "~하므로"처럼 인과가 드러나게 이어라.
3. 줄표(—, –), 화살표(→), 세미콜론, 슬래시, 단어를 잇는 "+" 기호를 쓰지 마라.
   쉼표와 연결어미("~고", "~며", "~어서", "~라")로 이어라. "장갑+추진기"는
   "장갑과 추진기"로 쓴다.
4. 괄호 안 편집 메모("(디몬 문서 상성의 역방향)", "다른 문서 필요" 등)는 사용자에게
   필요 없는 정보라 버려라. "문서"라는 말을 쓰지 마라.
5. "매우 유리", "약간 불리" 같은 판정 표현은 쓰지 마라. 점수로 따로 보여준다.
6. 원문이 {short_chars}자 이하면 내용을 하나도 빼지 말고 말투만 바꿔라.
   더 길면 1~2문장, {long_max}자 이내로 줄이되 왜 유불리한지와 어떻게 상대하는지를
   남기고 곁가지(킷 설명, 다른 영웅과의 비교)를 버려라.
7. 주어가 헷갈리면 영웅 이름을 밝혀라. "영웅"은 표의 주인, "상대"는 표에 적힌 상대다.
8. 구어·속어·커뮤니티 은어는 뜻을 풀어 쓴다. 예: "따갑다" → "피해가 크다", "갈린다/녹는다/순삭" →
   "빠르게 처치된다", "거덜낸다" → "쉽게 파괴한다", "농락한다" → "일방적으로 공격한다",
   "벽꿍" → "벽에 부딪혀 추가 피해를 입힌다", "짤딜" → "견제 피해", "맛없다" → "효율이 낮다",
   "깔짝" → "짧게 공격하고 빠진다", "반피" → "체력이 절반 정도 남은". 줄임말도 풀어 쓴다("궁" → "궁극기",
   "딜" → "피해", "힐" → "치유", "자힐" → "자가 치유"). 체력 표현을 풀 때 누구의 체력인지는 원문에 적혀
   있을 때만 밝히고, 원문에 없으면 원문처럼 주어 없이 쓴다. 누구의 체력인지 추측해서 채우지 마라.
   스킬·궁극기 이름과 "탱커·딜러·힐러·진입·교전" 같은 일반 게임 용어는 그대로 쓴다.

예시:
- 원문: "서브 딜러의 천적. 갈고리에 끌리면 확정 사망, 원거리 짤딜은 깡체력에 간지러움."
- 결과: "서브 딜러에게 매우 위협적인 상대다. 갈고리에 끌려오면 처치될 가능성이 높고,
  체력이 많아 원거리 견제 피해는 거의 효과가 없다."

- 원문: "압도적 기동력에 도망가도 순식간에 따라잡히고 파워 블락으로 피해가 상쇄됨.
  순간 이동기도, 둠피 무력화 스킬도 없어 단독으로 몰아낼 수 없다. 궁 상성도
  최악—승천은 너무 느려 못 노리고, 부활은 무적이 없어 파멸의 일격 타이밍에
  부활하자마자 죽는다."
- 결과: "도주해도 금방 따라잡히며 파워 블락으로 피해가 상쇄된다. 둠피스트를 저지할
  이동기나 스킬이 없어 혼자서는 상대하기 어렵다."

- 원문: "(디몬 문서 상성의 역방향) 디몬은 방벽으로 물폭탄을 막고 추진기+히트스캔으로
  근접해 우양을 압박한다."
- 결과: "디몬은 방벽으로 물폭탄을 막고, 추진기와 히트스캔 무기로 접근해 우양을
  압박한다."

입력:
{items}

출력은 JSON 배열만. 다른 말은 쓰지 마라.
[{{"id": 1, "summary": "..."}}, ...]
"""


class Command(BaseCommand):
    help = "상성표 설명을 사용자용 문장으로 다듬어 저장한다."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0, help="앞에서 N개만 처리(시험용)")
        parser.add_argument("--dry-run", action="store_true", help="LLM을 부르지 않고 대상 수만 센다")
        parser.add_argument("--delay", type=float, default=1.0, help="요청 사이 대기 초(무료 할당량이면 12 = 분당 5회)")
        parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="한 요청에 넣을 줄 수(응답이 잘려 계속 실패하면 줄인다)")

    def handle(self, *args, **options):
        text = read_source_markdown()
        if not text:
            self.stderr.write("원본 문서를 읽지 못했습니다.")
            return

        rows = [
            {"key": f"{hero}|{opponent}", "a": hero, "b": opponent,
             "verdict": verdict, "reason": reason.strip()}
            for hero, table in _parse_hero_tables(text).items()
            for opponent, (verdict, reason) in table.items()
            if reason.strip()
        ]
        rows.sort(key=lambda row: row["key"])
        # --limit으로 잘라도 지우기 판단은 문서 전체 기준이다.
        all_keys = {row["key"] for row in rows}
        if options["limit"]:
            rows = rows[:options["limit"]]

        batch_size = max(1, options["batch_size"])
        self.stdout.write(f"대상 {len(rows)}줄, 배치 {batch_size}개씩")
        if options["dry_run"]:
            return

        entries = self._load_existing()
        # 문서에서 사라진 줄의 옛 요약은 지운다.
        removed = [key for key in entries if key not in all_keys]
        for key in removed:
            del entries[key]

        # 저장된 원문이나 말투 규칙이 지금과 다른 줄만 다시 만든다.
        todo = [
            row for row in rows
            if entries.get(row["key"], {}).get("source") != row["reason"]
            or entries.get(row["key"], {}).get("style") != STYLE_VERSION
        ]
        self.stdout.write(
            f"그대로 두는 것 {len(rows) - len(todo)}줄, 새로 만들 것 {len(todo)}줄, "
            f"지운 것 {len(removed)}줄"
        )
        if not todo:
            self._save(entries)
            self.stdout.write(self.style.SUCCESS("문서와 이미 같습니다."))
            return

        llm = ChatBot().get_llm()

        failed = []
        consecutive_failures = 0
        for start in range(0, len(todo), batch_size):
            batch = todo[start:start + batch_size]
            items = [
                {"id": i + 1, "영웅": row["a"], "상대": row["b"],
                 "판정": row["verdict"], "설명": row["reason"]}
                for i, row in enumerate(batch)
            ]
            prompt = PROMPT.format(
                short_chars=SHORT_CHARS,
                long_max=LONG_MAX_CHARS,
                items=json.dumps(items, ensure_ascii=False, indent=1),
            )
            try:
                parsed = safe_json_loads(call_llm_text(llm, prompt), [])
            except Exception as exc:
                logger.warning("배치 실패: %s", exc)
                parsed = []

            consecutive_failures = 0 if parsed else consecutive_failures + 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                failed.extend(row["key"] for row in todo[start:])
                self.stdout.write(self.style.WARNING(
                    f"{MAX_CONSECUTIVE_FAILURES}번 연속 실패해 멈춥니다(할당량 소진 가능성). 나중에 다시 실행하면 이어서 처리합니다."
                ))
                break

            by_id = {item.get("id"): (item.get("summary") or "").strip()
                     for item in parsed if isinstance(item, dict)}
            for i, row in enumerate(batch):
                summary = by_id.get(i + 1)
                if summary:
                    entries[row["key"]] = {
                        "summary": clean_note(summary),
                        "source": row["reason"],
                        "style": STYLE_VERSION,
                    }
                else:
                    failed.append(row["key"])

            done = min(start + batch_size, len(todo))
            self.stdout.write(f"  {done}/{len(todo)}")
            self._save(entries)
            time.sleep(options["delay"])

        self._save(entries)
        self.stdout.write(self.style.SUCCESS(
            f"저장 완료: {SUMMARY_PATH} ({len(entries)}줄, 실패 {len(failed)}줄)"
        ))
        if failed:
            self.stdout.write("실패한 줄은 다시 실행하면 이어서 처리한다: " + ", ".join(failed[:10]))

    def _load_existing(self):
        if not os.path.exists(SUMMARY_PATH):
            return {}
        with open(SUMMARY_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        return {
            key: value if isinstance(value, dict) else {"summary": value, "source": None}
            for key, value in raw.items()
        }

    def _save(self, entries):
        with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=1, sort_keys=True)
