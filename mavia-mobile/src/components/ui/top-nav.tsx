import { StyleSheet, Text, View } from "react-native";

import { colors, fonts, radii, spacing } from "@/theme";

const LINKS = ["Home", "About us", "Contact"];

export function TopNav() {
  return (
    <View style={styles.bar}>
      <View style={styles.brandPill}>
        <Text style={styles.brandText}>MAVIA</Text>
      </View>
      <View style={styles.links}>
        {LINKS.map((label) => (
          <Text key={label} style={styles.linkText}>
            {label}
          </Text>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: colors.maroon800,
    borderRadius: radii.pill,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
  },
  brandPill: {
    backgroundColor: colors.maroon900,
    borderRadius: radii.pill,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  brandText: {
    color: colors.white,
    fontFamily: fonts.display,
    fontSize: 14,
    letterSpacing: 1,
  },
  links: {
    flexDirection: "row",
    gap: spacing.md,
  },
  linkText: {
    color: colors.white,
    fontFamily: fonts.bodySemi,
    fontSize: 12,
  },
});
