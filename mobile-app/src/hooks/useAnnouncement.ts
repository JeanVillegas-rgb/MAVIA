import { useEffect } from "react";
import { AccessibilityInfo, Platform } from "react-native";

// Reads a message aloud when it appears -- a login error, "check your email"
// -- so a learner using a screen reader hears it without having to find it.
//
// iOS needs the announcement. On Android the element showing the message is a
// live region (`accessibilityLiveRegion="polite"`, see `liveRegion` below),
// which TalkBack reads on its own; announcing there too would read it twice.
export function useAnnouncement(message: string | null | undefined) {
  useEffect(() => {
    if (message && Platform.OS === "ios") {
      AccessibilityInfo.announceForAccessibility(message);
    }
  }, [message]);
}

// Spread onto the element that shows an announced message.
export const liveRegion = {
  accessibilityLiveRegion: "polite" as const,
  accessibilityRole: "alert" as const,
};
