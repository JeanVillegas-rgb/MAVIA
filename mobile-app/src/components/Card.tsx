import React from "react";
import { StyleSheet, View, ViewStyle } from "react-native";

import { colors, radii, shadow, spacing } from "@/theme";

type Props = {
  children: React.ReactNode;
  style?: ViewStyle;
  tint?: boolean;
};

export default function Card({ children, style, tint }: Props) {
  return (
    <View
      style={[
        styles.card,
        tint && { backgroundColor: colors.brand50, borderColor: colors.brand100 },
        style,
      ]}
    >
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
    width: "100%",
    ...shadow.card,
  },
});
