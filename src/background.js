chrome.action.onClicked.addListener(async () => {
  await chrome.runtime.openOptionsPage();
});