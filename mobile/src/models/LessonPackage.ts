export type BloomType = "remember" | "understand" | "analyze";
export type VariantType = "normal" | "elaborated" | "simplified";

export interface LessonChunk {
  id: number | null;
  order: number;
  title: string;
  content: string;
}

export interface LessonVariantContent {
  chunks: LessonChunk[];
  audio_url?: string;
}

export interface SerializedLessonQuestion {
  id: number;
  question: string;
  choices: string[];
}

export interface PackageLessonQuestion {
  id: number;
  order: number;
  bloom_level: BloomType;
  question: string;
  choices: string[];
  correct_answer?: string;
}

export interface LessonNodeSerialized {
  id: number;
  title: string;
  variants: Record<VariantType, LessonVariantContent>;
  questions: Partial<Record<BloomType, SerializedLessonQuestion[]>>;
}

export interface CourseModuleSerialized {
  id: number;
  sequence_order: number;
  title: string;
  lesson_nodes: LessonNodeSerialized[];
}

export interface LessonPackage {
  module: {
    id: number;
    title: string;
    sequence_order: number;
  };
  lesson_node: {
    id: number;
    title: string;
    node_order: number | null;
  };
  variants: Record<VariantType, LessonVariantContent>;
  questions: Record<BloomType, PackageLessonQuestion[]>;
}

// Consolidated StartLearningResult — accepts either the serialized module shape or the full package.
// Also keep current_node_id optional for backward compatibility.
export interface StartLearningResult {
  learning_state_id: number;
  mastery: number;
  current_variant: VariantType;
  current_bloom: BloomType;
  lesson: CourseModuleSerialized | LessonPackage;
  current_node_id?: number;
}

export interface SubmitResponseResult {
  is_correct: boolean;
  mastery: number;
  reward: number;
  next_variant: VariantType;
  next_bloom: BloomType;
  next_node: number;
  node_changed: boolean;
  completed: boolean;
}

// Backwards-compatible aliases so existing code that imports the older (no "Serialized")
// names continues to work without changing import sites.
export type LessonNode = LessonNodeSerialized;
export type LessonQuestion = SerializedLessonQuestion;
export type CourseModule = CourseModuleSerialized;
