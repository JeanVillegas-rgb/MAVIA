"""Flask front-end for the MAVIA adaptive-engine POC.

Serves one question at a time and drives the same DAG/BKT/Bloom pipeline as
engine.py, but the "student" is a real person clicking through the browser
instead of student_sim.py's simulated learner.
"""

import random

from flask import Flask, jsonify, render_template, request, session

from bloom import QUESTION_TYPE, TIER_NAMES, route_for_mastery
from bkt import update_mastery
from dag import NODES, PREREQUISITES, unlocked_nodes
from engine import LearnerState, apply_routing, pick_next_node
from questions import QUESTION_BANK, get_question

app = Flask(__name__)
app.secret_key = "mavia-poc-dev-key"


def label(node):
    return node.replace("_", " ").title()


def load_state():
    state = LearnerState()
    state.mastery = session.get("mastery", {})
    state.tier = session.get("tier", {})
    state.mastered = set(session.get("mastered", []))
    return state


def save_state(state):
    session["mastery"] = state.mastery
    session["tier"] = state.tier
    session["mastered"] = list(state.mastered)


def node_status_list(state):
    unlocked = set(unlocked_nodes(state.mastered))
    nodes = []
    for node in NODES:
        if node in state.mastered:
            status = "mastered"
        elif node in unlocked:
            status = "unlocked"
        else:
            status = "locked"
        nodes.append({
            "id": node,
            "label": label(node),
            "status": status,
            "tier": state.tier_of(node),
            "tier_name": TIER_NAMES[state.tier_of(node)],
            "mastery": round(state.mastery_of(node), 3),
            "prereqs": [label(p) for p in PREREQUISITES[node]],
        })
    return nodes


def ensure_current_question(state):
    """Picks the next node/question if one isn't already pending in session."""
    if session.get("current_question"):
        return session["current_question"]

    node = pick_next_node(state, state.mastered)
    if node is None:
        return None

    tier = state.tier_of(node)
    rng = random.Random()
    q = get_question(node, tier, rng)
    qidx = QUESTION_BANK[node][tier].index(q)

    current = {
        "node": node,
        "tier": tier,
        "qidx": qidx,
        "prompt": q["prompt"],
        "choices": q["choices"],
        "type": q["type"],
        "tier_name": TIER_NAMES[tier],
    }
    session["current_question"] = current
    return current


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    state = load_state()
    current = ensure_current_question(state)
    save_state(state)

    complete = current is None
    return jsonify({
        "nodes": node_status_list(state),
        "mastered_count": len(state.mastered),
        "total": len(NODES),
        "current_question": current,
        "complete": complete,
    })


@app.route("/api/answer", methods=["POST"])
def api_answer():
    current = session.get("current_question")
    if not current:
        return jsonify({"error": "no active question"}), 400

    choice_index = request.get_json(force=True).get("choice_index")
    node, tier = current["node"], current["tier"]
    correct_index = QUESTION_BANK[node][tier][current["qidx"]]["answer"]
    correct = choice_index == correct_index

    state = load_state()
    prior = state.mastery_of(node)
    posterior = update_mastery(prior, correct)
    state.mastery[node] = posterior

    decision = route_for_mastery(posterior)
    tier_before = tier
    became_mastered = apply_routing(node, tier, decision, state)
    tier_after = state.tier_of(node)

    session.pop("current_question", None)
    save_state(state)

    return jsonify({
        "node": node,
        "node_label": label(node),
        "correct": correct,
        "correct_index": correct_index,
        "prior": round(prior, 3),
        "posterior": round(posterior, 3),
        "decision": decision,
        "tier_before": tier_before,
        "tier_before_name": TIER_NAMES[tier_before],
        "tier_after": tier_after,
        "tier_after_name": TIER_NAMES[tier_after],
        "mastered": became_mastered,
    })


@app.route("/api/reset", methods=["POST"])
def api_reset():
    session.clear()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True)
