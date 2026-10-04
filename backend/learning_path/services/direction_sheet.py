"""The v7 direction votes as a spreadsheet (spec section 7).

One CSV: the settings, the block x concept matrix, then one row per pair whose
counts, shares, votes and verdict are Excel formulas over the matrix, so a
teacher can change a 0/1 cell or a setting and watch the verdict move. Heading,
order and the teaching centres are values: they come from the lesson's
structure, not from the matrix. The two "Python" columns are what the code
decided, for checking the formulas against.
"""

from . import criteria
from .clues import find_term_owners, heading_vote
from .concept_text import prepare
from .direction_votes import (
    MIN_BLOCKS, REFERENCE_GAP, SUBSUME_HIGH, SUBSUME_LOW, build_block_matrix, centre_order_vote,
)
from .embeddings import EncoderUnavailable

HEADER = [
    "A", "B", "Heading", "Centres", "Blocks A", "Blocks B", "Both", "P(A|B)", "P(B|A)",
    "A refers to B", "B refers to A", "Hierarchy", "Order", "Reference", "Voters", "Total",
    "Verdict", "Direction", "Python verdict", "Python direction",
]


def column(number):
    """Spreadsheet column letters: 1 -> A, 27 -> AA."""
    letters = ""
    while number:
        number, rest = divmod(number - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _no_encoder(sentences):
    # The verdicts do not need the encoder (criteria docstring); skip loading it.
    raise EncoderUnavailable("not needed for the sheet")


def direction_sheet_rows(concepts):
    concepts = list(concepts)
    texts = prepare(concepts)
    by_id = {text.id: text for text in texts}
    matrix = build_block_matrix(texts, find_term_owners(texts))
    labels = {text.id: f"{text.concept.title} [{text.id}]" for text in texts}
    last = column(len(matrix.blocks) + 1)
    owners = f"$B$3:${last}$3"

    rows = [
        ["Settings", "MIN_BLOCKS", MIN_BLOCKS, "SUBSUME_HIGH", SUBSUME_HIGH,
         "SUBSUME_LOW", SUBSUME_LOW, "REFERENCE_GAP", REFERENCE_GAP],
        [],
        ["Owner"] + [labels[block.owner] for block in matrix.blocks],
        ["PDF"] + [block.material_id for block in matrix.blocks],
        ["Position"] + [block.order for block in matrix.blocks],
    ]
    matrix_row = {}
    for text in texts:
        matrix_row[text.id] = len(rows) + 1
        rows.append([labels[text.id]] + [int(cell) for cell in matrix.present[text.id]])
    rows += [[], HEADER]

    position = {text.id: index for index, text in enumerate(texts)}
    decisions = criteria.decide_pairs(concepts, embed=_no_encoder, rule=criteria.THREE_VOTES)
    for decision in decisions:
        if decision["evidence"]["rule"] != criteria.THREE_VOTES:
            continue
        a, b = sorted((decision["prerequisite"].id, decision["dependent"].id), key=position.get)
        r = len(rows) + 1
        span_a = f"$B${matrix_row[a]}:${last}${matrix_row[a]}"
        span_b = f"$B${matrix_row[b]}:${last}${matrix_row[b]}"
        order, order_record = centre_order_vote(by_id[a], by_id[b], matrix)
        rows.append([
            labels[a], labels[b],
            heading_vote(by_id[a], by_id[b])[0],
            "; ".join(f"PDF {pdf}: {first} vs {second}" for pdf, (first, second) in order_record["centres"].items()),
            f"=SUM({span_a})",
            f"=SUM({span_b})",
            f"=SUMPRODUCT({span_a},{span_b})",
            f"=IF(F{r}=0,0,G{r}/F{r})",
            f"=IF(E{r}=0,0,G{r}/E{r})",
            f"=SUMPRODUCT(({owners}=A{r})*({span_b}))/COUNTIF({owners},A{r})",
            f"=SUMPRODUCT(({owners}=B{r})*({span_a}))/COUNTIF({owners},B{r})",
            f"=IF(C{r}<>0,C{r},IF(AND(E{r}>=$C$1,F{r}>=$C$1),"
            f"IF(AND(H{r}>=$E$1,I{r}<=$G$1),1,IF(AND(I{r}>=$E$1,H{r}<=$G$1),-1,0)),0))",
            order,
            f"=IF(K{r}-J{r}>=$I$1,1,IF(K{r}-J{r}<=-$I$1,-1,0))",
            f'=COUNTIF(L{r}:N{r},"<>0")',
            f"=SUM(L{r}:N{r})",
            f'=IF(O{r}=0,"pending",IF(AND(O{r}=1,M{r}=0),"pending",'
            f'IF(ABS(P{r})>=O{r}-(O{r}=3)*2,"accepted","pending")))',
            f'=IF(P{r}>0,"A first",IF(P{r}<0,"B first",IF(M{r}<0,"B first","A first")))',
            decision["verdict"],
            "A first" if decision["prerequisite"].id == a else "B first",
        ])
    return rows
