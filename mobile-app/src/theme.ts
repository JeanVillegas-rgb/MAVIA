// The same design tokens as web-app/src/styles/mavia.css, in React Native
// values. Change one, change the other: the two apps are one product, and a
// teacher who sets a lesson up on the web should recognise it on the phone.
//
// Blue is the only saturated colour and it fills areas rather than tinting
// them, so a screen is a few large blocks with space between rather than many
// small accents. Only two fills carry text, and both are checked:
//   brand600 with white  5.7:1
//   brand100 with ink   14.7:1
// Nothing else in the ramp is a background for text.

export const colors = {
  brand950: "#0a1b3d",
  brand900: "#10275a",
  brand800: "#163578",
  brand700: "#1a47a8",
  brand600: "#1d5fd6",
  brand500: "#3c81f0",
  brand400: "#6ba3f6",
  brand300: "#9cc4fa",
  brand200: "#c3dcfc",
  brand100: "#dce8fd",
  brand50: "#eff4fe",

  // Near-black, not navy: the ink has to be colourless for a blue block to
  // read as its own region rather than as more of the same.
  ink: "#14161a",
  muted: "#5a6472",
  faint: "#8c95a3",

  surface: "#ffffff",
  // The page is tinted so that white cards are separated by their own colour
  // and need no border to be told apart from it.
  panel: "#eef1f5",
  page: "#eef1f5",
  dark: "#14181f",
  darkSoft: "#1e242e",
  border: "#e2e7ee",
  borderStrong: "#cbd3de",

  success: "#157a4f",
  successBg: "#d8f0e4",
  warning: "#8a5200",
  warningBg: "#fbead2",
  danger: "#b5271b",
  dangerBg: "#fbe3e0",

  white: "#ffffff",
};

export const radii = {
  sm: 14,
  md: 22,
  lg: 32,
  pill: 999,
};

export const spacing = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
};

// Barely there. Separation is carried by colour and space, so a card does not
// also need to float off the page.
export const shadow = {
  card: {
    shadowColor: "#14181f",
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.06,
    shadowRadius: 18,
    elevation: 2,
  },
};

// Kept for the one lesson hero that still takes a gradient, but flattened to
// two neighbouring blues: a visible gradient reads as decoration, where a flat
// field reads as a region. See web-app's .mv-auth__aside and .mv-hero, which
// were flattened for the same reason.
export const gradients = {
  cover: [colors.brand600, colors.brand700] as const,
  hero: [colors.brand600, colors.brand700] as const,
};
