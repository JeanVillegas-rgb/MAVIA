import json
from lessons.models import LearningObjectGroup, OutlineNode
from mobile_course_package.models import TopicPackage

# which topic's package to show
TOPIC_ID = 122
# False: hide the answer key (what the phone sees).  True: show each question's key + explanation (server only)
SHOW_ANSWERS = False
# False: a readable tree in the terminal.  True: the raw steps list as JSON
AS_JSON = False
# with AS_JSON: write here (plain UTF-8). A "> steps.json" redirect saves UTF-16 plus the shell's banner
JSON_FILE = "steps_peek.json"

# read the saved package as it is -- get_package() would rebuild a stale one
topic = OutlineNode.objects.get(pk=TOPIC_ID)
package = TopicPackage.objects.filter(topic=topic).first()
if package is None:
    raise SystemExit(f"Topic {TOPIC_ID} has no package yet: open it once in the app (or run build_mobile_packages).")

labels = dict(LearningObjectGroup.objects.filter(
    id__in={s["concept_id"] for s in package.steps}).values_list("id", "label"))


def name(concept_id):
    return f'{concept_id} "{labels.get(concept_id) or "?"}"'


def choices(question):
    c = question.get("choices")
    if isinstance(c, dict):
        return "  ".join(f"{k}. {v}" for k, v in sorted(c.items()))
    return "  ".join(f"{k}. {v}" for k, v in zip("ABCD", c or []))


def ask(question, audio):
    has_clip = "clip " if str(question["id"]) in audio else "voice"
    print(f"      Q{question['id']:<5} {question['format']:<3} {question.get('thinking_order', ''):<3} {has_clip}  {question['text']}")
    if choices(question):
        print(f"{'':25}{choices(question)}")
    if SHOW_ANSWERS:
        key = package.answer_key.get(str(question["id"]), {})
        print(f"{'':25}key: {key.get('correct_answer')}  --  {key.get('explanation', '')}")


if AS_JSON:
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(package.steps, f, indent=2, ensure_ascii=False)
    print(f"wrote {JSON_FILE}: {len(package.steps)} steps")
else:
    fresh = "up to date" if package.published_at == topic.published_at else "STALE: rebuilt on the next open"
    print(f"\nTOPIC {topic.id} · {topic.title} · {len(package.steps)} steps · "
          f"built {package.built_at:%Y-%m-%d %H:%M} ({fresh})")
    for index, step in enumerate(package.steps):
        print(f"\nsteps[{index}]  position {step['position']} · concept {name(step['concept_id'])}")
        print(f"  prerequisites  {', '.join(name(c) for c in step['prerequisites']) or '[]'}")
        print("  versions       " + "   ".join(f"{v} x{len(urls)}" for v, urls in step["versions"].items()))
        audio = step.get("question_audio", {})
        for pool in ("questions", "reserve_questions"):
            items = step.get(pool, [])
            print(f"  {pool} ({len(items)})")
            for question in items:
                ask(question, audio)

