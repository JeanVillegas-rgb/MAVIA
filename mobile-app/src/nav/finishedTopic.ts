// The one line the lessons screen needs after a lesson has just been finished.
//
// The player knows which topic was completed, but the lessons screen is where
// the learner lands and where the next four options are read out, so that is
// where the announcement belongs. Route params would be the obvious carrier,
// except the lessons screen is reached with router.back(): it is already in
// the stack, so there is nothing to pass it. This holds the one title between
// the two screens instead.
//
// Handed over exactly once. A learner who walks back to the list later is not
// told again that they finished something an hour ago.

let pending: string | null = null;

/** Called by the player the moment a lesson finishes. */
export function setFinishedTopic(title: string): void {
  pending = title;
}

/** Read it and clear it. `null` when nothing was just finished. */
export function takeFinishedTopic(): string | null {
  const title = pending;
  pending = null;
  return title;
}
