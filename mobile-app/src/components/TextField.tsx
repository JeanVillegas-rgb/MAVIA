import React from "react";
import { StyleSheet, Text, TextInput, TextInputProps, View } from "react-native";

import { liveRegion } from "@/hooks/useAnnouncement";
import { colors, radii, spacing } from "@/theme";

type Props = TextInputProps & {
  label: string;
  error?: string;
};

export default function TextField({ label, error, style, ...rest }: Props) {
  return (
    <View style={styles.field}>
      {/* The input carries the label for screen readers; reading it here too would say it twice. */}
      <Text style={styles.label} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
        {label}
      </Text>
      <TextInput
        accessibilityLabel={label}
        accessibilityHint={error}
        placeholderTextColor={colors.faint}
        style={[styles.input, error ? styles.inputError : null, style]}
        {...rest}
      />
      {error ? <Text style={styles.error} {...liveRegion}>{error}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  field: { gap: spacing.xs, marginBottom: spacing.md },
  label: { fontSize: 13, fontWeight: "700", color: colors.brand800 },
  input: {
    borderWidth: 1,
    borderColor: colors.borderStrong,
    borderRadius: radii.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: 12,
    fontSize: 16,
    color: colors.ink,
    backgroundColor: colors.surface,
  },
  inputError: { borderColor: colors.danger },
  error: { fontSize: 12, color: colors.danger },
});
