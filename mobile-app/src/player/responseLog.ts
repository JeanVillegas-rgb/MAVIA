// One line in the Metro console for every graded answer.
//
// The server records each answer (StudentResponse) and what the engine did
// about it (adaptive Decision). During a run-through nobody wants to query the
// database between questions, so this prints the same transition as it
// happens: what was sent, the verdict, and the command that came back.
//
// Development only: nothing is stored and nothing leaves the device.

import type { ApiAnswerResult, ApiCommand, Variant } from "@/api/client";

export type SentAnswer = {
  questionId: number;
  selectedAnswer: string;
  /** Where the learner was before the engine answered. */
  stepPosition: number;
  variant: Variant;
};

let answered = 0;

/** Call when a topic is opened, so numbering restarts with the run. */
export function resetResponseLog(): void {
  answered = 0;
}

/** What the command means, named rather than left for the reader to diff. */
export function describeCommand(from: number, command: ApiCommand): string {
  const to = command.next_step_position;
  switch (command.action) {
    case "complete":
      return "COMPLETE";
    case "regress":
      return `REGRESS to step ${to}, will resume step ${command.return_to_position}`;
    case "resume":
      return `RESUME step ${to}`;
    case "advance":
      return `ADVANCE step ${from} -> ${to}`;
    case "escalate_variant":
      return `ESCALATE to ${command.next_variant}`;
    case "next_question":
      return "NEXT QUESTION in this step";
    case "retry":
    default:
      return "SAME question again";
  }
}

export function logResponse(sent: SentAnswer, res: ApiAnswerResult): void {
  if (!__DEV__) return;
  answered += 1;
  const n = String(answered).padStart(2, "0");
  console.log(
    `[MAVIA ${n}] sent  q=${sent.questionId} answer=${sent.selectedAnswer} (step ${sent.stepPosition}, ${sent.variant})`
  );
  console.log(
    `[MAVIA ${n}] back  ${res.is_correct ? "CORRECT" : "WRONG  "} ` +
      `step=${res.next.next_step_position ?? "-"} variant=${res.next.next_variant || "-"} ` +
      `nextQ=${res.next.next_question_id ?? "-"}  :: ${describeCommand(sent.stepPosition, res.next)}`
  );
}
