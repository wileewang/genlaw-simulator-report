import './s1-lazy.js';

const scope = document.querySelector('#indexed-setups');
const root = scope?.querySelector('[data-indexed-viewer]');
if (root) {
  let promise;
  const begin = () => promise ||= import('./s1-viewer-core.js').then(({mountViewer}) =>
    mountViewer(root, {scope, selectSelector:'#indexed-case-select', qaKey:'__INDEXED_VIEWER__', processSelector:'#indexed-playback'})
  ).catch(error => {
    root.querySelector('.viewer-loading').textContent = '回放加载失败，可查看上方形状图片和下载结果。';
    window.__INDEXED_VIEWER__ = {ready:false, error:String(error)};
  });
  scope.querySelectorAll('[data-open-indexed-case]').forEach(button => button.addEventListener('click', async () => {
    const playback = scope.querySelector('#indexed-playback');
    playback.open = true;
    playback.scrollIntoView({block:'start', behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth'});
    await begin();
    if (!window.__INDEXED_VIEWER__?.ready) return;
    const select = scope.querySelector('#indexed-case-select');
    select.value = button.dataset.openIndexedCase;
    select.dispatchEvent(new Event('change'));
  }));
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { observer.disconnect(); begin(); }
    });
    observer.observe(root);
  } else begin();
}
