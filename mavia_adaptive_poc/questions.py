"""Question bank for the matter curriculum, keyed by (node, tier).

Every question has `choices` and an `answer` index into `choices`, so
true/false and multiple-choice items share one shape. Two variants per
(node, tier) so a concept revisited at the same tier doesn't repeat verbatim.
"""

TRUE_FALSE = ["True", "False"]

QUESTION_BANK = {
    "properties_of_matter": {
        1: [
            {"type": "true_false", "prompt": "Matter is anything that has mass and takes up space.",
             "choices": TRUE_FALSE, "answer": 0},
            {"type": "true_false", "prompt": "Color is not something you can observe about an object.",
             "choices": TRUE_FALSE, "answer": 1},
        ],
        2: [
            {"type": "multiple_choice", "prompt": "Which of the following is a physical property of matter?",
             "choices": ["Mass", "Emotion", "Opinion", "Time"], "answer": 0},
            {"type": "multiple_choice", "prompt": "Which property describes how much space an object takes up?",
             "choices": ["Volume", "Mass", "Temperature", "Weight"], "answer": 0},
        ],
        3: [
            {"type": "multiple_choice",
             "prompt": "A rock and a feather are the same size, but the rock feels heavier. This is because of a difference in their ______.",
             "choices": ["density", "color", "shape", "smell"], "answer": 0},
            {"type": "multiple_choice",
             "prompt": "You describe an object as hard, blue, and cube-shaped. These are all examples of ______.",
             "choices": ["properties of matter", "chemical changes", "states of energy", "living things"], "answer": 0},
        ],
    },
    "mass_and_volume": {
        1: [
            {"type": "true_false", "prompt": "Mass is the amount of matter in an object.",
             "choices": TRUE_FALSE, "answer": 0},
            {"type": "true_false", "prompt": "Volume measures how heavy an object is.",
             "choices": TRUE_FALSE, "answer": 1},
        ],
        2: [
            {"type": "multiple_choice", "prompt": "Which tool is commonly used to measure the volume of a liquid?",
             "choices": ["Graduated cylinder", "Thermometer", "Ruler", "Clock"], "answer": 0},
            {"type": "multiple_choice", "prompt": "Which unit is commonly used to measure mass?",
             "choices": ["Grams", "Liters", "Meters", "Seconds"], "answer": 0},
        ],
        3: [
            {"type": "multiple_choice",
             "prompt": "Two boxes have the same volume, but Box A is heavier than Box B. Box A likely has more ______.",
             "choices": ["mass", "color", "shape", "temperature"], "answer": 0},
            {"type": "multiple_choice",
             "prompt": "You pour 200 mL of water into a cube-shaped container. What did you just measure?",
             "choices": ["Volume", "Mass", "Weight", "Density"], "answer": 0},
        ],
    },
    "density": {
        1: [
            {"type": "true_false", "prompt": "Density compares how much mass is packed into a given volume.",
             "choices": TRUE_FALSE, "answer": 0},
            {"type": "true_false", "prompt": "Objects that are denser than water will float on it.",
             "choices": TRUE_FALSE, "answer": 1},
        ],
        2: [
            {"type": "multiple_choice", "prompt": "What is the formula for density?",
             "choices": ["mass ÷ volume", "mass × volume", "volume ÷ mass", "mass + volume"], "answer": 0},
            {"type": "multiple_choice", "prompt": "Which object is most likely to sink in water?",
             "choices": ["A solid rock", "A cork", "An empty bottle", "A beach ball"], "answer": 0},
        ],
        3: [
            {"type": "multiple_choice",
             "prompt": "Oil floats on top of water because oil has ______ density than water.",
             "choices": ["lower", "higher", "the same", "no"], "answer": 0},
            {"type": "multiple_choice",
             "prompt": "A metal ball and a plastic ball are the same size. The metal ball sinks and the plastic ball floats. This is because the metal ball has greater ______.",
             "choices": ["density", "color", "volume", "shape"], "answer": 0},
        ],
    },
    "states_of_matter": {
        1: [
            {"type": "true_false", "prompt": "The three common states of matter are solid, liquid, and gas.",
             "choices": TRUE_FALSE, "answer": 0},
            {"type": "true_false", "prompt": "A liquid has a fixed shape, just like a solid.",
             "choices": TRUE_FALSE, "answer": 1},
        ],
        2: [
            {"type": "multiple_choice", "prompt": "Which state of matter has a definite shape and a definite volume?",
             "choices": ["Solid", "Liquid", "Gas", "Plasma"], "answer": 0},
            {"type": "multiple_choice",
             "prompt": "Which state of matter takes the shape of its container but keeps the same volume?",
             "choices": ["Liquid", "Solid", "Gas", "None of these"], "answer": 0},
        ],
        3: [
            {"type": "multiple_choice", "prompt": "Steam rising from a boiling pot of water is in which state?",
             "choices": ["Gas", "Solid", "Liquid", "Plasma"], "answer": 0},
            {"type": "multiple_choice", "prompt": "Ice cubes floating in a glass of water are an example of which state?",
             "choices": ["Solid", "Liquid", "Gas", "Mixture"], "answer": 0},
        ],
    },
    "changes_of_state": {
        1: [
            {"type": "true_false", "prompt": "Melting is when a solid changes into a liquid.",
             "choices": TRUE_FALSE, "answer": 0},
            {"type": "true_false", "prompt": "Freezing changes a liquid into a gas.",
             "choices": TRUE_FALSE, "answer": 1},
        ],
        2: [
            {"type": "multiple_choice", "prompt": "What is it called when a liquid changes into a gas?",
             "choices": ["Evaporation", "Condensation", "Freezing", "Melting"], "answer": 0},
            {"type": "multiple_choice", "prompt": "What is it called when a gas changes into a liquid?",
             "choices": ["Condensation", "Evaporation", "Melting", "Freezing"], "answer": 0},
        ],
        3: [
            {"type": "multiple_choice",
             "prompt": "Water droplets form on a cold glass of lemonade on a hot day. This is an example of ______.",
             "choices": ["condensation", "evaporation", "melting", "freezing"], "answer": 0},
            {"type": "multiple_choice",
             "prompt": "An ice pop left out in the sun melts because it gains ______.",
             "choices": ["heat energy", "cold energy", "pressure", "mass"], "answer": 0},
        ],
    },
    "mixtures_and_solutions": {
        1: [
            {"type": "true_false",
             "prompt": "A mixture is made of two or more substances combined but not chemically joined.",
             "choices": TRUE_FALSE, "answer": 0},
            {"type": "true_false",
             "prompt": "In a solution, the dissolved substance separates back out on its own within seconds.",
             "choices": TRUE_FALSE, "answer": 1},
        ],
        2: [
            {"type": "multiple_choice", "prompt": "Salt water is an example of a ______.",
             "choices": ["solution", "pure element", "chemical change", "brand-new substance"], "answer": 0},
            {"type": "multiple_choice", "prompt": "Which of these is a mixture?",
             "choices": ["Trail mix (nuts and raisins)", "Pure water", "Table salt", "Oxygen gas"], "answer": 0},
        ],
        3: [
            {"type": "multiple_choice",
             "prompt": "You stir sugar into water until it disappears. This is a solution because the sugar ______.",
             "choices": ["dissolved evenly throughout the water", "burned away", "turned into a gas", "reacted to form a new element"],
             "answer": 0},
            {"type": "multiple_choice",
             "prompt": "Sand and iron filings are mixed together. What is the best way to separate them?",
             "choices": ["Use a magnet to pull out the iron filings", "Boil the mixture", "Freeze the mixture", "Add food coloring"],
             "answer": 0},
        ],
    },
    "physical_changes": {
        1: [
            {"type": "true_false",
             "prompt": "A physical change alters the form of matter but not its chemical makeup.",
             "choices": TRUE_FALSE, "answer": 0},
            {"type": "true_false", "prompt": "Cutting a piece of paper into smaller pieces creates a new substance.",
             "choices": TRUE_FALSE, "answer": 1},
        ],
        2: [
            {"type": "multiple_choice", "prompt": "Which of these is a physical change?",
             "choices": ["Tearing paper", "Burning wood", "Rusting iron", "Baking a cake"], "answer": 0},
            {"type": "multiple_choice", "prompt": "Which type of change can usually be reversed?",
             "choices": ["Physical change", "Chemical change", "Both equally", "Neither"], "answer": 0},
        ],
        3: [
            {"type": "multiple_choice",
             "prompt": "Bending a paperclip into a new shape is a physical change because ______.",
             "choices": ["no new substance is formed", "a new substance forms", "it releases gas", "its chemical formula changes"],
             "answer": 0},
            {"type": "multiple_choice",
             "prompt": "Melting chocolate and letting it harden again is a physical change because you can ______.",
             "choices": ["reverse it back to a solid", "never get solid chocolate again", "create a brand-new substance", "change its chemical formula"],
             "answer": 0},
        ],
    },
    "chemical_changes": {
        1: [
            {"type": "true_false",
             "prompt": "A chemical change produces a new substance with different properties.",
             "choices": TRUE_FALSE, "answer": 0},
            {"type": "true_false", "prompt": "Rusting of iron is a physical change, not a chemical one.",
             "choices": TRUE_FALSE, "answer": 1},
        ],
        2: [
            {"type": "multiple_choice", "prompt": "Which of these is a common sign of a chemical change?",
             "choices": ["Bubbles, a color change, or heat being released", "Just changing shape", "Just changing size", "Just cooling down slightly"],
             "answer": 0},
            {"type": "multiple_choice", "prompt": "Which of these is an example of a chemical change?",
             "choices": ["Burning wood into ash", "Melting ice", "Cutting a rope", "Folding paper"], "answer": 0},
        ],
        3: [
            {"type": "multiple_choice",
             "prompt": "Baking a cake is a chemical change because the batter ______.",
             "choices": ["turns into a new substance that can't turn back into batter", "just changes shape", "just changes temperature", "stays exactly the same"],
             "answer": 0},
            {"type": "multiple_choice",
             "prompt": "Vinegar and baking soda mixed together fizz and produce gas. This shows a ______.",
             "choices": ["chemical change", "physical change", "no change at all", "state change only"], "answer": 0},
        ],
    },
}


def get_question(node, tier, rng):
    return rng.choice(QUESTION_BANK[node][tier])
