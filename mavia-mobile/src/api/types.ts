export type LessonVariantKey = "normal" | "elaborated" | "simplified";

export type ChunkVariant = {
  text: string;
  audio_url: string;
};

export type LessonChunk = {
  id: number;
  order: number;
  title: string;
  variants: Partial<Record<LessonVariantKey, ChunkVariant>>;
};

export type QuestionChoice = {
  key: string;
  label: string;
};

export type QuestionFormat = "MCQ" | "TF";

export type LessonQuestion = {
  id: number;
  chunk_id: number;
  order: number;
  format: QuestionFormat;
  bloom_level: string;
  difficulty: string;
  question_text: string;
  choices: QuestionChoice[] | null;
  correct_answer: string;
  explanation: string;
};

export type ModuleInfo = {
  id: number;
  title: string;
  sequence_order: number;
};

export type LessonNodeInfo = {
  id: number;
  title: string;
  node_order: number;
};

export type LessonPackage = {
  module: ModuleInfo;
  lesson_node: LessonNodeInfo;
  chunks: LessonChunk[];
  questions: LessonQuestion[];
};

export type ModuleSummary = {
  id: number;
  title: string;
  sequence_order: number;
  lesson_nodes: LessonNodeInfo[];
};

export type StartLearningResponse = {
  learning_state_id: number;
  current_node_id: number;
  current_question: number | null;
  mastery: number;
  current_variant: LessonVariantKey;
  current_bloom: string;
  lesson: LessonPackage;
};

export type SubmitResponseResult = {
  is_correct: boolean;
  mastery: number;
  reward: number;
  next_node: number;
  node_changed: boolean;
  next_chunk: number | null;
  chunk_changed: boolean;
  next_variant: "NORMAL" | "ELABORATED" | "SIMPLIFIED";
  next_bloom: string;
  next_question: number | null;
  completed: boolean;
};

export type LearningStateRecord = {
  id: number;
  learner_id: string;
  current_module: number;
  current_node: number;
  current_question: number | null;
  mastery: number;
  attempts: number;
  tier_attempts: number;
  current_variant: "NORMAL" | "ELABORATED" | "SIMPLIFIED";
  current_bloom: string;
  reward: number;
  completed: boolean;
  last_updated: string;
};

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}
