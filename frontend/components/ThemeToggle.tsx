// app/components/ThemeToggle.tsx
"use client";

import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  // Évite le mismatch d'hydration : on ne connaît le thème réel
  // qu'une fois monté côté client.
  useEffect(() => setMounted(true), []);

  if (!mounted) {
    return <div className="theme-toggle theme-toggle--skeleton" />;
  }

  const isDark = theme === "dark";

  return (
    <button
      type="button"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      className="theme-toggle"
      aria-label={isDark ? "Passer en thème clair" : "Passer en thème sombre"}
    >
      <span className="theme-toggle__icon">{isDark ? "☾" : "☼"}</span>
      <span className="theme-toggle__label">{isDark ? "Sombre" : "Clair"}</span>
    </button>
  );
}