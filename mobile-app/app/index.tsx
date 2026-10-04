import React from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import { Redirect } from "expo-router";

import { useAuth } from "@/auth/AuthContext";
import { colors } from "@/theme";

// Entry route: decides where to send the user based on auth + role state.
// Kept as a plain loading view (not a redirect) while auth is still
// resolving, so we don't flash the login screen before a stored token has
// had a chance to validate.
export default function Index() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={colors.brand600} size="large" />
        <Text style={styles.loadingText}>Loading MAVIA…</Text>
      </View>
    );
  }

  if (!user) {
    return <Redirect href="/login" />;
  }

  if (user.role !== "STUDENT") {
    return <Redirect href="/not-supported" />;
  }

  // (student) is a route group, not a URL segment — its "home" tab
  // resolves to /home, not /(student).
  return <Redirect href="/home" />;
}

const styles = StyleSheet.create({
  loading: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    gap: 12,
    backgroundColor: colors.page,
  },
  loadingText: { color: colors.muted, fontSize: 14 },
});
