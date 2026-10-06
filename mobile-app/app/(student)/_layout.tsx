import React, { useCallback } from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import { Redirect, Tabs, router } from "expo-router";
import { Ionicons } from "@expo/vector-icons";

import { useAuth } from "@/auth/AuthContext";
import { silenceAll } from "@/hooks/audioBus";
import { useNarration } from "@/hooks/useNarration";
import { useBrailleKeypad } from "@/input/useBrailleKeypad";
import { SwipeToCourses } from "@/nav/SwipeToCourses";
import { GuideActivityProvider } from "@/guide/GuideActivity";
import { useGuideRunner } from "@/guide/useGuideRunner";
import { COMMAND_KEYS } from "@/guide/script";
import { colors, radii, spacing } from "@/theme";

// The guide lives here, at the layout, so the guide key (.) works wherever the
// learner is -- being lost is exactly when it is wanted. See
// src/guide/useGuideRunner.ts: A, the whole guide, or B, choose a part;
// narration only. While it is open it owns the answer keys and the screens
// below keep quiet (GuideActivityProvider).

type IconName = keyof typeof Ionicons.glyphMap;

// Matches the reference layout's bottom bar: a "library" tab (course list ->
// lesson list -> player, nested under home/) and a profile tab.
const TABS: { name: string; title: string; icon: IconName; iconOutline: IconName }[] = [
  { name: "home", title: "Home", icon: "home", iconOutline: "home-outline" },
  { name: "profile", title: "Profile", icon: "person-circle", iconOutline: "person-circle-outline" },
];

const GUIDE_BANNER: Record<string, string> = {
  asking: "Guide — A: the whole guide · B: choose a part",
  choosing: "Guide — choose a part: A, B, C or D",
  playing: `Guide playing — press ${COMMAND_KEYS.guide} to stop`,
};

export default function StudentLayout() {
  const { user, loading } = useAuth();
  const signedIn = Boolean(user) && user?.role === "STUDENT";
  const narration = useNarration();
  const guide = useGuideRunner(narration, { signedIn });

  // Swipe anywhere: back to the course list. A swipe during the guide stops it.
  const goToCourses = useCallback(() => {
    guide.stop();
    silenceAll();
    router.push("/home");
  }, [guide]);

  // The back key (-, sticker E): leave the screen.
  // Everything is silenced first -- the screen being left has its own
  // narrator and, in the player, an audio track as well.
  const goBack = useCallback(() => {
    silenceAll();
    if (router.canGoBack()) router.back();
    else router.replace("/home");
  }, []);

  // App-wide keys. While the guide is open it takes them: the answer keys
  // answer the guide, repeat says its prompt again, and the guide key or the
  // back key closes it -- back unwinds the guide before it leaves a screen.
  // Otherwise the answer keys belong to whichever screen is asking (the keypad
  // hook is refcounted, so both listeners are live at once).
  useBrailleKeypad(
    (action) => {
      if (guide.busy) {
        if (action.kind === "answer") guide.answer(action.letter);
        else if (action.kind === "repeat") guide.repeat();
        else if (action.kind === "guide" || action.kind === "back") guide.stop();
        return;
      }
      if (action.kind === "guide") guide.toggle();
      else if (action.kind === "back") goBack();
    },
    { enabled: signedIn }
  );

  if (loading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={colors.brand600} size="large" />
      </View>
    );
  }
  if (!user) {
    return <Redirect href="/login" />;
  }
  if (user.role !== "STUDENT") {
    return <Redirect href="/not-supported" />;
  }

  return (
    <GuideActivityProvider busy={guide.busy}>
      <SwipeToCourses onSwipe={goToCourses}>
        <View style={styles.fill}>
          <Tabs
            screenOptions={{
              headerShown: false,
              tabBarActiveTintColor: colors.brand600,
              tabBarInactiveTintColor: colors.faint,
              tabBarStyle: styles.tabBar,
              tabBarLabelStyle: styles.tabLabel,
            }}
          >
            {TABS.map((tab) => (
              <Tabs.Screen
                key={tab.name}
                name={tab.name}
                options={{
                  title: tab.title,
                  tabBarIcon: ({ color, focused, size }) => (
                    <Ionicons name={focused ? tab.icon : tab.iconOutline} color={color} size={size} />
                  ),
                }}
              />
            ))}
          </Tabs>
          {/* What the guide is doing, for anyone who can see it. Last child so
              it paints above the tabs. */}
          {guide.busy && (
            <View style={styles.guideBanner} pointerEvents="none" accessibilityElementsHidden>
              <Ionicons name="compass-outline" size={16} color={colors.white} />
              <Text style={styles.guideBannerText}>{GUIDE_BANNER[guide.mode]}</Text>
            </View>
          )}
        </View>
      </SwipeToCourses>
    </GuideActivityProvider>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  loading: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.page },
  tabBar: {
    // Taller, and the label sits clear of the gesture bar: at 62px with 8px
    // below it, "Home" and "Profile" were being cut in half on this device.
    borderTopWidth: 0,
    backgroundColor: colors.surface,
    height: 78,
    paddingBottom: 18,
    paddingTop: 8,
  },
  tabLabel: { fontSize: 12, fontWeight: "700" },
  guideBanner: {
    position: "absolute",
    left: spacing.lg,
    right: spacing.lg,
    bottom: 92,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
    borderRadius: radii.pill,
    backgroundColor: colors.brand700,
  },
  guideBannerText: { flex: 1, fontSize: 13, fontWeight: "700", color: colors.white },
});
