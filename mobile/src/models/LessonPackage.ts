export type BloomType = "remember" | "understand" | "analyze";

export type VariantType = "normal" | "elaborated" | "simplified";

export interface LessonVariantContent {
  text: string;
  audio_url?: string;
}

export interface LessonQuestion {
  id: number;
  question: string;
  choices: string[];
}

export interface LessonNode {
  id: number;
  title: string;
  variants: Record<VariantType, LessonVariantContent>;
  questions: Partial<Record<BloomType, LessonQuestion[]>>;
}

export interface CourseModule {
  id: number;
  sequence_order: number;
  title: string;
  lesson_nodes: LessonNode[];
}

export interface StartLearningResult {
  learning_state_id: number;
  mastery: number;
  current_variant: VariantType;
  current_bloom: BloomType;
  lesson: CourseModule;
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