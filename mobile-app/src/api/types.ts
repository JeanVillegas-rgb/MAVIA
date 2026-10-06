// The audio course API's shapes (backend: mobile_course_package), on their
// own so that modules which only need types -- src/player/traversal.ts --
// never pull in the network client, its config or AsyncStorage.

export type ApiCourse = {
  id: number;
  title: string;
  topic_count: number;
  completed_topic_count: number;
};

export type ApiOutlineTopic = { id: number; title: string };
export type ApiOutlineModule = { id: number; title: string; topics: ApiOutlineTopic[] };
export type ApiCourseOutline = { id: number; title: string; modules: ApiOutlineModule[] };

// The three ways one concept is explained. Standard is the teacher's own
// wording; the engine escalates to the others after repeated misses.
export type Variant = "standard" | "simplified" | "elaborated";

export type ApiPackageQuestion = {
  id: number;
  text: string;
  format: "MCQ" | "TF" | string;
  // MCQ choices arrive as a list or as {"A": ..., "B": ...}; null for True/False.
  choices: string[] | Record<string, string> | null;
  thinking_order: "LOT" | "HOT" | string;
};

// One concept of the topic's published learning path, audio only.
export type ApiPackageStep = {
  position: number;
  concept_id: number;
  prerequisites: number[];
  // Each explanation type as its audio clips, in play order. A type with no
  // audio is left out entirely.
  versions: Partial<Record<Variant, string[]>>;
  // Empty for a listen-only step (e.g. the guide's introduction).
  questions: ApiPackageQuestion[];
  // Spare questions from the concept's bank. Only ever asked after a missed
  // True/False, which is never asked again (the other answer would be certain).
  reserve_questions?: ApiPackageQuestion[];
  // Each question's recorded audio, by question id. A question with no clip
  // is left out and read by the device voice instead.
  question_audio?: Record<string, string>;
};

export type ApiProgress = {
  current_step_position: number;
  current_variant: Variant;
  return_to_position: number | null;
  completed: boolean;
  updated_at: string;
};

export type ApiTopicPackage = {
  topic: { id: number; title: string };
  steps: ApiPackageStep[];
  progress: ApiProgress;
  // Which question of the current step to ask after listening; null when
  // nothing is left to ask there (listen, then continue).
  next_question_id: number | null;
};

export type ApiAction =
  | "next_question"
  | "retry"
  | "escalate_variant"
  | "regress"
  | "resume"
  | "advance"
  | "complete";

// A question the student missed in the segment they just finished and never got
// right: read to them, with its answer and why, before the next part plays.
export type ApiReviewItem = {
  question_id: number;
  question: string;
  answer: string;
  explanation: string;
};

// What the engine decided. The player only follows it.
export type ApiCommand = {
  action: ApiAction;
  next_step_position: number | null;
  next_variant: Variant | "";
  next_question_id: number | null;
  return_to_position: number | null;
  play_audio: boolean;
  // Filled when the command leaves a finished segment (advance, resume, complete).
  review?: ApiReviewItem[];
};

export type ApiAnswerResult = {
  question_id: number;
  is_correct: boolean;
  explanation: string;
  next: ApiCommand;
};

// The shape the player's audio controls render: one clip of narration.
export type ApiTrack = {
  id: string;
  order: number;
  title: string;
  type: string;
  audio_url: string;
  audio_ready: boolean;
  text: string;
};
