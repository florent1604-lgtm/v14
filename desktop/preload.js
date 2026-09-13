const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('titaniumDesktop', Object.freeze({
  platform: 'windows',
  shell: 'electron',
  readOnlyUi: true,
}));
