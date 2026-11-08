import React, {useEffect, useState} from 'react';

export default function ThemeChoice() {
  const [preference, setPreference] = useState(() => document.documentElement.dataset.themePreference || 'system');
  useEffect(() => {
    const system = window.matchMedia('(prefers-color-scheme: dark)');
    const apply = () => {
      const theme = preference === 'system' ? (system.matches ? 'dark' : 'light') : preference;
      document.documentElement.dataset.theme = theme;
      document.documentElement.dataset.themePreference = preference;
      document.documentElement.style.colorScheme = theme;
    };
    apply();
    system.addEventListener('change', apply);
    return () => system.removeEventListener('change', apply);
  }, [preference]);

  function choose(event) {
    const next = event.target.value;
    setPreference(next);
    try { localStorage.setItem('personal-coach-theme', next); } catch { /* Keep the in-page choice usable. */ }
  }

  return <label className="theme-choice" htmlFor="theme-choice">Appearance
    <select id="theme-choice" value={preference} onChange={choose}>
      <option value="system">System</option>
      <option value="light">Light</option>
      <option value="dark">Dark</option>
    </select>
  </label>;
}
