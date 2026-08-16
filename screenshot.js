const { chromium } = require('playwright');

(async () => {
  const frontendUrl = process.env.FRONTEND_URL || 'http://localhost:5173';
  const screenshotPath = process.env.SCREENSHOT_PATH || 'test.png';
  const browser = await chromium.launch();

  try {
    const page = await browser.newPage();
    await page.goto(frontendUrl, { waitUntil: 'networkidle' });
    await page.screenshot({ path: screenshotPath, fullPage: true });
    console.log(`Screenshot saved as ${screenshotPath}`);
  } catch (error) {
    console.error('Error taking screenshot:', error);
  } finally {
    await browser.close();
  }
})();
