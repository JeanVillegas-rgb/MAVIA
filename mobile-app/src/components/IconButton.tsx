import React from "react";
import { Pressable, StyleSheet, ViewStyle } from "react-native";
import { Ionicons } from "@expo/vector-icons";

import { colors, radii } from "@/theme";

type Variant = "ghost" | "filled" | "light";

type Props = {
  icon: keyof typeof Ionicons.glyphMap;
  /** Required, not optional — this button has no visible text, so a screen
   * reader has nothing else to announce. */
  accessibilityLabel: string;
  onPress?: () => void;
  size?: number;
  variant?: Variant;
  disabled?: boolean;
  /** For toggle buttons (e.g. a favorite heart) — announced as "selected". */
  selected?: boolean;
  style?: ViewStyle;
};

// Minimum 44x44 touch target (iOS HIG / Android accessibility guidance)
// regardless of the visible icon size, so this stays comfortably tappable
// for anyone with limited fine motor control, not just screen-reader users.
const MIN_HIT_AREA = 44;

export default function IconButton({
  icon,
  accessibilityLabel,
  onPress,
  size = 40,
  variant = "ghost",
  disabled,
  selected,
  style,
}: Props) {
  const inset = Math.max(0, (MIN_HIT_AREA - size) / 2);

  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      accessibilityState={{
        disabled: Boolean(disabled),
        ...(selected !== undefined ? { selected } : null),
      }}
      hitSlop={{ top: inset, bottom: inset, left: inset, right: inset }}
      style={({ pressed }) => [
        styles.base,
        variantStyles[variant],
        { width: size, height: size, borderRadius: size / 2 },
        disabled && styles.disabled,
        pressed && !disabled && styles.pressed,
        style,
      ]}
    >
      <Ionicons
        name={icon}
        size={Math.round(size * 0.52)}
        color={iconColor(variant)}
      />
    </Pressable>
  );
}

function iconColor(variant: Variant) {
  switch (variant) {
    case "filled":
      return colors.white;
    case "light":
      return colors.brand800;
    default:
      return colors.ink;
  }
}

const styles = StyleSheet.create({
  base: {
    alignItems: "center",
    justifyContent: "center",
  },
  pressed: { opacity: 0.7 },
  disabled: { opacity: 0.35 },
});

const variantStyles: Record<Variant, ViewStyle> = {
  ghost: { backgroundColor: "transparent" },
  filled: { backgroundColor: colors.brand600 },
  light: { backgroundColor: colors.white, borderRadius: radii.pill },
};
