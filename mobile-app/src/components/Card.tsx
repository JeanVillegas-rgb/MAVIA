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
        tint && { backgroundColor: colors.brand50 },
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
    padding: spacing.lg + 4,
    width: "100%",
    ...shadow.card,
  },
});
