import React, { createContext, useCallback } from "react";

import {
  StartLearningResult,
  SubmitResponseResult,
  BloomType,
  VariantType,
  LessonNode,
  LessonQuestion,
} from "../models/LessonPackage";

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
   * Update learning state after backend submits an answer
   * Applies mastery, bloom, variant, and node changes from the response
   */
  updateFromSubmitResponse: (response: SubmitResponseResult) => void;

  /**
   * Getters for current state (derived from learningState)
   */
  getCurrentNode: () => LessonNode | undefined;
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

  const learningStateId = learningState?.learning_state_id ?? 0;

  /**
   * Get the current lesson node being studied
   */
  const getCurrentNode = useCallback((): LessonNode | undefined => {
    if (!learningState?.lesson) return undefined;
    
    // Find the node with matching ID in the lesson nodes array
    return learningState.lesson.lesson_nodes.find(
      (node) => node.id === learningState.current_node_id
    );
  }, [learningState]);

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
    (response: SubmitResponseResult) => {
      if (!learningState) return;

      // Create updated learning state with new adaptive parameters
      const updatedState: StartLearningResult = {
        ...learningState,
        learning_state_id: learningState.learning_state_id, // Keep same session
        current_node_id: response.next_node,
        mastery: response.mastery,
        current_bloom: response.next_bloom,
        current_variant: response.next_variant,
        lesson: learningState.lesson, // Module stays the same unless we fetch new one
      };

      setLearningState(updatedState);

      // Reset question index if moving to a new node
      if (response.node_changed) {
        setCurrentQuestionIndex(0);
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
    updateFromSubmitResponse,
    getCurrentNode,
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
