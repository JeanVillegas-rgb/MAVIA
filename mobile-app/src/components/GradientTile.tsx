import React from "react";
import { StyleSheet, ViewStyle } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";

import { colors, gradients, radii } from "@/theme";

type Props = {
  size?: number;
  radius?: number;
  icon?: keyof typeof Ionicons.glyphMap;
  variant?: "cover" | "hero";
  style?: ViewStyle;
  /** Cover art is decorative once its container already has a text label
   * (e.g. the lesson title next to it) — pass a label only when this tile
   * is the sole description of what it represents. */
  accessibilityLabel?: string;
};

// Every "cover art" tile in the app is a generated gradient, not a real
// image — there's no artwork to fetch yet, so this is the honest stand-in
// rather than a placeholder photo.
export default function GradientTile({
  size = 56,
  radius = radii.sm,
  icon,
  variant = "cover",
  style,
  accessibilityLabel,
}: Props) {
  return (
    <LinearGradient
      colors={gradients[variant]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={[
        styles.tile,
        { width: size, height: size, borderRadius: radius },
        style,
      ]}
      accessible={Boolean(accessibilityLabel)}
      accessibilityRole={accessibilityLabel ? "image" : undefined}
      accessibilityLabel={accessibilityLabel}
      importantForAccessibility={accessibilityLabel ? "yes" : "no-hide-descendants"}
    >
      {icon ? (
        <Ionicons name={icon} size={Math.round(size * 0.4)} color={colors.white} />
      ) : null}
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  tile: {
    alignItems: "center",
    justifyContent: "center",
    overflow: "hidden",
  },
});
