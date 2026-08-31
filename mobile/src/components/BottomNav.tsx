import React from "react";
import { StyleSheet, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, radii, spacing } from "@/theme";

const ICONS: (keyof typeof Ionicons.glyphMap)[] = [
  "person-outline",
  "chatbubble-outline",
  "bar-chart-outline",
  "star-outline",
];

export default function BottomNav() {
  return (
    <View style={styles.wrap}>
      {ICONS.map((name) => (
        <View key={name} style={styles.iconWrap}>
          <Ionicons name={name} size={20} color={colors.white} />
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    backgroundColor: colors.maroon900,
    borderRadius: radii.pill,
    paddingVertical: 14,
    paddingHorizontal: spacing.lg,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  iconWrap: {
    width: 40,
    height: 40,
    borderRadius: radii.pill,
    alignItems: "center",
    justifyContent: "center",
  },
});
