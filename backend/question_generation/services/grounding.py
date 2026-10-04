"""Corrective-RAG validation gate for generated questions.

Generation writes a draft bank from one concept's Normal text. That text is
already in the prompt, so this gate is not here to *supply* context -- it is
here to check the model used the context it was given. Measured on the first
published topic before this existed: 61 of 76 final questions used words that
appear nowhere in the topic's PDFs, and two marked "plasma" correct where the
source says "gas" and "solid". The source was in the prompt both times.

Three stages, cheapest first, and a draft has to clear all three:

1. **Lexical grounding** -- content words in the question and its choices that
   appear nowhere in the topic corpus. Deterministic, no model, and on its own
   it catches the "plasma" class of failure.
2. **Retrieval** -- the draft Q&A becomes the query for a vector search over
   chunked raw PDF text, returning the top ``k`` passages. Scoped to the
   *topic's* materials, not the whole library: a global index would happily
   answer a question about particle arrangement with a passage about melting,
   and the judge would then validate a question against text the learner was
   never read.
3. **LLM-as-judge** -- the draft and the retrieved passages go to a second
   model asked one question: does this text support this answer? It reports
   which option the source actually supports, so a wrong key is caught rather
   than merely flagged as unsupported.

A failure carries a reason back to generation, which retries with that reason
appended to the prompt. Retries are bounded; a draft that cannot be grounded is
dropped rather than served.

Nothing here degrades silently. When the sentence encoder is unavailable the
gate runs lexical-only and says so in the verdict, so a teacher never reads an
unverified bank as a verified one.
"""

import json
import logging
import re

import requests
from django.conf import settings
from config.groq_client import generate as groq_generate

logger = logging.getLogger(__name__)

# Chunking. Small enough that a retrieved passage is about one idea and fits
# the encoder's window whole -- MiniLM truncates nothing here because
# `supports()` is checked before anything is embedded.
CHUNK_WORDS = 90
CHUNK_OVERLAP = 30

# Words too common to carry grounding evidence. Deliberately short: the point
# is to catch *content* words like "plasma" and "intermolecular", so anything
# that is not obviously furniture stays in.
_STOP_WORDS = frozenset("""
about above after again against all also always among and any are because been
before being below between both but came does each either else every follow for
from further had has have here how however into its itself just least less like
made make many might more most much must never next only other over own same
should since some such than that the their them then there these they this
those through thus too under until upon very were what when where which while
who why will with within without would your
best content false following given statement true
""".split())

_WORD = re.compile(r"[a-z][a-z-]{2,}")


def _variants(word):
    """Every written form that should count as the same word as ``word``.

    Measured need, not tidiness: the lesson says "the third *depicts* particles
    far apart", a generated question said "the state *depicted* by particles
    far apart", and an exact-match vocabulary rejected a question that was
    word-for-word grounded in its source. Plurals do the same ("arrangement"
    against "arrangements").

    A candidate set rather than one canonical stem, because no suffix rule
    picks the right single form for both sides: stripping "-ing" turns
    "moving" into "mov", which no rule turns "move" into. Emitting "mov" *and*
    "move" lets the two meet without a stemmer, a dependency or lemma data to
    ship.

    It only ever *merges* forms, so an invented word has nothing to merge with
    and is still caught -- "plasma", "temperature" and "intermolecular" each
    yield themselves alone. Length guards stop short words eroding: "gas" must
    not become "ga".
    """
    forms = {word}
    if len(word) <= 3:
        return forms
    if len(word) >= 5 and word.endswith(("ies", "ied")):
        forms.add(word[:-3] + "y")
    for suffix in ("sses", "shes", "ches", "xes", "zes", "ses"):
        if word.endswith(suffix) and len(word) >= len(suffix) + 2:
            forms.add(word[:-2])
    if len(word) >= 4 and word.endswith("s") and not word.endswith("ss"):
        forms.add(word[:-1])
    for suffix, minimum in (("ing", 6), ("ed", 5)):
        if word.endswith(suffix) and len(word) >= minimum:
            stem = word[: -len(suffix)]
            forms.add(stem)
            # "moving" -> "move": the silent e the suffix displaced.
            forms.add(stem + "e")
            # "stopped" -> "stop": the consonant the suffix doubled.
            if len(stem) > 3 and stem[-1] == stem[-2]:
                forms.add(stem[:-1])
    return forms


def _words(text):
    return _WORD.findall((text or "").lower())


def _terms(text):
    """Every form of every content word in ``text`` -- the corpus side.

    Both sides expand, so matching is symmetric: the corpus offering
    "depicts" and a question offering "depicted" meet at "depict" without
    either having to guess the other's spelling.
    """
    forms = set()
    for word in _words(text):
        forms |= _variants(word)
    return forms


def _is_grounded(word, vocabulary):
    return bool(_variants(word) & vocabulary)


# -- 1. the index ---------------------------------------------------------

def _chunk_text(text):
    """Overlapping word windows over one PDF's raw extracted text.

    Overlap matters: a sentence cut across a boundary would otherwise support
    nothing, and the one fact a question turns on is often that sentence.
    """
    words = (text or "").split()
    if not words:
        return []
    if len(words) <= CHUNK_WORDS:
        return [" ".join(words)]
    step = max(1, CHUNK_WORDS - CHUNK_OVERLAP)
    chunks = []
    for start in range(0, len(words), step):
        window = words[start:start + CHUNK_WORDS]
        if not window:
            break
        chunks.append(" ".join(window))
        if start + CHUNK_WORDS >= len(words):
            break
    return chunks


def _fit(engine, text):
    """Trim ``text`` until the encoder accepts it, or "" if it never does.

    The query is a whole draft Q&A -- stem plus four choices -- which can run
    past the encoder's window. Dropping trailing words is lossy but honest;
    silently truncating inside the model is what ``supports()`` exists to
    prevent everywhere else in this codebase.
    """
    words = (text or "").split()
    while words:
        candidate = " ".join(words)
        if engine.supports(candidate):
            return candidate
        words = words[:int(len(words) * 0.9)] if len(words) > 10 else words[:-1]
    return ""


class TopicIndex:
    """Chunked raw PDF text for one topic, with its embeddings.

    Built once per run and passed down, because every draft of every concept
    in the topic searches the same corpus. Embeddings go through the grouping
    pipeline's own on-disk cache, so a republish of an unchanged PDF re-embeds
    nothing.
    """

    def __init__(self, chunks, vocabulary, vectors=None, reason=""):
        self.chunks = chunks
        self.vocabulary = vocabulary
        self.vectors = vectors
        self.reason = reason

    @property
    def searchable(self):
        return bool(self.vectors) and bool(self.chunks)

    def search(self, query, k=None):
        """Top-k chunks by cosine, highest first. Empty when unsearchable."""
        k = k or int(getattr(settings, "QUESTION_VALIDATION_TOP_K", 3))
        if not self.searchable:
            return []
        from lessons.services import semantic_grouping

        engine = semantic_grouping.runtime()
        fitted = _fit(engine, query)
        if not fitted:
            return []
        # Vectors come back normalised, so the dot product is the cosine --
        # the same assumption learning_path's criteria already rely on.
        vector = engine.embeddings([fitted])[0]
        scored = [
            (sum(a * b for a, b in zip(vector, candidate)), chunk)
            for candidate, chunk in zip(self.vectors, self.chunks)
        ]
        scored.sort(key=lambda row: -row[0])
        return scored[:k]


def build_index(outline_node):
    """Index every completed PDF of one topic. Never raises."""
    from lessons.models import LearningMaterial

    materials = LearningMaterial.objects.filter(
        outline_node=outline_node, status=LearningMaterial.Status.COMPLETED,
    ).order_by("created_at", "id")

    chunks, vocabulary = [], set()
    for material in materials:
        raw = material.extracted_text or ""
        vocabulary |= _terms(raw)
        for body in _chunk_text(raw):
            chunks.append({
                "material_id": material.id,
                "material_title": material.title,
                "text": body,
            })
        # A figure carries no PDF text, so its written description is the only
        # record of what the learner is actually told about it.
        for item in material.learning_objects.all():
            vocabulary |= _terms(item.content) | _terms(item.title)

    if not chunks:
        return TopicIndex([], vocabulary, reason="no extracted PDF text to index")

    try:
        from lessons.services import semantic_grouping

        engine = semantic_grouping.runtime()
    except Exception as exc:  # noqa: BLE001 -- a lexical-only gate beats none
        logger.warning("Grounding index falling back to lexical-only: %s", exc)
        return TopicIndex(
            chunks, vocabulary, reason=f"sentence encoder unavailable: {exc}"
        )

    usable, texts = [], []
    for chunk in chunks:
        fitted = _fit(engine, chunk["text"])
        if fitted:
            usable.append(chunk)
            texts.append(fitted)
    if not texts:
        return TopicIndex(chunks, vocabulary, reason="no chunk fit the encoder window")

    try:
        vectors = engine.embeddings(texts)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Grounding index could not embed chunks: %s", exc)
        return TopicIndex(chunks, vocabulary, reason=f"embedding failed: {exc}")

    return TopicIndex(usable, vocabulary, vectors=vectors)


# -- 2. the draft, as text ------------------------------------------------

def asserted_answer(question):
    """What this draft claims is true, written out rather than as a letter."""
    if question.question_format == "TF":
        return str(question.correct_answer)
    choices = question.choices or {}
    key = str(question.correct_answer).strip()
    if isinstance(choices, dict) and key in choices:
        return f"{key}) {choices[key]}"
    return key


def question_query(question):
    """The draft Q&A as one string -- retrieval query and judge subject alike."""
    parts = [question.question_text]
    choices = question.choices or {}
    if isinstance(choices, dict):
        parts.extend(f"{letter}) {text}" for letter, text in sorted(choices.items()))
    elif isinstance(choices, (list, tuple)):
        parts.extend(str(text) for text in choices)
    parts.append(f"Answer: {asserted_answer(question)}")
    return "\n".join(str(part) for part in parts if part)


# -- 3. lexical grounding -------------------------------------------------

def ungrounded_terms(question, index):
    """Content words the draft *asserts* that appear nowhere in the topic's PDFs.

    Scoped to the stem and the option marked correct -- deliberately **not**
    the distractors. A good multiple-choice distractor is wrong on purpose,
    and wrong often means vocabulary the lesson never uses ("none of the
    above", "indefinite", "neither"). Scanning every option made the gate
    punish the generator for writing usable distractors: measured on the first
    live run, it rejected "What is the arrangement of particles in a solid?"
    -- whose stem and correct answer are both straight from the source --
    because one distractor said "none".

    The narrower scope loses nothing that matters. Every defect this gate was
    built for lives in the stem or the key: "plasma" was the *correct answer*
    on Q107 and Q109, and appeared in Q114's stem.
    """
    pieces = [question.question_text or "", asserted_answer(question)]
    return sorted({
        word for word in _words(" ".join(pieces))
        if word not in _STOP_WORDS and not _is_grounded(word, index.vocabulary)
    })


# -- 4. LLM as judge ------------------------------------------------------

JUDGE_PROMPT = """You are a strict fact-checker for a science lesson for young
learners. You are given SOURCE passages taken from the lesson's own materials,
and one draft quiz question with the answer it claims is correct.

Decide ONLY from SOURCE. Do not use outside knowledge. If SOURCE does not say
it, it is not supported -- even if you know it to be true in general.

Return one JSON object:
- verdict: "supported" if SOURCE states or directly implies that the claimed
  answer is correct; "contradicted" if SOURCE indicates a different answer is
  correct; "unsupported" if SOURCE simply does not settle it.
- supported_answer: when the verdict is "contradicted", the option SOURCE
  actually supports, copied exactly from the draft's options. Otherwise "".
- problem: one short sentence naming the specific unsupported or contradicted
  claim. Empty string when the verdict is "supported".

SOURCE:
{passages}

DRAFT QUESTION:
{draft}
"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["supported", "contradicted", "unsupported"],
        },
        "supported_answer": {"type": "string"},
        "problem": {"type": "string"},
    },
    "required": ["verdict", "supported_answer", "problem"],
}


def _judge_model():
    return getattr(settings, "QUESTION_JUDGE_MODEL", settings.QUESTION_LLM_MODEL)


def _judge_call(prompt):
    """One judging call. Temperature 0 -- a verdict is not a creative task."""
    if settings.LLM_PROVIDER == "groq":
        raw, _metrics = groq_generate(
            prompt,
            model=_judge_model(),
            schema=JUDGE_SCHEMA,
            temperature=0.0,
            max_tokens=512,
            timeout=getattr(settings, "QUESTION_JUDGE_TIMEOUT", settings.OLLAMA_TIMEOUT),
        )
        return json.loads(raw)
    response = requests.post(
        f"{settings.OLLAMA_BASE_URL}/api/generate",
        json={
            "model": _judge_model(),
            "prompt": prompt,
            "stream": False,
            "format": JUDGE_SCHEMA,
            "keep_alive": getattr(
                settings, "QUESTION_LLM_KEEP_ALIVE", settings.OLLAMA_KEEP_ALIVE
            ),
            "options": {"temperature": 0.0, "num_predict": 512},
        },
        timeout=getattr(settings, "QUESTION_JUDGE_TIMEOUT", settings.OLLAMA_TIMEOUT),
    )
    response.raise_for_status()
    body = response.json()
    if body.get("error"):
        raise RuntimeError(str(body["error"]))
    return json.loads(body.get("response") or "{}")


def judge(question, passages):
    """``(verdict, supported_answer, problem)`` for one draft and its passages."""
    prompt = JUDGE_PROMPT.format(
        passages="\n\n".join(
            f"[{index + 1}] ({chunk['material_title']}) {chunk['text']}"
            for index, chunk in enumerate(passages)
        ),
        draft=question_query(question),
    )
    result = _judge_call(prompt)
    verdict = str(result.get("verdict") or "").strip().lower()
    if verdict not in ("supported", "contradicted", "unsupported"):
        verdict = "unsupported"
    return (
        verdict,
        str(result.get("supported_answer") or "").strip(),
        str(result.get("problem") or "").strip(),
    )


# -- 5. the gate ----------------------------------------------------------

def enabled():
    return bool(getattr(settings, "QUESTION_VALIDATION_ENABLED", True))


def verify(question, index):
    """One draft's verdict.

    ``{"passed", "stage", "reason", "verdict", "retrieved", "novel_terms"}``.
    A stage that could not run says so rather than being counted as a pass.
    """
    novel = ungrounded_terms(question, index)
    limit = int(getattr(settings, "QUESTION_VALIDATION_MAX_NOVEL_TERMS", 0))
    if len(novel) > limit:
        return {
            "passed": False,
            "stage": "lexical",
            "reason": "uses wording absent from the lesson materials: "
                      + ", ".join(novel[:6]),
            "verdict": "unsupported",
            "retrieved": [],
            "novel_terms": novel,
        }

    if not index.searchable:
        # Lexical passed and retrieval is unavailable. The draft goes through,
        # but the trace records that the retrieval gate never ran, so an
        # unverified bank is never read as a verified one.
        return {
            "passed": True,
            "stage": "lexical_only",
            "reason": index.reason or "retrieval unavailable",
            "verdict": "unverified",
            "retrieved": [],
            "novel_terms": novel,
        }

    hits = index.search(question_query(question))
    if not hits:
        return {
            "passed": False,
            "stage": "retrieval",
            "reason": "no source passage matched this question",
            "verdict": "unsupported",
            "retrieved": [],
            "novel_terms": novel,
        }

    passages = [chunk for _score, chunk in hits]
    retrieved = [
        {
            "material_id": chunk["material_id"],
            "score": round(score, 4),
            "preview": chunk["text"][:120],
        }
        for score, chunk in hits
    ]

    try:
        verdict, supported_answer, problem = judge(question, passages)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Judge call failed for draft %s: %s", question.id, exc)
        return {
            "passed": True,
            "stage": "judge_unavailable",
            "reason": f"judge unavailable: {exc}",
            "verdict": "unverified",
            "retrieved": retrieved,
            "novel_terms": novel,
        }

    if verdict == "supported":
        return {
            "passed": True, "stage": "judge", "reason": "",
            "verdict": verdict, "retrieved": retrieved, "novel_terms": novel,
        }

    reason = problem or (
        f"the source does not support the answer {asserted_answer(question)!r}"
    )
    if verdict == "contradicted" and supported_answer:
        reason = f"{reason} -- the source supports {supported_answer!r}"
    return {
        "passed": False, "stage": "judge", "reason": reason,
        "verdict": verdict, "retrieved": retrieved, "novel_terms": novel,
    }


def correction_note(failures):
    """The feedback appended to the generation prompt on a corrective retry."""
    if not failures:
        return ""
    lines = [f'- "{text}" was rejected: {reason}' for text, reason in failures[:6]]
    return (
        "A previous attempt produced questions the source text does not "
        "support. Do not repeat these mistakes:\n"
        + "\n".join(lines)
        + "\nAsk only about facts stated in the content above, and make sure "
          "the option you mark correct is the one the content states."
    )
