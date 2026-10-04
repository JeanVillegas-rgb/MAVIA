import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";

import { colors, radii, spacing } from "@/theme";

type Props = {
  title: string;
  subtitle?: string;
  leading?: React.ReactNode;
  trailing?: React.ReactNode;
  onPress: () => void;
  /** Full description a screen reader announces for this row — should say
   * more than the visible title alone (e.g. include the subtitle/duration)
   * since a sighted user gets that from layout, not an announcement. */
  accessibilityLabel: string;
  accessibilityHint?: string;
};

export default function ListRow({
  title,
  subtitle,
  leading,
  trailing,
  onPress,
  accessibilityLabel,
  accessibilityHint = "Double tap to open",
}: Props) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      accessibilityHint={accessibilityHint}
      style={({ pressed }) => [styles.row, pressed && styles.pressed]}
    >
      {leading}
      <View style={styles.text}>
        <Text style={styles.title} numberOfLines={1}>
          {title}
        </Text>
        {subtitle ? (
          <Text style={styles.subtitle} numberOfLines={1}>
            {subtitle}
          </Text>
        ) : null}
      </View>
      {trailing ?? (
        <Ionicons name="chevron-forward" size={20} color={colors.faint} />
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.md + 2,
    borderRadius: radii.sm,
    backgroundColor: colors.surface,
    minHeight: 68,
  },
  pressed: { backgroundColor: colors.brand50 },
  text: { flex: 1, gap: 3 },
  title: { fontSize: 17, fontWeight: "700", color: colors.ink, letterSpacing: -0.2 },
  subtitle: { fontSize: 13, color: colors.muted },
});
