const tabs = [...document.querySelectorAll('[data-contract-tab]')];
function chooseContract(id, focus = false) {
  const disclosure = document.getElementById('interfaces');
  if (disclosure?.tagName === 'DETAILS') disclosure.open = true;
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

const supportTabs = [...document.querySelectorAll('[data-support-tab]')];
function chooseSupport(index, focus = false) {
  supportTabs.forEach((tab, position) => {
    const selected = position === index;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    const panel = document.getElementById(tab.getAttribute('aria-controls'));
    if (panel) panel.hidden = !selected;
    if (selected && focus) tab.focus();
  });
}
supportTabs.forEach((tab, index) => {
  tab.addEventListener('click', () => chooseSupport(index));
  tab.addEventListener('keydown', event => {
    const target = event.key === 'ArrowRight' ? (index + 1) % supportTabs.length
      : event.key === 'ArrowLeft' ? (index - 1 + supportTabs.length) % supportTabs.length
      : event.key === 'Home' ? 0 : event.key === 'End' ? supportTabs.length - 1 : undefined;
    if (target !== undefined) { event.preventDefault(); chooseSupport(target, true); }
  });
});
