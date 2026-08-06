export type BloomType = "remember" | "understand" | "analyze";

export type VariantType = "normal" | "elaborated" | "simplified";

export interface LessonVariantContent {
  text: string;
  // backend uses `audio_url` (snake_case) in its JSON responses
  audio_url?: string;
}

// Question shape used by the student-facing serializer (correct_answer omitted)
export interface SerializedLessonQuestion {
  id: number;
  question: string;
  choices: string[];
}

// Question shape returned by the server-side LessonPackageService (includes extra fields)
export interface PackageLessonQuestion {
  id: number;
  order: number;
  bloom_level: BloomType;
  question: string;
  choices: string[];
  correct_answer?: string;
}

// Matches the LessonNodeSerializer / CourseModuleSerializer output
export interface LessonNodeSerialized {
  id: number;
  title: string;
  variants: Record<VariantType, LessonVariantContent>;
  // serializer groups questions by bloom level and intentionally omits correct_answer
  questions: Partial<Record<BloomType, SerializedLessonQuestion[]>>;
}

export interface CourseModuleSerialized {
  id: number;
  sequence_order: number;
  title: string;
  lesson_nodes: LessonNodeSerialized[];
}

// Matches the object returned by LessonPackageService.build_package (LessonPackageView / FirstLessonView)
export interface LessonPackage {
  module: {
    id: number;
    title: string;
    sequence_order: number;
  };
  lesson_node: {
    id: number;
    title: string;
    // backend uses `node_order` for the source order value
    node_order: number | null;
  };
  // variants mapping (normal/elaborated/simplified)
  variants: Record<VariantType, LessonVariantContent>;
  // questions mapping (remember/understand/analyze) with server-side grading fields
  questions: Record<BloomType, PackageLessonQuestion[]>;
}

// Legacy / other endpoints (if you use start-learning / submit endpoints) — keep these aligned
export interface StartLearningResult {
  learning_state_id: number;
  mastery: number;
  current_variant: VariantType;
  current_bloom: BloomType;
  lesson: CourseModuleSerialized;
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
