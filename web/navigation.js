// Keep the workflow focused while retaining deep links to each section.
function revealSection(id, focus = true) {
  const target = document.getElementById(id) || document.getElementById('run');
  const view = target.closest('[data-view]');
  if (!view) return;
  document.querySelectorAll('[data-view]').forEach(item => { item.hidden = item !== view; });
  document.querySelectorAll('nav a').forEach(link => {
    const active = document.querySelector(link.getAttribute('href'))?.closest('[data-view]') === view;
    if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  });
  if (focus) {
    const heading = target.querySelector('h2') || target;
    heading.setAttribute('tabindex', '-1'); heading.focus({preventScroll: true});
    target.scrollIntoView({behavior: 'instant', block: 'start'});
  }
}
function goTo(id) {
  if (location.hash === '#' + id) revealSection(id);
  else location.hash = id;
}
window.addEventListener('hashchange', () => revealSection(location.hash.slice(1)));
document.addEventListener('click', event => {
  const link = event.target.closest('a[href^="#"]');
  if (link && link.hash !== '#main') { event.preventDefault(); goTo(link.hash.slice(1)); }
});
revealSection(location.hash.slice(1) || 'run', false);
