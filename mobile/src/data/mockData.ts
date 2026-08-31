import { CourseModuleSummary, LessonPackage } from "./types";

export const MODULES: CourseModuleSummary[] = [
  { id: "m1", title: "Living and Non-living Things", order: 1, lessonNodeId: "ln1" },
  { id: "m2", title: "Plants and Animals", order: 2, lessonNodeId: "ln2", coverImage: "plantsAnimals" },
  { id: "m3", title: "Weather and Seasons", order: 3, lessonNodeId: "ln3" },
  { id: "m4", title: "Caring for the environment", order: 4, lessonNodeId: "ln4" },
];

export const LESSON_PACKAGES: Record<string, LessonPackage> = {
  ln2: {
    lessonNodeId: "ln2",
    title: "Plants and Animals",
    chunks: [
      {
        id: "c1",
        order: 1,
        title: "What Living Things Need",
        variants: {
          NORMAL: {
            variant: "NORMAL",
            narration:
              "Plants and animals are both living things. They need food, water, air, and space to grow.",
            audioUrl: "",
          },
          SIMPLIFIED: {
            variant: "SIMPLIFIED",
            narration: "Plants and animals are alive. They need food, water, and air.",
            audioUrl: "",
          },
          ELABORATED: {
            variant: "ELABORATED",
            narration:
              "Plants and animals are both classified as living things because they share key life processes. Every living thing needs food for energy, water to survive, air to breathe, and space to grow and move.",
            audioUrl: "",
          },
        },
      },
      {
        id: "c2",
        order: 2,
        title: "How Plants Are Different From Animals",
        variants: {
          NORMAL: {
            variant: "NORMAL",
            narration:
              "Plants make their own food using sunlight. Animals cannot make their own food, so they eat plants or other animals.",
            audioUrl: "",
          },
          SIMPLIFIED: {
            variant: "SIMPLIFIED",
            narration: "Plants make food from sunlight. Animals eat plants or other animals.",
            audioUrl: "",
          },
        },
      },
      {
        id: "c3",
        order: 3,
        title: "Where Animals Live",
        variants: {
          NORMAL: {
            variant: "NORMAL",
            narration:
              "Animals live in habitats that give them what they need — forests, oceans, deserts, and grasslands are all examples.",
            audioUrl: "",
          },
        },
      },
    ],
    questions: [
      {
        id: "q1",
        chunkId: "c1",
        order: 1,
        format: "TF",
        bloomLevel: "remember",
        difficulty: "easy",
        questionText: "Plants and animals both need water to survive.",
        choices: null,
        correctAnswer: "true",
        explanation: "All living things need water, food, air, and space to grow.",
      },
      {
        id: "q2",
        chunkId: "c2",
        order: 2,
        format: "MCQ",
        bloomLevel: "understand",
        difficulty: "medium",
        questionText: "How do plants get their food?",
        choices: [
          { key: "a", label: "By eating other plants" },
          { key: "b", label: "By using sunlight" },
          { key: "c", label: "By eating animals" },
          { key: "d", label: "They do not need food" },
        ],
        correctAnswer: "b",
        explanation: "Plants make their own food using sunlight, a process called photosynthesis.",
      },
      {
        id: "q3",
        chunkId: "c3",
        order: 3,
        format: "MCQ",
        bloomLevel: "apply",
        difficulty: "medium",
        questionText: "Which of these is an example of a habitat?",
        choices: [
          { key: "a", label: "A backpack" },
          { key: "b", label: "A forest" },
          { key: "c", label: "A calculator" },
          { key: "d", label: "A calendar" },
        ],
        correctAnswer: "b",
        explanation: "A habitat is a natural home that gives an animal what it needs — like a forest.",
      },
    ],
  },
};
