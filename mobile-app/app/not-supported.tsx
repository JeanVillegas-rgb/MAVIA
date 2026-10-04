import React from "react";
import { StyleSheet, Text } from "react-native";

import ScreenContainer from "@/components/ScreenContainer";
import Card from "@/components/Card";
import Button from "@/components/Button";
import { useAuth } from "@/auth/AuthContext";
import { colors, spacing } from "@/theme";

// Teacher and admin accounts can authenticate against the same API, but
// this app only ships the student experience — send them back out rather
// than showing a blank/broken screen.
export default function NotSupportedScreen() {
  const { user, logout } = useAuth();

  return (
    <ScreenContainer>
      <Card style={{ alignItems: "center" }}>
        <Text style={styles.title}>This app is for students</Text>
        <Text style={styles.subtitle}>
          {user?.username ? `"${user.username}"` : "This account"} is registered as{" "}
          {user?.role?.toLowerCase()}. Teacher and admin access is on the MAVIA web app.
        </Text>
        <Button label="Log out" onPress={logout} style={{ marginTop: spacing.lg, width: "100%" }} />
      </Card>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: 20, fontWeight: "800", color: colors.ink, textAlign: "center" },
  subtitle: {
    marginTop: spacing.sm,
    fontSize: 14,
    color: colors.muted,
    textAlign: "center",
    lineHeight: 20,
  },
});
