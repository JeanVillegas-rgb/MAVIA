// "Which part would you like to hear?" -- the minus key's second visit.
//
// The first time a learner opens the app they hear the whole guide, because
// someone who has never used it cannot usefully pick a section. Every time
// after, minus opens this instead: four titles, four letters, and only the
// part they asked for gets read. Hearing the whole thing again to reach the
// last thirty seconds of it is the problem this exists to remove.
//
// It deliberately reuses useListPicker rather than growing its own letter
// handling. Picking a guide section is the same act as picking a course, on
// the same keys, with the same "say the letter or press it" -- so it should
// be the same code, and a learner should not have to notice they are in a
// different kind of list.

import React, { useCallback } from "react";

import { useListPicker } from "@/nav/useListPicker";
import { GUIDE_MENU_QUESTION, GUIDE_SECTIONS, type GuideSection } from "./script";

type Narrator = {
  speak: (text: string, options?: { onDone?: () => void }) => void;
  stop: () => void;
};

export function GuideMenu({
  open,
  narration,
  onPlaySection,
  onClose,
}: {
  open: boolean;
  narration: Narrator;
  onPlaySection: (id: string) => void;
  onClose: () => void;
}) {
  const handlePick = useCallback(
    (section: GuideSection) => {
      // Close first, then play. The menu's own picker would otherwise still be
      // listening while the section is read, and a section that happens to say
      // a letter out loud -- "press 7 for A" -- would pick from the menu again.
      onClose();
      onPlaySection(section.id);
    },
    [onClose, onPlaySection]
  );

  useListPicker<GuideSection>({
    items: GUIDE_SECTIONS,
    labelOf: (section) => section.title,
    question: GUIDE_MENU_QUESTION,
    narration,
    onPick: handlePick,
    enabled: open,
  });

  // Nothing is drawn. The menu is entirely spoken: a learner who needs it
  // cannot see a sheet, and a sighted teacher watching over their shoulder
  // does not need one to follow what is happening.
  return null;
}
