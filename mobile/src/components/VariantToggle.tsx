import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { Variant } from "@/data/types";
import { colors, fonts, radii } from "@/theme";

const LABELS: Record<Variant, string> = {
  NORMAL: "Normal",
  SIMPLIFIED: "Simplified",
  ELABORATED: "Elaborated",
};

type Props = {
  available: Variant[];
  selected: Variant;
  onSelect: (v: Variant) => void;
};

export default function VariantToggle({ available, selected, onSelect }: Props) {
  return (
    <View style={styles.wrap}>
      {available.map((v) => {
        const active = v === selected;
        return (
          <Pressable
            key={v}
            onPress={() => onSelect(v)}
            style={[styles.pill, active && styles.pillActive]}
          >
            <Text style={[styles.label, active && styles.labelActive]}>{LABELS[v]}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    flexDirection: "row",
    backgroundColor: colors.card,
    borderRadius: radii.pill,
    padding: 4,
  },
  pill: {
    paddingVertical: 8,
    paddingHorizontal: 14,
    borderRadius: radii.pill,
  },
  pillActive: {
    backgroundColor: colors.maroon900,
  },
  label: {
    fontFamily: fonts.bodySemi,
    fontSize: 12,
    color: colors.inkSoft,
  },
  labelActive: {
    color: colors.white,
  },
});
