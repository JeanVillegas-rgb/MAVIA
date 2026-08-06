import React, { createContext, useState } from "react";

import {
  CourseModule,
  VariantType,
  BloomType, 
} from "../models/LessonPackage";

interface LessonContextType {
  module: CourseModule | null;
  setModule: React.Dispatch<React.SetStateAction<CourseModule | null>>;

  mastery: number;
  setMastery: React.Dispatch<React.SetStateAction<number>>;

  currentVariant: VariantType;
  setCurrentVariant: React.Dispatch<React.SetStateAction<VariantType>>;

  currentBloom: BloomType;
  setCurrentBloom: React.Dispatch<React.SetStateAction<BloomType>>;

  learningStateId: number;
  setLearningStateId: React.Dispatch<React.SetStateAction<number>>;

  // Current lesson inside module.lesson_nodes
  currentLessonIndex: number;
  setCurrentLessonIndex: React.Dispatch<React.SetStateAction<number>>;

  // Current question inside the selected bloom level
  currentQuestionIndex: number;
  setCurrentQuestionIndex: React.Dispatch<React.SetStateAction<number>>;

  // Backend LessonNode id (used when submitting answers)
  currentLessonNodeId: number;
  setCurrentLessonNodeId: React.Dispatch<React.SetStateAction<number>>;
}

export const LessonContext = createContext({} as LessonContextType);

export function LessonProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [module, setModule] = useState<CourseModule | null>(null);

  const [mastery, setMastery] = useState(0.3);

  const [currentVariant, setCurrentVariant] =
    useState<VariantType>("normal");

  const [currentBloom, setCurrentBloom] =
    useState<BloomType>("remember");

  const [learningStateId, setLearningStateId] = useState(0);

  const [currentLessonIndex, setCurrentLessonIndex] = useState(0);

  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);

  const [currentLessonNodeId, setCurrentLessonNodeId] = useState(0);

  return (
    <LessonContext.Provider
      value={{
        module,
        setModule,

        mastery,
        setMastery,

        currentVariant,
        setCurrentVariant,

        currentBloom,
        setCurrentBloom,

        learningStateId,
        setLearningStateId,

        currentLessonIndex,
        setCurrentLessonIndex,

        currentQuestionIndex,
        setCurrentQuestionIndex,

        currentLessonNodeId,
        setCurrentLessonNodeId,
      }}
    >
      {children}
    </LessonContext.Provider>
  );
}