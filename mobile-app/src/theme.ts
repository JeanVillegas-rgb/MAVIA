// Same blue MAVIA palette as web-app/src/styles/mavia.css, translated to
// React Native style values.

export const colors = {
  brand950: "#071b33",
  brand900: "#0b2a4a",
  brand800: "#123a63",
  brand700: "#17497c",
  brand600: "#1e5fa8",
  brand500: "#2f7dd1",
  brand400: "#5fa0e0",
  brand300: "#93c1ec",
  brand200: "#c4def6",
  brand100: "#dfecfa",
  brand50: "#eef5fc",

  ink: "#12212f",
  muted: "#5c6b7c",
  faint: "#8a97a5",
  surface: "#ffffff",
  panel: "#f4f7fb",
  page: "#ffffff",
  border: "#dde5ee",
  borderStrong: "#c6d2e0",

  success: "#1f7a55",
  successBg: "#dff3ea",
  warning: "#9a5b00",
  warningBg: "#fdeede",
  danger: "#c0392b",
  dangerBg: "#fdecea",

  white: "#ffffff",
};

export const radii = {
  sm: 10,
  md: 16,
  lg: 24,
  pill: 999,
};

export const spacing = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
};

export const shadow = {
  card: {
    shadowColor: colors.brand900,
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.1,
    shadowRadius: 16,
    elevation: 3,
  },
};

// Diagonal blue gradients for cover-art tiles (course cards, lesson hero,
// player art) — every "cover" in the app is a gradient, never a fake image,
// since we have no real artwork to show yet.
export const gradients = {
  cover: [colors.brand500, colors.brand900] as const,
  hero: [colors.brand600, colors.brand950] as const,
};
