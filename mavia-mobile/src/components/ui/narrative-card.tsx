import { LinearGradient } from "expo-linear-gradient";
import { SymbolView } from "expo-symbols";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { colors, fonts, radii, spacing } from "@/theme";

type NarrativeCardProps = {
  title: string;
  emoji?: string;
  selected?: boolean;
  large?: boolean;
  onPress?: () => void;
  playing?: boolean;
  onTogglePlay?: () => void;
};

export function NarrativeCard({
  title,
  emoji = "\u{1F431}",
  selected,
  large,
  onPress,
  playing,
  onTogglePlay,
}: NarrativeCardProps) {
  return (
    <Pressable onPress={onPress} style={[styles.card, selected && styles.cardSelected]}>
      <Text style={styles.title}>{title}</Text>
      <LinearGradient
        colors={["#CDEBCE", "#DCEFFB"]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={[styles.illustration, large && styles.illustrationLarge]}>
        <Text style={styles.emoji}>{emoji}</Text>
        <Pressable
          style={styles.playBar}
          onPress={onTogglePlay}
          disabled={!onTogglePlay}
          hitSlop={8}>
          <Text style={styles.playLabel}>{playing ? "Reading aloud…" : "Play narration"}</Text>
          <View style={styles.playButton}>
            <SymbolView
              name={
                playing
                  ? { ios: "pause.fill", android: "pause", web: "pause" }
                  : { ios: "play.fill", android: "play_arrow", web: "play_arrow" }
              }
              tintColor={colors.maroon900}
              size={16}
            />
          </View>
        </Pressable>
      </LinearGradient>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.cream,
    borderRadius: radii.control,
    borderWidth: 1,
    borderColor: colors.cardBorder,
    padding: spacing.sm,
    gap: spacing.sm,
  },
  cardSelected: {
    borderColor: colors.selectedBorder,
    borderWidth: 2,
  },
  title: {
    fontFamily: fonts.display,
    fontSize: 18,
    color: colors.ink,
  },
  illustration: {
    borderRadius: radii.control - 4,
    aspectRatio: 4 / 3,
    justifyContent: "center",
    alignItems: "center",
    overflow: "hidden",
  },
  illustrationLarge: {
    aspectRatio: 16 / 10,
  },
  emoji: {
    fontSize: 56,
  },
  playBar: {
    position: "absolute",
    left: spacing.sm,
    right: spacing.sm,
    bottom: spacing.sm,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: colors.maroon900,
    borderRadius: radii.pill,
    paddingLeft: spacing.md,
    paddingRight: spacing.xs,
    paddingVertical: spacing.xs,
  },
  playLabel: {
    fontFamily: fonts.bodySemi,
    fontSize: 12,
    color: colors.white,
  },
  playButton: {
    width: 28,
    height: 28,
    borderRadius: radii.pill,
    backgroundColor: colors.white,
    alignItems: "center",
    justifyContent: "center",
    paddingLeft: 2,
  },
});
