import logging, random
from dataclasses import dataclass

from .rulings import ask_next_open_question, reteach, regress, ask_other_question, command, move_on
logger = logging.getLogger(__name__)


ACTION_CHANCES = { 
    "escalate_variant": .85, #the usual pick
    "regress": .15, #alternative 
}


@dataclass
class Context: #the situation that the rules need to be checked against
    step:dict
    steps: list
    variant: str
    question_id: int
    question_format: str
    open_ids: list
    reserve_ids: list
    return_to_position: int | None
    regressed_positions: list
    return_to_variant: str | None

    @property
    def on_detour(self):
        return self.return_to_position is not None


AFTER_CORRECT = [
    [ask_next_open_question],
]

AFTER_WRONG = [
    [reteach, regress], #ways that the tutor can help, i put the randomizer here
    [ask_other_question], 
]

#if both cant be used, move_on

def choose(options):
    total = sum(ACTION_CHANCES.get(opt["action"], 1) for opt in options)
    chances = [ACTION_CHANCES.get(opt["action"], 1) / total for opt in options]

    picked = random.choices(options, weights=chances)[0]
    chance = ACTION_CHANCES.get(picked["action"], 1) /total

    result = command(picked["action"], picked["position"], picked["variant"], picked.get("question_id"), picked.get("return_to"), picked.get("return_to_variant"))
    result["action_probability"] = chance
    result["action_candidates"] = [
        {"action": opt["action"], "next_step_position": opt["position"], "next_variant": opt["variant"], "probability": ch}
        for opt, ch in zip(options, chances)
    ]
    logger.info("[CHOOSE] picked %s (%.0f%%) out of %s", picked["action"], chance * 100, [o["action"] for o in options])
    return result


def follow(tiers, context):
    for tier in tiers:
        options = []
        for rule in tier:
            option = rule(context)
            if option:
                options.append(option)
        if options:
            logger.info("[DECIDE] Q%s on %s -> options: %s", context.question_id, context.variant, " or ".join(option["action"] for option in options))
            return choose(options)
    logger.info("[DECIDE] Q%s on %s -> nothing applies, move on", context.question_id, context.variant)
    return move_on(context.step, context.steps, context.return_to_position, context.return_to_variant)




def decide(step, steps, variant, is_correct, question_id, question_format, open_ids, reserve_ids,
           return_to_position, regressed_positions, return_to_variant=None):
    context = Context(step, steps, variant, question_id, question_format, open_ids, reserve_ids,
                  return_to_position, regressed_positions, return_to_variant)
    return follow(AFTER_CORRECT if is_correct else AFTER_WRONG, context)