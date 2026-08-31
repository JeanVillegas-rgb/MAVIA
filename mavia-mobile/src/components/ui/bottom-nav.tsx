import { SymbolView, type SymbolViewProps } from "expo-symbols";
import { Pressable, StyleSheet, View } from "react-native";

import { colors, radii, shadow, spacing } from "@/theme";

type IconName = "profile" | "book" | "chart" | "star";

const ICONS: Record<IconName, SymbolViewProps["name"]> = {
  profile: { ios: "person.circle", android: "person", web: "person" },
  book: { ios: "book.closed", android: "menu_book", web: "menu_book" },
  chart: { ios: "chart.bar", android: "bar_chart", web: "bar_chart" },
  star: { ios: "star", android: "star", web: "star" },
};

const ORDER: IconName[] = ["profile", "book", "chart", "star"];

export function BottomNav() {
  return (
    <View style={styles.wrap}>
      <View style={styles.pill}>
        {ORDER.map((name) => (
          <Pressable key={name} style={styles.button} hitSlop={8}>
            <SymbolView name={ICONS[name]} tintColor={colors.white} size={20} />
          </Pressable>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    alignItems: "center",
  },
  pill: {
    flexDirection: "row",
    gap: spacing.lg,
    backgroundColor: colors.maroon800,
    borderRadius: radii.pill,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    ...shadow.card,
  },
  button: {
    alignItems: "center",
    justifyContent: "center",
  },
});
