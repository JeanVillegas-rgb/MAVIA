import React, { useContext, useState, useEffect } from "react";
import {
  View,
  Text,
  Alert,
  ScrollView,
  Pressable,
  StyleSheet,
  SafeAreaView,
} from "react-native";
import * as Speech from "expo-speech";
import { colors, spacing, typography, radii } from "../theme/theme";
import PrimaryButton from "../components/PrimaryButton";
import BloomProgress from "../components/BloomProgress";
import HiddenHardwareInput from "../components/HiddenHardwareInput";

import { LessonContext } from "../context/LessonContext";
import { BloomType, CourseModule } from "../models/LessonPackage";
import api from "../services/api";

const BLOOM_ORDER: BloomType[] = ["remember", "understand", "analyze"];

// Physical keys on the BOW HW157 numpad mapped to answer choices A-D.
// 7/8/9/+ sit adjacent to each other on a standard numpad layout, so
// they're findable by feel without looking at the device.
const NUMPAD_KEY_TO_ANSWER: Record<string, string> = {
  "7": "A",
  "8": "B",
  "9": "C",
  "+": "D",
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },

  content: {
    flexGrow: 1,
    padding: spacing.lg,
    paddingBottom: spacing.xxl,
  },

  scroll: {
    flex: 1,
  },

  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
    marginVertical: spacing.lg,
  },

  choices: {
    marginTop: spacing.lg,
    marginBottom: spacing.lg,
  },

  choice: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    marginBottom: spacing.sm,
  },

  choiceSelected: {
    borderColor: colors.primary,
    backgroundColor: colors.primaryMuted,
  },

  choiceLetter: {
    ...typography.body,
    color: colors.primary,
    fontWeight: "700",
    marginRight: spacing.sm,
  },

  choiceText: {
    ...typography.body,
    flex: 1,
  },
});

function nextBloomInOrder(bloom: BloomType): BloomType {
  const idx = BLOOM_ORDER.indexOf(bloom);
  if (idx === -1 || idx === BLOOM_ORDER.length - 1) {
    return bloom;
  }
  return BLOOM_ORDER[idx + 1];
}

export default function QuestionScreen({ navigation }: any) {
  const {
    module,
    setModule,
    setMastery,
    currentVariant,
    setCurrentVariant,
    currentBloom,
    setCurrentBloom,
    learningStateId,
    currentLessonIndex,
    setCurrentLessonIndex,
    currentQuestionIndex,
    setCurrentQuestionIndex,
    setCurrentLessonNodeId,
  } = useContext(LessonContext);

  const [selectedAnswer, setSelectedAnswer] = useState("");

  const lesson = module?.lesson_nodes[currentLessonIndex];
  const questions = lesson?.questions?.[currentBloom] || [];
  const question = questions[currentQuestionIndex];
  const answerLabels = ["A", "B", "C", "D"];

  useEffect(() => {
    if (!question) return;
    speakQuestion();
    return () => {
      Speech.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentQuestionIndex, currentBloom, question?.id]);

  if (!module || !lesson || !lesson.questions || !lesson.questions[currentBloom]) {
    return null;
  }

  if (!question) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.content}>
          <Text style={typography.title}>No questions yet</Text>
          <View style={styles.card}>
            <Text style={typography.body}>
              Generate questions for this topic in the teacher web app before
              continuing the mobile adaptive flow.
            </Text>
          </View>
          <PrimaryButton
            label="Back to lesson"
            onPress={() => navigation.replace("Lesson")}
            variant="outline"
          />
        </View>
      </SafeAreaView>
    );
  }

  function speakQuestion() {
    Speech.stop();
    const choiceLines = question.choices
      .map((choice: string, i: number) => `Option ${answerLabels[i]}: ${choice}.`)
      .join(" ");

    Speech.speak(`${question.question}. ${choiceLines}`, { rate: 0.95 });
  }

  // Handles a single keystroke from the hardware numpad.
  function handleHardwareKey(char: string) {
    const answer = NUMPAD_KEY_TO_ANSWER[char];
    if (answer) {
      setSelectedAnswer(answer);
    }
    // Any other key (Backspace, other digits, etc.) is ignored.
  }

  async function submitAnswer() {
    if (!selectedAnswer) {
      Alert.alert("Select an answer first.");
      return;
    }

    try {
      const response = await api.post("adaptive/submit-response/", {
        learning_state_id: learningStateId,
        question_id: question.id,
        selected_answer: selectedAnswer,
        response_time: 3.5
      });

      Speech.stop();
      setMastery(response.data.mastery);

      const targetBloomKey = response.data.next_bloom.toLowerCase() as BloomType;

      // 1. Total completion check
      if (response.data.completed) {
        setSelectedAnswer("");
        setCurrentQuestionIndex(0);
        navigation.replace("Completion");
        return;
      }

      // 2. Node progression check
      if (response.data.node_changed) {
        try {
          const nextNodeId = response.data.next_node;
          const packageResponse = await api.get(`course/lesson-package/${nextNodeId}/`);
          const newModule: CourseModule = packageResponse.data;

          const nodeIndex = newModule.lesson_nodes.findIndex(
            (n) => n.id === nextNodeId
          );

          setSelectedAnswer("");
          setModule(newModule);
          setCurrentLessonIndex(nodeIndex >= 0 ? nodeIndex : 0);
          setCurrentLessonNodeId(nextNodeId);
          setCurrentQuestionIndex(0);
          setCurrentVariant(response.data.next_variant.toLowerCase());
          setCurrentBloom(targetBloomKey);

          navigation.replace("Lesson");
        } catch (fetchErr) {
          console.log(fetchErr);
          Alert.alert("Unable to load the next lesson.");
        }
        return;
      }

      // 3. Incorrect answer flow
      if (!response.data.is_correct) {
        setSelectedAnswer("");
        setCurrentVariant(response.data.next_variant.toLowerCase());
        setCurrentBloom(targetBloomKey);

        navigation.replace("Lesson");
        return;
      }

      // 4. Correct answer flow
      setSelectedAnswer("");
      const backendEscalated = targetBloomKey !== currentBloom;
      const nextIndex = currentQuestionIndex + 1;
      const moreQuestionsInTier = nextIndex < questions.length;

      if (backendEscalated) {
        setCurrentVariant(response.data.next_variant.toLowerCase());
        setCurrentBloom(targetBloomKey);
        setCurrentQuestionIndex(0);
        navigation.replace("Lesson");
      } else if (moreQuestionsInTier) {
        setCurrentVariant(response.data.next_variant.toLowerCase());
        setCurrentQuestionIndex(nextIndex);
      } else {
        const forcedBloom = nextBloomInOrder(currentBloom);
        setCurrentVariant(response.data.next_variant.toLowerCase());
        setCurrentBloom(forcedBloom);
        setCurrentQuestionIndex(0);
        navigation.replace("Lesson");
      }
    } catch (err) {
      console.log(err);
      Alert.alert("Unable to submit answer.");
    }
  }

 return (
  <SafeAreaView style={styles.container}>
    <HiddenHardwareInput
      onKey={handleHardwareKey}
      onSubmit={submitAnswer}
    />

    <ScrollView
      style={styles.scroll}
      contentContainerStyle={styles.content}
      keyboardShouldPersistTaps="handled"
    >
      <Text style={typography.title}>Question</Text>

      <BloomProgress currentBloom={currentBloom} />

      <View style={styles.card}>
        <Text style={typography.body}>{question.question}</Text>
      </View>

      <PrimaryButton
        label="Replay question"
        onPress={speakQuestion}
        variant="outline"
        accessibilityHint="Plays the question narration again"
      />

      <View style={styles.choices}>
        {question.choices.map((choice: string, index: number) => {
          const label = answerLabels[index];

          return (
            <Pressable
              key={index}
              onPress={() => setSelectedAnswer(label)}
              style={[
                styles.choice,
                selectedAnswer === label && styles.choiceSelected,
              ]}
            >
              <Text style={styles.choiceLetter}>{label}.</Text>

              <Text style={styles.choiceText}>{choice}</Text>
            </Pressable>
          );
        })}
      </View>

      <PrimaryButton
        label="Submit Answer"
        onPress={submitAnswer}
      />
    </ScrollView>
  </SafeAreaView>
);
}