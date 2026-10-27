const tabs = [...document.querySelectorAll('[data-contract-tab]')];
function chooseContract(id, focus = false) {
  for (const tab of tabs) {
    const active = tab.dataset.contractTab === id;
    tab.setAttribute('aria-selected', String(active));
    tab.tabIndex = active ? 0 : -1;
    const panel = document.getElementById(tab.getAttribute('aria-controls'));
    if (panel) panel.hidden = !active;
    if (active && focus) tab.focus();
  }
}
tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => chooseContract(tab.dataset.contractTab));
  tab.addEventListener('keydown', event => {
    let target;
    if (event.key === 'ArrowRight') target = (index + 1) % tabs.length;
    if (event.key === 'ArrowLeft') target = (index - 1 + tabs.length) % tabs.length;
    if (event.key === 'Home') target = 0;
    if (event.key === 'End') target = tabs.length - 1;
    if (target !== undefined) { event.preventDefault(); chooseContract(tabs[target].dataset.contractTab, true); }
  });
});
document.querySelectorAll('[data-open-contract]').forEach(link => {
  link.addEventListener('click', () => chooseContract(link.dataset.openContract));
});
if (tabs.length && location.hash.startsWith('#interface-')) {
  const id = location.hash.slice('#interface-'.length);
  if (tabs.some(tab => tab.dataset.contractTab === id)) chooseContract(id);
}
