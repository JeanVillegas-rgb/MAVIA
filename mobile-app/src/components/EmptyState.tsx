import React from "react";
import { StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";

import { colors, spacing } from "@/theme";

type Props = {
  icon: keyof typeof Ionicons.glyphMap;
  title: string;
  body?: string;
};

// Shown wherever a list has nothing to show yet — deliberately not filled
// with placeholder/mock content, so this stands in until the real data is
// wired up.
export default function EmptyState({ icon, title, body }: Props) {
  return (
    <View
      style={styles.wrap}
      accessible
      accessibilityRole="text"
      accessibilityLabel={body ? `${title}. ${body}` : title}
    >
      <Ionicons name={icon} size={28} color={colors.faint} />
      <Text style={styles.title}>{title}</Text>
      {body ? <Text style={styles.body}>{body}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    alignItems: "center",
    paddingVertical: spacing.xl + spacing.md,
    paddingHorizontal: spacing.lg,
    gap: 10,
  },
  title: { fontSize: 19, fontWeight: "700", color: colors.ink, textAlign: "center" },
  body: { fontSize: 15, lineHeight: 22, color: colors.muted, textAlign: "center" },
});
