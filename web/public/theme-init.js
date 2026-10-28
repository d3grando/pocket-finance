// Runs before first paint (no theme flash). Mirrors src/lib/theme.ts — localStorage `pf.theme`.
(function () {
  var t = null
  try { t = localStorage.getItem('pf.theme') } catch (e) {}
  var dark = t === 'dark' || (t !== 'light' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
  if (dark) document.documentElement.classList.add('dark')
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
  // status bar follows the chosen theme, not only the system one (lib/theme.ts THEME_COLOR)
  var metas = document.querySelectorAll('meta[name="theme-color"]')
  for (var i = 0; i < metas.length; i++) metas[i].setAttribute('content', dark ? '#0d0e12' : '#5d4ed6')
})()
