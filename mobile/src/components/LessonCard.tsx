import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { colors, fonts, radii, shadow, spacing } from "@/theme";

type Props = {
  title: string;
  onPress: () => void;
  emoji?: string;
};

export default function LessonCard({ title, onPress, emoji }: Props) {
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.card, shadow.card, pressed && styles.pressed]}
    >
      <Text style={styles.title}>{title}</Text>
      {emoji && (
        <View style={styles.emojiWrap}>
          <Text style={styles.emoji}>{emoji}</Text>
        </View>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    flex: 1,
    minHeight: 140,
    backgroundColor: colors.card,
    borderRadius: radii.card,
    borderWidth: 1,
    borderColor: colors.cardBorder,
    padding: spacing.md,
    justifyContent: "space-between",
  },
  pressed: {
    opacity: 0.85,
    transform: [{ scale: 0.99 }],
  },
  title: {
    fontFamily: fonts.displaySemi,
    fontSize: 15,
    color: colors.ink,
    lineHeight: 20,
  },
  emojiWrap: {
    alignSelf: "flex-end",
  },
  emoji: {
    fontSize: 32,
  },
});
