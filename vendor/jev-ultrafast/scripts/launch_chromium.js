const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ["--remote-debugging-port=9333"],
  });
  console.log("chromium launched, connected:", browser.isConnected());
  // Keep the process (and browser) alive until killed externally.
  await new Promise(() => {});
})();
