// Light, dark, or the computer's setting. The page is served already marked with your choice
// (data-theme-choice on <html>), and a tiny script in index.html sets data-theme before the first
// paint. This keeps it right afterwards: when you change it in Settings, or when the computer
// switches between light and dark while you've chosen to follow it.
export type ThemeChoice = "system" | "light" | "dark";

const media = window.matchMedia?.("(prefers-color-scheme: dark)");

function resolve(choice: ThemeChoice) {
  return choice === "system" ? (media?.matches ? "dark" : "light") : choice;
}

export function currentChoice(): ThemeChoice {
  const c = document.documentElement.getAttribute("data-theme-choice");
  return c === "light" || c === "dark" ? c : "system";
}

export function applyTheme(choice: ThemeChoice) {
  const root = document.documentElement;
  root.setAttribute("data-theme-choice", choice);
  root.setAttribute("data-theme", resolve(choice));
}

media?.addEventListener?.("change", () => {
  if (currentChoice() === "system") applyTheme("system");
});
