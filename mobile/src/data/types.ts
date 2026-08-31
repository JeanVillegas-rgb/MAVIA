export type Variant = "NORMAL" | "ELABORATED" | "SIMPLIFIED";

export type BloomLevel =
  | "remember"
  | "understand"
  | "apply"
  | "analyze"
  | "evaluate"
  | "create";

export type Difficulty = "easy" | "medium" | "hard";

export interface CourseModuleSummary {
  id: string;
  title: string;
  order: number;
  lessonNodeId: string;
  coverImage?: string; // local asset key, mock-only
}

export interface ChunkVariant {
  variant: Variant;
  narration: string;
  audioUrl: string; // "" if not generated yet
}

export interface LessonChunk {
  id: string;
  order: number;
  title: string;
  variants: Partial<Record<Variant, ChunkVariant>>;
}

export interface QuestionChoice {
  key: string;
  label: string;
}

export interface LessonQuestion {
  id: string;
  chunkId: string;
  order: number;
  format: "MCQ" | "TF";
  bloomLevel: BloomLevel;
  difficulty: Difficulty;
  questionText: string;
  choices: QuestionChoice[] | null;
  correctAnswer: string;
  explanation: string;
}

export interface LessonPackage {
  lessonNodeId: string;
  title: string;
  chunks: LessonChunk[];
  questions: LessonQuestion[];
}

export interface AnsweredQuestion {
  questionId: string;
  selected: string;
  isCorrect: boolean;
}
