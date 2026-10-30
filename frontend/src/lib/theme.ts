/** Light or dark follows the system setting: the `dark` class on <html>, which shadcn/ui's theme reads. */
export function followSystemTheme(): () => void {
  const query = window.matchMedia('(prefers-color-scheme: dark)')
  const apply = () => document.documentElement.classList.toggle('dark', query.matches)
  apply()
  query.addEventListener('change', apply)
  return () => query.removeEventListener('change', apply)
}
