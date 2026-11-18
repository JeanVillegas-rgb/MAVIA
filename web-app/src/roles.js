// Role constants and helpers shared across routing and UI.

export const ROLES = {
  STUDENT: "STUDENT",
  TEACHER: "TEACHER",
  ADMIN: "ADMIN",
};

// Roles a visitor is allowed to self-register as on the web app. Students
// register and log in from the MAVIA mobile app instead; admins are
// provisioned, not self-registered anywhere.
export const SELF_SIGNUP_ROLES = [ROLES.TEACHER];

export const ROLE_LABEL = {
  STUDENT: "Student",
  TEACHER: "Teacher",
  ADMIN: "Admin",
};

// Landing route for a user once authenticated.
export function homePathForRole(role) {
  switch (role) {
    case ROLES.ADMIN:
      return "/admin";
    case ROLES.TEACHER:
      return "/teacher";
    case ROLES.STUDENT:
      return "/student";
    default:
      return "/";
  }
}

export function initialsFor(user) {
  if (!user) return "?";
  const source =
    [user.first_name, user.last_name].filter(Boolean).join(" ") ||
    user.username ||
    "?";
  return source
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
}

export function displayName(user) {
  if (!user) return "";
  return (
    [user.first_name, user.last_name].filter(Boolean).join(" ") || user.username
  );
}
