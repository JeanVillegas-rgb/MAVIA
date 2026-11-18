import { useWindowDimensions } from "react-native";

// Phone-first, but flexes for larger phones / small tablets: once the
// viewport is wide enough, list screens switch from a single column to a
// two-column grid and cap their content width instead of stretching edge
// to edge.
const TABLET_BREAKPOINT = 700;
const MAX_CONTENT_WIDTH = 640;

export function useResponsive() {
  const { width, height } = useWindowDimensions();
  const isTablet = width >= TABLET_BREAKPOINT;

  return {
    width,
    height,
    isTablet,
    columns: isTablet ? 2 : 1,
    contentWidth: Math.min(width, MAX_CONTENT_WIDTH),
  };
}
