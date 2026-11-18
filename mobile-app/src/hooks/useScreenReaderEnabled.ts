import { useEffect, useState } from "react";
import { AccessibilityInfo } from "react-native";

// Whether a screen reader (TalkBack, VoiceOver) is on right now, kept current
// if the learner switches it mid-session.
//
// Screens that take raw taps need this: under a screen reader one tap only
// moves focus and a double-tap activates, so a gesture like "tap twice for B"
// cannot work -- the screen reader's own controls must be left in charge.
export function useScreenReaderEnabled(): boolean {
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    let active = true;
    AccessibilityInfo.isScreenReaderEnabled()
      .then((value) => {
        if (active) setEnabled(value);
      })
      .catch(() => {
        // Unknown: assume off, which keeps tap answering available.
      });
    const subscription = AccessibilityInfo.addEventListener("screenReaderChanged", setEnabled);
    return () => {
      active = false;
      subscription.remove();
    };
  }, []);

  return enabled;
}
