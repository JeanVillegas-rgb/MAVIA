import React, { createContext, useState } from "react";

import {
  LessonPackage,
  VariantType,
  BloomType, 
} from "../models/LessonPackage";

interface LessonContextType {
  lesson: LessonPackage | null;
  setLesson: React.Dispatch<React.SetStateAction<LessonPackage | null>>;

  mastery: number;
  setMastery: React.Dispatch<React.SetStateAction<number>>;

  currentVariant: VariantType;
  setCurrentVariant: React.Dispatch<React.SetStateAction<VariantType>>;

  currentBloom: BloomType;
  setCurrentBloom: React.Dispatch<React.SetStateAction<BloomType>>;

  learningStateId: number;
  setLearningStateId: React.Dispatch<React.SetStateAction<number>>;

  currentQuestionIndex: number;
  setCurrentQuestionIndex: React.Dispatch<React.SetStateAction<number>>;

  currentNodeId: number;
  setCurrentNodeId: React.Dispatch<React.SetStateAction<number>>;
}

export const LessonContext = createContext({} as LessonContextType);

export function LessonProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [lesson, setLesson] = useState<LessonPackage | null>(null);
  const [mastery, setMastery] = useState(0.3);
  const [currentVariant, setCurrentVariant] =
    useState<VariantType>("normal");
  const [currentBloom, setCurrentBloom] =
    useState<BloomType>("remember");
  const [learningStateId, setLearningStateId] = useState(0);
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);
  const [currentNodeId, setCurrentNodeId] = useState(0);

  return (
    <LessonContext.Provider
      value={{
        lesson,
        setLesson,
        mastery,
        setMastery,
        currentVariant,
        setCurrentVariant,
        currentBloom,
        setCurrentBloom,
        learningStateId,
        setLearningStateId,
        currentQuestionIndex,
        setCurrentQuestionIndex,
        currentNodeId,
        setCurrentNodeId,
      }}
    >
      {children}
    </LessonContext.Provider>
  );
}
