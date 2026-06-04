import { useTheme } from "../hooks/useTheme";
import { IconSun, IconMoon } from "./Icons";

export function ThemeToggle() {
  const [theme, toggle] = useTheme();
  return (
    <button
      className="theme-toggle"
      onClick={toggle}
      title={theme === "light" ? "Switch to dark mode" : "Switch to light mode"}
      aria-label={theme === "light" ? "Switch to dark mode" : "Switch to light mode"}
      aria-pressed={theme === "dark"}
    >
      <span aria-hidden="true">
        {theme === "light" ? <IconMoon /> : <IconSun />}
      </span>
    </button>
  );
}
