// Open an inline result directly, including when arriving via an old bookmark.
function reveal(hash, scroll = false) {
  if (!hash) return;
  const target = document.getElementById(decodeURIComponent(hash.slice(1)));
  if (!target) return;
  if (target.id.startsWith('entry-') || ['equipment', 'parts', 'materials', 'inspection', 'main'].includes(target.id)) {
    document.querySelectorAll('[data-s4-report]').forEach(report => { report.open = false; });
  }
  for (let node = target; node; node = node.parentElement) {
    if (node.tagName === 'DETAILS') node.open = true;
  }
  if (scroll) requestAnimationFrame(() => target.scrollIntoView({block:'start', behavior:'instant'}));
}
document.addEventListener('click', event => {
  const link = event.target.closest('a[href^="#"]');
  if (link) reveal(link.hash);
});
addEventListener('hashchange', () => reveal(location.hash, true));
reveal(location.hash, true);
