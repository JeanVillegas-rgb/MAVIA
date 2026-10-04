import React, { useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { Link } from "expo-router";

import ScreenContainer from "@/components/ScreenContainer";
import TextField from "@/components/TextField";
import Button from "@/components/Button";
import Card from "@/components/Card";
import { resendVerification } from "@/api/client";
import { colors, spacing } from "@/theme";

// The verification link itself opens in the phone's browser (it points at
// the web-app, per the backend's FRONTEND_URL) — this screen only covers
// resending it.
export default function VerifyEmailScreen() {
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function handleResend() {
    if (!/\S+@\S+\.\S+/.test(email)) {
      setError("Enter a valid email address.");
      return;
    }
    setError("");
    setSending(true);
    try {
      const res = await resendVerification(email);
      setMessage(res.message || "If that account exists and is unverified, a new link is on its way.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not resend. Try again.");
    } finally {
      setSending(false);
    }
  }

  return (
    <ScreenContainer>
      <Card>
        <Text style={styles.eyebrow}>Resend</Text>
        <Text style={styles.title}>Verification email</Text>
        <Text style={styles.subtitle}>
          Enter the email you signed up with and we&apos;ll send a fresh verification
          link. Open it on this phone to activate your account.
        </Text>

        {message ? (
          <Text style={styles.message}>{message}</Text>
        ) : (
          <View style={{ marginTop: spacing.md }}>
            <TextField
              label="Your email"
              keyboardType="email-address"
              autoCapitalize="none"
              autoCorrect={false}
              value={email}
              onChangeText={(text) => {
                setEmail(text);
                setError("");
              }}
            />
            {error ? <Text style={styles.error}>{error}</Text> : null}
            <Button
              label={sending ? "Sending…" : "Resend verification email"}
              onPress={handleResend}
              loading={sending}
            />
          </View>
        )}

        <View style={styles.footer}>
          <Link href="/login" style={styles.link}>
            Back to log in
          </Link>
        </View>
      </Card>
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
  title: { marginTop: spacing.xs, fontSize: 22, fontWeight: "800", color: colors.ink },
  subtitle: { marginTop: 6, fontSize: 14, color: colors.muted },
  message: { marginTop: spacing.md, fontSize: 14, color: colors.success, fontWeight: "600" },
  error: { fontSize: 13, color: colors.danger, marginBottom: spacing.sm },
  footer: { marginTop: spacing.lg, alignItems: "center" },
  link: { color: colors.brand600, fontWeight: "700", fontSize: 14 },
});
