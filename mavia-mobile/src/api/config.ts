import { Platform } from "react-native";

const DEFAULT_WEB_URL = "http://localhost:8000";
const DEFAULT_ANDROID_EMULATOR_URL = "http://10.0.2.2:8000";

function defaultBaseUrl() {
  return Platform.OS === "android" ? DEFAULT_ANDROID_EMULATOR_URL : DEFAULT_WEB_URL;
}

export const API_BASE_URL = process.env.EXPO_PUBLIC_API_BASE_URL ?? defaultBaseUrl();

// The course endpoints now require an authenticated request (a teammate
// added token auth + roles in the `user` app). There's no login screen yet,
// so we log in once with a fixed dev account — swap this out once real
// auth UI exists.
export const DEV_CREDENTIALS = {
  username: "dev-student",
  password: "DevStudent!2026",
};
