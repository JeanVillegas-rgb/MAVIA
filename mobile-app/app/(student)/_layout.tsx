import React from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { Redirect, Tabs } from "expo-router";
import { Ionicons } from "@expo/vector-icons";

import { useAuth } from "@/auth/AuthContext";
import { colors } from "@/theme";

type IconName = keyof typeof Ionicons.glyphMap;

// Matches the reference layout's bottom bar: a "library" tab (course list ->
// lesson list -> player, nested under home/) and a profile tab.
const TABS: { name: string; title: string; icon: IconName; iconOutline: IconName }[] = [
  { name: "home", title: "Home", icon: "home", iconOutline: "home-outline" },
  { name: "profile", title: "Profile", icon: "person-circle", iconOutline: "person-circle-outline" },
];

export default function StudentLayout() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={colors.brand600} size="large" />
      </View>
    );
  }
  if (!user) {
    return <Redirect href="/login" />;
  }
  if (user.role !== "STUDENT") {
    return <Redirect href="/not-supported" />;
  }

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.brand600,
        tabBarInactiveTintColor: colors.faint,
        tabBarStyle: styles.tabBar,
        tabBarLabelStyle: styles.tabLabel,
      }}
    >
      {TABS.map((tab) => (
        <Tabs.Screen
          key={tab.name}
          name={tab.name}
          options={{
            title: tab.title,
            tabBarIcon: ({ color, focused, size }) => (
              <Ionicons name={focused ? tab.icon : tab.iconOutline} color={color} size={size} />
            ),
          }}
        />
      ))}
    </Tabs>
  );
}

const styles = StyleSheet.create({
  loading: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.page },
  tabBar: {
    borderTopColor: colors.border,
    height: 62,
    paddingBottom: 8,
    paddingTop: 6,
  },
  tabLabel: { fontSize: 11, fontWeight: "700" },
});
