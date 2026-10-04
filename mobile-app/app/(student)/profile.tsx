import React, { useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";

import ScreenContainer from "@/components/ScreenContainer";
import Card from "@/components/Card";
import Button from "@/components/Button";
import Avatar, { displayName } from "@/components/Avatar";
import { useAuth } from "@/auth/AuthContext";
import { colors, radii, spacing } from "@/theme";

export default function ProfileScreen() {
  const { user, logout } = useAuth();
  const router = useRouter();
  const [loggingOut, setLoggingOut] = useState(false);

  async function handleLogout() {
    setLoggingOut(true);
    try {
      await logout();
      router.replace("/login");
    } finally {
      setLoggingOut(false);
    }
  }

  return (
    <ScreenContainer>
      <Text style={styles.heading}>Profile</Text>

      <Card style={{ marginTop: spacing.md }}>
        <View style={styles.profileRow}>
          <Avatar user={user} size={56} />
          <View style={{ flex: 1 }}>
            <Text style={styles.name}>{displayName(user)}</Text>
            <Text style={styles.email}>{user?.email}</Text>
            <View style={styles.rolePill}>
              <Text style={styles.rolePillText}>{user?.role}</Text>
            </View>
          </View>
        </View>
      </Card>

      <Card style={{ marginTop: spacing.md }}>
        <Row label="Username" value={user?.username ?? ""} />
        <Row label="Email verified" value={user?.is_verified ? "Yes" : "No"} last />
      </Card>

      <Button
        label={loggingOut ? "Logging out…" : "Log out"}
        variant="ghost"
        onPress={handleLogout}
        loading={loggingOut}
        style={{ marginTop: spacing.lg }}
      />
    </ScreenContainer>
  );
}

function Row({ label, value, last }: { label: string; value: string; last?: boolean }) {
  return (
    <View style={[styles.infoRow, !last && styles.infoRowBorder]}>
      <Text style={styles.infoLabel}>{label}</Text>
      <Text style={styles.infoValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  heading: { fontSize: 22, fontWeight: "800", color: colors.ink },
  profileRow: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  name: { fontSize: 17, fontWeight: "800", color: colors.ink },
  email: { fontSize: 13, color: colors.muted, marginTop: 2 },
  rolePill: {
    alignSelf: "flex-start",
    marginTop: 6,
    backgroundColor: colors.brand100,
    borderRadius: radii.pill,
    paddingVertical: 3,
    paddingHorizontal: 10,
  },
  rolePillText: { fontSize: 11, fontWeight: "800", color: colors.brand800, letterSpacing: 0.5 },
  infoRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: spacing.sm,
  },
  infoRowBorder: { borderBottomWidth: 1, borderBottomColor: colors.border },
  infoLabel: { fontSize: 13, color: colors.muted },
  infoValue: { fontSize: 13, fontWeight: "700", color: colors.ink },
});
