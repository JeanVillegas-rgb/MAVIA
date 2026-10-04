// Swipe right, anywhere in the student area, to go and pick a lesson.
//
// A blind learner has no "tap the Home tab" available: the tab bar is a target
// you have to find. A swipe needs no target at all -- it works from wherever
// the finger already is, which is the point of it.
//
// The gesture is deliberately blunt: a mostly-horizontal drag to the right,
// past a distance no accidental touch covers. It activates only after 40px of
// horizontal travel, so taps, tap-to-answer runs and vertical scrolling all
// pass through to the screen underneath untouched.

import React from "react";
import { Gesture, GestureDetector } from "react-native-gesture-handler";

const ACTIVATE_AFTER_PX = 40;
const MIN_TRAVEL_PX = 90;
// A swipe that travels further up or down than across is a scroll, not this.
const MAX_VERTICAL_RATIO = 0.8;

export function SwipeToCourses({
  onSwipe,
  enabled = true,
  children,
}: {
  onSwipe: () => void;
  enabled?: boolean;
  children: React.ReactNode;
}) {
  const gesture = React.useMemo(
    () =>
      Gesture.Pan()
        .enabled(enabled)
        // This app has no reanimated, so the callback must not be a worklet:
        // runOnJS(true) keeps onEnd on the JS thread. Pulling reanimated in
        // just for a swipe would mean a native rebuild of every installed APK.
        .runOnJS(true)
        .activeOffsetX(ACTIVATE_AFTER_PX)
        .onEnd((event) => {
          if (event.translationX < MIN_TRAVEL_PX) return;
          if (Math.abs(event.translationY) > Math.abs(event.translationX) * MAX_VERTICAL_RATIO) return;
          onSwipe();
        }),
    [enabled, onSwipe]
  );

  return <GestureDetector gesture={gesture}>{children}</GestureDetector>;
}
