// Who is allowed to talk.
//
// The guide, the section menu and the practice drills all live at the layout,
// while the course and lesson pickers live on the screens underneath. Both
// speak, and expo-speech has exactly one voice: `speak` stops whatever is
// already being said. So a screen loading its list under a running guide cut
// the guide off mid-sentence -- which is precisely what happened the first
// time practice ran, and it looked like the practice had simply never started.
//
// Rather than have the two coordinate, the guide always wins: it is the thing
// a learner is being asked to follow, and it is short. This context is how a
// screen finds out to keep quiet. Screens still work normally while it is
// true -- keys and taps do what they always do -- they just do not narrate
// over the top.

import React, { createContext, useContext, useMemo } from "react";

const GuideActivityContext = createContext(false);

/** True while the guide, its menu or a practice drill is speaking. A screen
 *  should not start narrating of its own accord while this is true. */
export function useGuideBusy(): boolean {
  return useContext(GuideActivityContext);
}

export function GuideActivityProvider({
  busy,
  children,
}: {
  busy: boolean;
  children: React.ReactNode;
}) {
  // Memo so every screen below does not re-render on unrelated layout renders.
  const value = useMemo(() => busy, [busy]);
  return <GuideActivityContext.Provider value={value}>{children}</GuideActivityContext.Provider>;
}
