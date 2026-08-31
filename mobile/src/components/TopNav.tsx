import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { colors, fonts, radii, spacing } from "@/theme";

type Props = {
  showBack?: boolean;
  title?: string;
};

export default function TopNav({ showBack, title = "MAVIA" }: Props) {
  const router = useRouter();
  return (
    <View style={styles.wrap}>
      <View style={styles.left}>
        {showBack && (
          <Pressable onPress={() => router.back()} hitSlop={10} style={styles.backBtn}>
            <Ionicons name="chevron-back" size={18} color={colors.white} />
          </Pressable>
        )}
        <Text style={styles.brand}>{title}</Text>
      </View>
      <View style={styles.links}>
        <Text style={styles.link}>Home</Text>
        <Text style={styles.link}>About us</Text>
        <Text style={styles.link}>Contact</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    backgroundColor: colors.maroon900,
    borderRadius: radii.pill,
    paddingVertical: 12,
    paddingHorizontal: spacing.md,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  left: {
    flexDirection: "row",
    alignItems: "center",
  },
  backBtn: {
    marginRight: spacing.sm,
  },
  brand: {
    color: colors.white,
    fontFamily: fonts.display,
    fontSize: 15,
    letterSpacing: 0.5,
  },
  links: {
    flexDirection: "row",
    gap: spacing.md,
    display: "none", // hidden on mobile widths; kept for larger screens
  },
  link: {
    color: colors.white,
    fontFamily: fonts.bodySemi,
    fontSize: 13,
  },
});
