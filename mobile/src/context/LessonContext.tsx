import React, { createContext, useCallback } from "react";

import {
  StartLearningResult,
  SubmitResponseResult,
  BloomType,
  VariantType,
  LessonNode,
  LessonQuestion,
  LessonChunk,
  LessonPackage,
  CourseModuleSerialized,
} from "../models/LessonPackage";
import { fetchLesson } from "../services/lessonService";

/**
 * The backend only ever returns the single-node `LessonPackage` shape
 * (see course/services.py build_package). The multi-node
 * `CourseModuleSerialized` shape is kept for type compatibility but isn't
 * produced by any current endpoint.
 */
function isLessonPackage(
  lesson: CourseModuleSerialized | LessonPackage
): lesson is LessonPackage {
  return (lesson as LessonPackage).lesson_node !== undefined;
}

/**
 * Adapts a single-node LessonPackage into the LessonNode shape the rest
 * of the app expects (variants/questions live at the node level there).
 */
function normalizePackageNode(lesson: LessonPackage): LessonNode {
  const questions: Partial<Record<BloomType, LessonQuestion[]>> = {};
  (Object.keys(lesson.questions) as BloomType[]).forEach((bloom) => {
    questions[bloom] = lesson.questions[bloom]?.map((q) => ({
      id: q.id,
      question: q.question,
      choices: q.choices,
    }));
  });

  return {
    id: lesson.lesson_node.id,
    title: lesson.lesson_node.title,
    variants: lesson.variants,
    questions,
  };
}

interface LessonContextType {
  /**
   * Server-side learning state from adaptive backend
   * Contains all adaptive logic state: mastery, bloom level, variant, current node
   */
  learningState: StartLearningResult | null;
  setLearningState: (state: StartLearningResult | null) => void;

  /**
   * Learning state ID from backend (used for submit answer requests)
   */
  learningStateId: number;

  /**
   * Current UI state for question pagination within a bloom level
   * (if there are multiple questions for the same bloom tier on a node)
   */
  currentQuestionIndex: number;
  setCurrentQuestionIndex: React.Dispatch<React.SetStateAction<number>>;

  /**
   * Current UI state for chunk pagination within the active variant's content
   */
  currentChunkIndex: number;
  setCurrentChunkIndex: React.Dispatch<React.SetStateAction<number>>;

  /**
   * Update learning state after backend submits an answer
   * Applies mastery, bloom, variant, and node changes from the response
   */
  updateFromSubmitResponse: (response: SubmitResponseResult) => Promise<void>;

  /**
   * Getters for current state (derived from learningState)
   */
  getCurrentNode: () => LessonNode | undefined;
  getCurrentChunks: () => LessonChunk[];
  getCurrentChunk: () => LessonChunk | undefined;
  getCurrentQuestions: () => LessonQuestion[];
  getCurrentBloom: () => BloomType;
  getCurrentVariant: () => VariantType;
  getCurrentMastery: () => number;
  isLearningComplete: () => boolean;
}

export const LessonContext = createContext<LessonContextType | undefined>(
  undefined
);

interface LessonProviderProps {
  children: React.ReactNode;
}

export function LessonProvider({ children }: LessonProviderProps) {
  const [learningState, setLearningState] =
    React.useState<StartLearningResult | null>(null);
  const [currentQuestionIndex, setCurrentQuestionIndex] = React.useState(0);
  const [currentChunkIndex, setCurrentChunkIndex] = React.useState(0);

  const learningStateId = learningState?.learning_state_id ?? 0;

  /**
   * Get the current lesson node being studied
   */
  const getCurrentNode = useCallback((): LessonNode | undefined => {
    if (!learningState?.lesson) return undefined;

    const { lesson } = learningState;

    if (isLessonPackage(lesson)) {
      return normalizePackageNode(lesson);
    }

    return lesson.lesson_nodes.find(
      (node) => node.id === learningState.current_node_id
    );
  }, [learningState]);

  /**
   * Get all chunks for the current node's active variant
   */
  const getCurrentChunks = useCallback((): LessonChunk[] => {
    const currentNode = getCurrentNode();
    if (!currentNode || !learningState) return [];

    const variant = learningState.current_variant;
    return currentNode.variants[variant]?.chunks ?? [];
  }, [learningState, getCurrentNode]);

  /**
   * Get the chunk at the current chunk index, bounds-checked
   */
  const getCurrentChunk = useCallback((): LessonChunk | undefined => {
    const chunks = getCurrentChunks();
    return chunks[currentChunkIndex];
  }, [getCurrentChunks, currentChunkIndex]);

  /**
   * Get all questions for the current bloom level in the current node
   */
  const getCurrentQuestions = useCallback((): LessonQuestion[] => {
    const currentNode = getCurrentNode();
    if (!currentNode || !learningState) return [];

    const bloomLevel = learningState.current_bloom;
    return currentNode.questions[bloomLevel] ?? [];
  }, [learningState, getCurrentNode]);

  /**
   * Get current bloom level from learning state
   */
  const getCurrentBloom = useCallback(
    (): BloomType => learningState?.current_bloom ?? "remember",
    [learningState]
  );

  /**
   * Get current variant (elaboration level) from learning state
   */
  const getCurrentVariant = useCallback(
    (): VariantType => learningState?.current_variant ?? "normal",
    [learningState]
  );

  /**
   * Get current mastery score from learning state
   */
  const getCurrentMastery = useCallback(
    (): number => learningState?.mastery ?? 0,
    [learningState]
  );

  /**
   * Check if the learning sequence is complete
   * (returned by backend when all nodes are mastered)
   */
  const isLearningComplete = useCallback((): boolean => {
    // This would be set by the component after receiving a SubmitResponseResult
    // with completed: true
    return false; // Implement based on your completion tracking needs
  }, []);

  /**
   * Update learning state after receiving a response from submit answer endpoint
   * This synchronizes the mobile client with the adaptive backend's state updates
   */
  const updateFromSubmitResponse = useCallback(
    async (response: SubmitResponseResult) => {
      if (!learningState) return;

      // The backend's lesson package only ever contains the current node's
      // content, so advancing to a new node requires fetching its package.
      const lesson = response.node_changed
        ? await fetchLesson(response.next_node)
        : learningState.lesson;

      // Create updated learning state with new adaptive parameters
      const updatedState: StartLearningResult = {
        ...learningState,
        learning_state_id: learningState.learning_state_id, // Keep same session
        current_node_id: response.next_node,
        mastery: response.mastery,
        current_bloom: response.next_bloom,
        current_variant: response.next_variant,
        lesson,
      };

      setLearningState(updatedState);

      // Reset question index if moving to a new node
      if (response.node_changed) {
        setCurrentQuestionIndex(0);
      }

      // Reset chunk index whenever the node OR the variant changes —
      // otherwise you could land past the end of a shorter chunk list.
      if (response.node_changed || response.next_variant !== learningState.current_variant) {
        setCurrentChunkIndex(0);
      }
    },
    [learningState]
  );

  const value: LessonContextType = {
    learningState,
    setLearningState,
    learningStateId,
    currentQuestionIndex,
    setCurrentQuestionIndex,
    currentChunkIndex,
    setCurrentChunkIndex,
    updateFromSubmitResponse,
    getCurrentNode,
    getCurrentChunks,
    getCurrentChunk,
    getCurrentQuestions,
    getCurrentBloom,
    getCurrentVariant,
    getCurrentMastery,
    isLearningComplete,
  };

  return (
    <LessonContext.Provider value={value}>{children}</LessonContext.Provider>
  );
}

/**
 * Hook to use the lesson context
 * Throws error if used outside LessonProvider
 */
export function useLessonContext(): LessonContextType {
  const context = React.useContext(LessonContext);
  if (!context) {
    throw new Error("useLessonContext must be used within LessonProvider");
  }
  return context;
}
