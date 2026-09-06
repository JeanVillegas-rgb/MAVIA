// 10.0.2.2 is the Android emulator's alias for the host machine's localhost.
// - Physical Android device: set EXPO_PUBLIC_API_BASE_URL to your machine's
//   LAN IP, e.g. `EXPO_PUBLIC_API_BASE_URL=http://192.168.1.20:8000/api`
//   (create mobile-app/.env with that line, or export it before `npm start`).
// - iOS simulator: `http://127.0.0.1:8000/api` works directly.
export const API_BASE_URL =
  process.env.EXPO_PUBLIC_API_BASE_URL ?? "http://10.0.2.2:8000/api";

// Where the "verify your email" link in the email points (the web-app).
// Only used for display copy here — the link itself is baked in by the
// backend's FRONTEND_URL setting.
export const WEB_APP_URL =
  process.env.EXPO_PUBLIC_WEB_APP_URL ?? "http://10.0.2.2:5173";
