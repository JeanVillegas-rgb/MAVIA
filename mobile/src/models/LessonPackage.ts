// Types matching the shape returned by GET course/lesson-package/:nodeId/
// and referenced throughout LessonScreen / QuestionScreen / LessonContext.
//
// NOTE: `variants[x].text` assumes your serializer exposes the
// LessonVariant.narration field under the key "text". If your API
// actually returns "narration", either rename it in the serializer or
// swap `.text` for `.narration` in LessonScreen.tsx -- just keep it
// consistent with whichever this type says.

export type BloomType = "remember" | "understand" | "analyze";

export type VariantType = "normal" | "elaborated" | "simplified";

export interface LessonNodeInfo {
  id: number;
  title: string;
}

export interface LessonVariantContent {
  text: string;
  audio_url?: string;
}

export interface LessonQuestion {
  id: number;
  question: string;
  choices: string[]; 
}

export interface LessonPackage {
  lesson_node: LessonNodeInfo;
  variants: Record<VariantType, LessonVariantContent>;
  questions: Record<BloomType, LessonQuestion[]>;
}

export interface SubmitResponseResult {
  is_correct: boolean;
  mastery: number;
  reward: number;
  next_variant: string;
  next_bloom: string;
  next_node: number;
  node_changed: boolean;
  completed: boolean;
}

export interface StartLearningResult {
  learning_state_id: number;
  current_node_id: number;
  mastery: number;
  current_variant: VariantType;
  current_bloom: BloomType;
  lesson: LessonPackage;
}
