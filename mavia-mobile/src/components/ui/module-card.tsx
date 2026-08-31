import { Pressable, StyleSheet, Text, View } from "react-native";

import { colors, fonts, radii, spacing } from "@/theme";

type ModuleCardProps = {
  title: string;
  emoji: string;
  selected?: boolean;
  locked?: boolean;
  onPress?: () => void;
};

export function ModuleCard({ title, emoji, selected, locked, onPress }: ModuleCardProps) {
  return (
    <Pressable
      onPress={locked ? undefined : onPress}
      style={[styles.card, selected && styles.cardSelected, locked && styles.cardLocked]}>
      <View style={styles.emojiBadge}>
        <Text style={styles.emoji}>{emoji}</Text>
      </View>
      <Text style={styles.title} numberOfLines={2}>
        {title}
      </Text>
      {locked ? <Text style={styles.lockedLabel}>Coming soon</Text> : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    flexBasis: "47%",
    flexGrow: 1,
    backgroundColor: colors.card,
    borderRadius: radii.control,
    borderWidth: 1,
    borderColor: colors.cardBorder,
    padding: spacing.md,
    gap: spacing.sm,
    minHeight: 96,
  },
  cardSelected: {
    borderColor: colors.selectedBorder,
    borderWidth: 2,
  },
  cardLocked: {
    opacity: 0.5,
  },
  emojiBadge: {
    width: 32,
    height: 32,
    alignItems: "center",
    justifyContent: "center",
  },
  emoji: {
    fontSize: 20,
  },
  title: {
    fontFamily: fonts.bodyBold,
    fontSize: 13,
    color: colors.ink,
  },
  lockedLabel: {
    fontFamily: fonts.bodySemi,
    fontSize: 10,
    letterSpacing: 0.5,
    textTransform: "uppercase",
    color: colors.inkSoft,
  },
});
