import { useTheme } from "../hooks/useTheme";
import { IconSun, IconMoon } from "./Icons";

export function ThemeToggle() {
  const [theme, toggle] = useTheme();
  return (
    <button
      className="theme-toggle"
      onClick={toggle}
      title={theme === "light" ? "切换到暗色模式" : "切换到亮色模式"}
      aria-label={theme === "light" ? "Switch to dark mode" : "Switch to light mode"}
      aria-pressed={theme === "dark"}
    >
      <span aria-hidden="true">
        {theme === "light" ? <IconMoon /> : <IconSun />}
      </span>
    </button>
  );
}
