import React from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, fonts, radii, spacing } from "@/theme";

type Props = {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  loading?: boolean;
  icon?: keyof typeof Ionicons.glyphMap;
  variant?: "solid" | "outline";
};

export default function PrimaryButton({
  label,
  onPress,
  disabled,
  loading,
  icon,
  variant = "solid",
}: Props) {
  const isOutline = variant === "outline";
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || loading}
      style={({ pressed }) => [
        styles.base,
        isOutline ? styles.outline : styles.solid,
        (disabled || loading) && styles.disabled,
        pressed && !disabled && styles.pressed,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={isOutline ? colors.maroon900 : colors.white} />
      ) : (
        <View style={styles.row}>
          <Text style={[styles.label, isOutline && styles.labelOutline]}>{label}</Text>
          {icon && (
            <Ionicons
              name={icon}
              size={18}
              color={isOutline ? colors.maroon900 : colors.white}
              style={{ marginLeft: spacing.sm }}
            />
          )}
        </View>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    paddingVertical: 14,
    paddingHorizontal: spacing.lg,
    borderRadius: radii.pill,
    alignItems: "center",
    justifyContent: "center",
  },
  solid: {
    backgroundColor: colors.maroon900,
  },
  outline: {
    backgroundColor: "transparent",
    borderWidth: 1.5,
    borderColor: colors.maroon900,
  },
  disabled: {
    opacity: 0.45,
  },
  pressed: {
    opacity: 0.85,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
  },
  label: {
    color: colors.white,
    fontFamily: fonts.bodyBold,
    fontSize: 15,
  },
  labelOutline: {
    color: colors.maroon900,
  },
});
