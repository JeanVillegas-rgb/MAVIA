const questionPanel = document.getElementById("question-panel");
const feedbackPanel = document.getElementById("feedback-panel");
const completePanel = document.getElementById("complete-panel");

const tierBadge = document.getElementById("tier-badge");
const nodeBadge = document.getElementById("node-badge");
const promptEl = document.getElementById("prompt");
const choicesEl = document.getElementById("choices");

const feedbackResult = document.getElementById("feedback-result");
const masteryBefore = document.getElementById("mastery-before");
const masteryAfter = document.getElementById("mastery-after");
const masteryValues = document.getElementById("mastery-values");
const decisionBadge = document.getElementById("decision-badge");
const masteredBanner = document.getElementById("mastered-banner");
const continueBtn = document.getElementById("continue-btn");
const restartBtn = document.getElementById("restart-btn");

const nodeListEl = document.getElementById("node-list");
const overallFill = document.getElementById("overall-fill");
const overallLabel = document.getElementById("overall-label");

const STATUS_ICON = { locked: "\u{1F512}", unlocked: "\u{1F4D8}", mastered: "\u{2705}" };

function renderSidebar(data) {
  nodeListEl.innerHTML = "";
  data.nodes.forEach((node) => {
    const li = document.createElement("li");
    li.className = `node-item status-${node.status}`;

    const meta = node.status === "locked"
      ? `Requires: ${node.prereqs.join(", ") || "—"}`
      : node.status === "mastered"
        ? "Mastered"
        : `Tier ${node.tier} · ${node.tier_name}`;

    li.innerHTML = `
      <div class="node-top">
        <span class="node-name">${node.label}</span>
        <span class="node-icon">${STATUS_ICON[node.status]}</span>
      </div>
      <div class="node-meta">${meta}</div>
      <div class="node-bar"><div class="node-bar-fill" style="width:${Math.round(node.mastery * 100)}%"></div></div>
    `;
    nodeListEl.appendChild(li);
  });

  overallFill.style.width = `${(data.mastered_count / data.total) * 100}%`;
  overallLabel.textContent = `${data.mastered_count} / ${data.total} concepts mastered`;
}

function renderQuestion(q) {
  feedbackPanel.classList.add("hidden");
  completePanel.classList.add("hidden");
  questionPanel.classList.remove("hidden");

  tierBadge.textContent = `Tier ${q.tier} · ${q.tier_name}`;
  nodeBadge.textContent = q.node.replace(/_/g, " ");
  promptEl.textContent = q.prompt;

  choicesEl.innerHTML = "";
  q.choices.forEach((choice, i) => {
    const btn = document.createElement("button");
    btn.className = "choice-btn";
    btn.textContent = choice;
    btn.onclick = () => submitAnswer(i, btn);
    choicesEl.appendChild(btn);
  });
}

async function submitAnswer(choiceIndex, btnEl) {
  [...choicesEl.children].forEach((b) => (b.disabled = true));

  const res = await fetch("/api/answer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ choice_index: choiceIndex }),
  });
  const result = await res.json();

  btnEl.classList.add(result.correct ? "correct" : "incorrect");
  if (!result.correct) {
    choicesEl.children[result.correct_index].classList.add("correct");
  }

  setTimeout(() => renderFeedback(result), 700);
}

function renderFeedback(result) {
  questionPanel.classList.add("hidden");
  feedbackPanel.classList.remove("hidden");

  feedbackResult.textContent = result.correct ? "Correct!" : "Not quite.";
  feedbackResult.className = `feedback-result ${result.correct ? "correct" : "incorrect"}`;

  masteryBefore.style.width = `${result.prior * 100}%`;
  requestAnimationFrame(() => {
    masteryAfter.style.width = `${result.posterior * 100}%`;
  });
  masteryValues.textContent = `${Math.round(result.prior * 100)}% → ${Math.round(result.posterior * 100)}% mastery on ${result.node_label}`;

  decisionBadge.textContent = {
    escalate: `Escalating to ${result.tier_after_name}`,
    deescalate: `Stepping back to ${result.tier_after_name}`,
    stagnate: `Staying at ${result.tier_after_name}`,
  }[result.decision];
  decisionBadge.className = `badge decision-badge ${result.decision}`;

  masteredBanner.classList.toggle("hidden", !result.mastered);
}

async function loadState() {
  const res = await fetch("/api/state");
  const data = await res.json();
  renderSidebar(data);

  if (data.complete) {
    questionPanel.classList.add("hidden");
    feedbackPanel.classList.add("hidden");
    completePanel.classList.remove("hidden");
  } else {
    renderQuestion(data.current_question);
  }
}

continueBtn.addEventListener("click", loadState);
restartBtn.addEventListener("click", async () => {
  await fetch("/api/reset", { method: "POST" });
  loadState();
});

loadState();
