import { useTheme } from "../hooks/useTheme";
import { useT } from "../hooks/useT";
import { IconSun, IconMoon } from "./Icons";

export function ThemeToggle() {
  const [theme, toggle] = useTheme();
  const t = useT();
  const label = theme === "light" ? "Switch to dark mode" : "Switch to light mode";
  return (
    <button
      className="theme-toggle"
      onClick={toggle}
      title={label}
      aria-label={label}
      aria-pressed={theme === "dark"}
    >
      <span aria-hidden="true">
        {theme === "light" ? <IconMoon /> : <IconSun />}
      </span>
    </button>
  );
}
