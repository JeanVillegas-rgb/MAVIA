import React from "react";
import { StyleSheet, Text, View } from "react-native";

import { colors } from "@/theme";
import { User } from "@/api/client";

export function initialsFor(user: User | null): string {
  if (!user) return "?";
  const source =
    [user.first_name, user.last_name].filter(Boolean).join(" ") || user.username || "?";
  return source
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
}

export function displayName(user: User | null): string {
  if (!user) return "";
  return [user.first_name, user.last_name].filter(Boolean).join(" ") || user.username;
}

export default function Avatar({ user, size = 42 }: { user: User | null; size?: number }) {
  return (
    <View style={[styles.avatar, { width: size, height: size, borderRadius: size / 2 }]}>
      <Text style={[styles.initials, { fontSize: size * 0.38 }]}>{initialsFor(user)}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  avatar: {
    backgroundColor: colors.brand600,
    alignItems: "center",
    justifyContent: "center",
  },
  initials: { color: colors.white, fontWeight: "800" },
});
