import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { Redirect, Tabs, router } from "expo-router";
import { Ionicons } from "@expo/vector-icons";

import { useAuth } from "@/auth/AuthContext";
import { useNarration } from "@/hooks/useNarration";
import { silenceAll } from "@/hooks/audioBus";
import { hasHeardGuide, useGuideOnFirstLaunch } from "@/guide/useGuide";
import { GuideMenu } from "@/guide/GuideMenu";
import { useGuidePractice } from "@/guide/useGuidePractice";
import { GuideActivityProvider } from "@/guide/GuideActivity";
import { PracticeTapLayer } from "@/guide/PracticeTapLayer";
import { useBrailleKeypad } from "@/input/useBrailleKeypad";
import { useVoiceCommands } from "@/voice/useVoiceCommands";
import { SwipeToCourses } from "@/nav/SwipeToCourses";
import { colors } from "@/theme";

type IconName = keyof typeof Ionicons.glyphMap;

// Matches the reference layout's bottom bar: a "library" tab (course list ->
// lesson list -> player, nested under home/) and a profile tab.
const TABS: { name: string; title: string; icon: IconName; iconOutline: IconName }[] = [
  { name: "home", title: "Home", icon: "home", iconOutline: "home-outline" },
  { name: "profile", title: "Profile", icon: "person-circle", iconOutline: "person-circle-outline" },
];

export default function StudentLayout() {
  const { user, loading } = useAuth();
  const narration = useNarration();
  const signedIn = Boolean(user) && user?.role === "STUDENT";

  // The guide plays itself the first time a learner ever gets here, and is on
  // the minus key and the "how does this work" commands forever after. It
  // lives at the layout rather than on a screen so that pressing minus works
  // wherever they are -- being lost is exactly when it is wanted.
  const guide = useGuideOnFirstLaunch(narration, { enabled: signedIn, autoPlay: false });
  const practice = useGuidePractice(narration);
  const [menuOpen, setMenuOpen] = useState(false);

  // First time in, the learner practises rather than listens: each move is
  // asked for and confirmed. Afterwards minus offers the sections to re-hear.
  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    void hasHeardGuide().then((heard) => {
      if (!cancelled && !heard) practice.start();
    });
    return () => {
      cancelled = true;
    };
    // Only ever on the transition into being signed in; practice.start is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signedIn]);

  // Minus means "help me" both times, but it should not mean the same eight
  // paragraphs twice. Never heard it: play the whole thing. Heard it: offer
  // the four sections and read only the one asked for.
  const openGuide = useCallback(() => {
    // Minus twice runs the practice again. A learner who wants to rehearse
    // the moves rather than hear them described has no other way back to it
    // once the first run is done -- and it is the part worth rehearsing.
    if (menuOpen) {
      setMenuOpen(false);
      practice.start();
      return;
    }
    void hasHeardGuide().then((heard) => {
      if (heard) setMenuOpen(true);
      else practice.start();
    });
  }, [menuOpen, practice]);

  const goToCourses = useCallback(() => {
    // During practice a swipe is the drill, not a navigation.
    if (practice.feed({ kind: "swipe" })) return;
    guide.stop();
    silenceAll();
    router.push("/home");
  }, [guide, narration, practice]);

  // Divide is "back" everywhere, and back means the nearest thing to leave --
  // not always a screen. Something talking at you is the thing you most want
  // out of, so it is unwound first: stop the guide, then close the menu, and
  // only then leave the screen. Without the ordering, pressing divide during
  // the guide would navigate while it kept talking over the new screen.
  const goBack = useCallback(() => {
    // The back key is itself the last drill, so practice gets first refusal --
    // but only if it actually consumes the press. If it does not (it is not
    // waiting, or is waiting for something else) divide must still mean
    // "leave", or a practice run that lost its voice leaves a learner with no
    // way out of the screen they are on.
    if (practice.running && practice.feed({ kind: "command", command: "back" })) return;
    if (guide.playing) {
      guide.stop();
      return;
    }
    if (menuOpen) {
      setMenuOpen(false);
      narration.stop();
      return;
    }
    // Everything, not just this component's narrator: the screen being left
    // has its own narrator and, in the player, an audio track as well.
    silenceAll();
    if (router.canGoBack()) router.back();
    else router.replace("/home");
  }, [guide, menuOpen, narration, practice]);

  // App-wide keys. Answer keys are deliberately not handled here: they belong
  // to whichever screen is asking a question, and the keypad hook is
  // refcounted so both listeners can be live at once.
  useBrailleKeypad(
    (action) => {
      // While practice runs it owns every key: a drill expecting 8 must not
      // also answer a real question, and keys it is not waiting for are
      // swallowed so hunting for the right one sets nothing off.
      if (practice.running) {
        if (action.kind === "answer") practice.feed({ kind: "letter", letter: action.letter, via: "key" });
        else if (action.kind === "repeat") practice.feed({ kind: "command", command: "repeat" });
        else if (action.kind === "next") practice.feed({ kind: "command", command: "next" });
        else if (action.kind === "back") goBack();
        // Minus abandons a practice run rather than restarting it underneath
        // itself: someone pressing it mid-drill wants out, not a second copy.
        else if (action.kind === "guide") practice.quit();
        return;
      }
      if (action.kind === "guide") openGuide();
      if (action.kind === "back") goBack();
    },
    { enabled: signedIn }
  );

  useVoiceCommands(
    {
      playGuide: () => openGuide(),
      goBack: () => goBack(),
      // Spoken letters count during practice, so a learner can rehearse the
      // voice path as well as the keys.
      chooseA: () => practice.feed({ kind: "letter", letter: "a", via: "voice" }),
      chooseB: () => practice.feed({ kind: "letter", letter: "b", via: "voice" }),
      chooseC: () => practice.feed({ kind: "letter", letter: "c", via: "voice" }),
      chooseD: () => practice.feed({ kind: "letter", letter: "d", via: "voice" }),
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
    <GuideActivityProvider busy={practice.running || guide.playing || menuOpen}>
    <SwipeToCourses onSwipe={goToCourses}>
      <View style={styles.fill}>
        <GuideMenu
          open={menuOpen}
          narration={narration}
          onPlaySection={guide.playSection}
          onClose={() => setMenuOpen(false)}
        />
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
        {/* Last child on purpose: later siblings paint on top, and this has to
            be above the tab navigator to receive a tap at all. */}
        <PracticeTapLayer
          active={practice.awaitingTap}
          narration={narration}
          onLetter={(letter) => practice.feed({ kind: "letter", letter, via: "tap" })}
        />
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
});
