import React, { useEffect, useRef } from "react";
import { TextInput, StyleSheet } from "react-native";

interface HiddenHardwareInputProps {
  onKey: (char: string) => void;
  onSubmit: () => void;
}

export default function HiddenHardwareInput({
  onKey,
  onSubmit,
}: HiddenHardwareInputProps) {
  const inputRef = useRef<TextInput>(null);

  useEffect(() => {
    const t = setTimeout(() => inputRef.current?.focus(), 50);
    return () => clearTimeout(t);
  }, []);

  function handleChangeText(text: string) {
    if (text.length === 0) return;

    // Take the last character typed (covers the rare case where
    // onChangeText batches more than one keystroke) and forward it.
    const char = text[text.length - 1];
    onKey(char);

    // Always clear -- this field is never meant to hold visible text.
    inputRef.current?.clear();
  }

  return (
    <TextInput
      ref={inputRef}
      value=""
      onChangeText={handleChangeText}
      onSubmitEditing={onSubmit}
      onBlur={() => {
        // The numpad should always be "listening" -- if focus is lost
        // for any reason, grab it back.
        setTimeout(() => inputRef.current?.focus(), 50);
      }}
      autoFocus
      blurOnSubmit={false}
      showSoftInputOnFocus={false}
      caretHidden
      style={styles.hidden}
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
    />
  );
}

const styles = StyleSheet.create({
  hidden: {
    position: "absolute",
    top: -1000,
    left: 0,
    width: 1,
    height: 1,
    opacity: 0,
  },
});