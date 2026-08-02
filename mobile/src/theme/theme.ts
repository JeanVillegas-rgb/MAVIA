// Shared design tokens for MAVIA.
//
// Color choices are deliberately high-contrast (WCAG AA/AAA against white)
// since this app serves low-vision users, not just blind users relying on
// narration -- some learners will be reading the screen directly.
// Font sizes start larger than typical mobile defaults (18px body instead
// of 14-16px) for the same reason, and every Text element below respects
// the OS-level font-scale setting (never set allowFontScaling={false}).

export const colors = {
  background: "#F7F8FC",
  surface: "#FFFFFF",

  textPrimary: "#111827",
  textMuted: "#4B5563",

  // Deep indigo-violet -- distinctive without being the generic
  // "AI purple," dark enough for AAA contrast with white text at
  // button sizes.
  primary: "#3B348B",
  primaryDark: "#2A2568",
  primaryMuted: "#EDECF8",

  // Teal reserved for progress / mastery / correct-answer states.
  accent: "#0D9488",
  accentMuted: "#E3F5F3",

  // Warm burnt-orange for "try again" -- signals attention without
  // reading as alarm/failure, appropriate for a learning tool.
  retry: "#C2410C",
  retryMuted: "#FDECE3",

  border: "#E2E4F0",
} as const;

export const spacing = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
  xxl: 48,
} as const;

export const radii = {
  sm: 8,
  md: 12,
  lg: 20,
  pill: 999,
} as const;

export const typography = {
  title: {
    fontSize: 28,
    lineHeight: 36,
    fontWeight: "700" as const,
    color: colors.textPrimary,
  },
  subtitle: {
    fontSize: 20,
    lineHeight: 28,
    fontWeight: "600" as const,
    color: colors.textPrimary,
  },
  body: {
    fontSize: 18,
    lineHeight: 26,
    fontWeight: "400" as const,
    color: colors.textPrimary,
  },
  bodyMuted: {
    fontSize: 16,
    lineHeight: 24,
    fontWeight: "400" as const,
    color: colors.textMuted,
  },
  label: {
    fontSize: 13,
    lineHeight: 18,
    fontWeight: "700" as const,
    color: colors.textMuted,
    letterSpacing: 0.6,
    textTransform: "uppercase" as const,
  },
  button: {
    fontSize: 18,
    lineHeight: 24,
    fontWeight: "700" as const,
  },
};

// Minimum touch target per WCAG 2.5.5 / platform HIG guidance.
export const MIN_TOUCH_TARGET = 48;