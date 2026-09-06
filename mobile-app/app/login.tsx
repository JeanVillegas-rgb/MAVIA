import React, { useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { Link, Redirect, useRouter } from "expo-router";

import ScreenContainer from "@/components/ScreenContainer";
import TextField from "@/components/TextField";
import Button from "@/components/Button";
import { useAuth } from "@/auth/AuthContext";
import { colors, spacing } from "@/theme";

export default function LoginScreen() {
  const { user, loading, login } = useAuth();
  const router = useRouter();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  // Already signed in -> let the index route re-decide where to send them.
  if (!loading && user) {
    return <Redirect href="/" />;
  }

  async function handleSubmit() {
    setError("");
    setSubmitting(true);
    try {
      await login(username, password);
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <ScreenContainer>
      <View style={styles.brandRow}>
        <View style={styles.dot} />
        <Text style={styles.brand}>MAVIA</Text>
      </View>

      <Text style={styles.title}>Welcome back</Text>
      <Text style={styles.subtitle}>Log in to keep exploring your lessons.</Text>

      <View style={styles.form}>
        <TextField
          label="Username"
          autoCapitalize="none"
          autoCorrect={false}
          value={username}
          onChangeText={setUsername}
        />
        <TextField
          label="Password"
          secureTextEntry
          autoCapitalize="none"
          value={password}
          onChangeText={setPassword}
        />

        {error ? (
          <View style={styles.alert}>
            <Text style={styles.alertText}>{error}</Text>
          </View>
        ) : null}

        <Button
          label={submitting ? "Logging in…" : "Log in"}
          onPress={handleSubmit}
          loading={submitting}
          disabled={!username || !password}
          style={{ marginTop: spacing.sm }}
        />
      </View>

      <View style={styles.footer}>
        <Text style={styles.footerText}>
          New to MAVIA?{" "}
          <Link href="/register" style={styles.link}>
            Create an account
          </Link>
        </Text>
        <Text style={[styles.footerText, { marginTop: spacing.xs }]}>
          Didn&apos;t get the verification email?{" "}
          <Link href="/verify-email" style={styles.link}>
            Resend it
          </Link>
        </Text>
      </View>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  brandRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, marginBottom: spacing.xl },
  dot: { width: 10, height: 10, borderRadius: 5, backgroundColor: colors.brand600 },
  brand: { fontSize: 16, fontWeight: "800", letterSpacing: 3, color: colors.brand900 },
  title: { fontSize: 26, fontWeight: "800", color: colors.ink },
  subtitle: { marginTop: 6, fontSize: 15, color: colors.muted, marginBottom: spacing.xl },
  form: { marginTop: spacing.sm },
  alert: {
    backgroundColor: colors.dangerBg,
    borderRadius: 10,
    padding: spacing.sm,
    marginBottom: spacing.sm,
  },
  alertText: { color: colors.danger, fontSize: 13 },
  footer: { marginTop: spacing.xl, alignItems: "center" },
  footerText: { fontSize: 14, color: colors.muted },
  link: { color: colors.brand600, fontWeight: "700" },
});
