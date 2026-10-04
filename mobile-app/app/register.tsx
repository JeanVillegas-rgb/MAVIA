import React, { useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { Link, useRouter } from "expo-router";

import ScreenContainer from "@/components/ScreenContainer";
import TextField from "@/components/TextField";
import Button from "@/components/Button";
import Card from "@/components/Card";
import { register } from "@/api/client";
import { colors, spacing } from "@/theme";

export default function RegisterScreen() {
  const router = useRouter();

  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  async function handleSubmit() {
    setError("");
    setSubmitting(true);
    try {
      // This app is the student experience, so role is fixed — no picker.
      await register({
        username,
        email,
        password,
        first_name: firstName,
        last_name: lastName,
        role: "STUDENT",
      });
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <ScreenContainer>
        <Card style={{ alignItems: "center" }}>
          <Text style={styles.eyebrow}>Check your inbox</Text>
          <Text style={[styles.title, { textAlign: "center" }]}>Verify your email</Text>
          <Text style={[styles.subtitle, { textAlign: "center" }]}>
            We sent a verification link to{"\n"}
            <Text style={{ fontWeight: "700", color: colors.ink }}>{email}</Text>. Open it on
            this phone to activate your account, then log in.
          </Text>
          <Button
            label="Go to log in"
            onPress={() => router.replace("/login")}
            style={{ marginTop: spacing.lg, width: "100%" }}
          />
        </Card>
      </ScreenContainer>
    );
  }

  return (
    <ScreenContainer>
      <Text style={styles.eyebrow}>Student sign up</Text>
      <Text style={styles.title}>Create your account</Text>
      <Text style={styles.subtitle}>Learn science through touch and sound.</Text>

      <View style={styles.row}>
        <TextField
          label="First name"
          value={firstName}
          onChangeText={setFirstName}
          style={styles.half}
        />
        <TextField
          label="Last name"
          value={lastName}
          onChangeText={setLastName}
          style={styles.half}
        />
      </View>

      <TextField
        label="Username"
        autoCapitalize="none"
        autoCorrect={false}
        value={username}
        onChangeText={setUsername}
      />
      <TextField
        label="Email"
        keyboardType="email-address"
        autoCapitalize="none"
        autoCorrect={false}
        value={email}
        onChangeText={setEmail}
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
        label={submitting ? "Creating account…" : "Create account"}
        onPress={handleSubmit}
        loading={submitting}
        disabled={!username || !email || !password}
        style={{ marginTop: spacing.sm }}
      />

      <View style={styles.footer}>
        <Text style={styles.footerText}>
          Already have an account?{" "}
          <Link href="/login" style={styles.link}>
            Log in
          </Link>
        </Text>
      </View>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  eyebrow: {
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 2,
    textTransform: "uppercase",
    color: colors.faint,
  },
  title: { marginTop: spacing.xs, fontSize: 24, fontWeight: "800", color: colors.ink },
  subtitle: { marginTop: 6, fontSize: 14, color: colors.muted, marginBottom: spacing.lg },
  row: { flexDirection: "row", gap: spacing.md },
  half: { flex: 1 },
  alert: {
    backgroundColor: colors.dangerBg,
    borderRadius: 10,
    padding: spacing.sm,
    marginBottom: spacing.sm,
  },
  alertText: { color: colors.danger, fontSize: 13 },
  footer: { marginTop: spacing.lg, alignItems: "center" },
  footerText: { fontSize: 14, color: colors.muted },
  link: { color: colors.brand600, fontWeight: "700" },
});
