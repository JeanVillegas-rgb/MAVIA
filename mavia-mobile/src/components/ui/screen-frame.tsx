import type { PropsWithChildren } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { BottomNav } from "./bottom-nav";
import { TopNav } from "./top-nav";
import { colors, fonts, radii, shadow, spacing } from "@/theme";

type ScreenFrameProps = PropsWithChildren<{
  eyebrow?: string;
}>;

export function ScreenFrame({ eyebrow = "Modules", children }: ScreenFrameProps) {
  return (
    <SafeAreaView style={styles.safeArea} edges={["top", "bottom"]}>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}>
        <TopNav />
        <Text style={styles.eyebrow}>{eyebrow}</Text>
        <View style={styles.card}>{children}</View>
        <View style={styles.bottomNavWrap}>
          <BottomNav />
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: colors.maroon900,
  },
  scroll: {
    flex: 1,
  },
  scrollContent: {
    padding: spacing.md,
    gap: spacing.sm,
    maxWidth: 900,
    width: "100%",
    alignSelf: "center",
  },
  eyebrow: {
    fontFamily: fonts.bodyBold,
    fontSize: 11,
    letterSpacing: 2,
    textTransform: "uppercase",
    color: colors.blush,
    paddingLeft: spacing.xs,
  },
  card: {
    backgroundColor: colors.cream,
    borderRadius: radii.card,
    padding: spacing.lg,
    gap: spacing.md,
    ...shadow.card,
  },
  bottomNavWrap: {
    marginTop: -spacing.lg,
    alignItems: "center",
    zIndex: 2,
  },
});
