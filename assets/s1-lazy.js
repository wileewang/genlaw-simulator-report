// The overview loads material meshes only when its inline replay is opened.
const root = document.querySelector('[data-s1-viewer]');
if (root) {
  const begin = () => import('./s1-viewer.js').catch(error => {
    root.querySelector('.viewer-loading').textContent = '回放加载失败，可查看上方形状图片和下载结果。';
    window.__S1_VIEWER__ = { ready: false, error: String(error) };
  });
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) {
        observer.disconnect();
        begin();
      }
    });
    observer.observe(root);
  } else begin();
}
