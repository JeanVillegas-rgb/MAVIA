import React from "react";
import { Stack } from "expo-router";

// Nested inside the "Home" tab: course list -> lesson list -> player. The
// parent Tabs layout keeps the bottom tab bar visible across all of these.
export default function HomeStackLayout() {
  return <Stack screenOptions={{ headerShown: false }} />;
}
