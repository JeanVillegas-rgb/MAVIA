import React from "react";
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  View,
  ViewStyle,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { colors, spacing } from "@/theme";
import { useResponsive } from "@/hooks/useResponsive";

type Props = {
  children: React.ReactNode;
  background?: string;
  contentStyle?: ViewStyle;
};

// Every screen's outer shell: safe-area + keyboard handling + a
// width-capped, centered content column so phones stay edge-to-edge while
// larger devices don't stretch content uncomfortably wide.
export default function ScreenContainer({
  children,
  background = colors.page,
  contentStyle,
}: Props) {
  const { contentWidth } = useResponsive();

  return (
    <SafeAreaView style={[styles.flex, { backgroundColor: background }]} edges={["top", "bottom"]}>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.scrollContent}
          keyboardShouldPersistTaps="handled"
        >
          <View style={[styles.content, { maxWidth: contentWidth }, contentStyle]}>
            {children}
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  scrollContent: { flexGrow: 1, alignItems: "center" },
  content: {
    width: "100%",
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.lg,
    paddingBottom: spacing.xl,
    alignSelf: "center",
  },
});
