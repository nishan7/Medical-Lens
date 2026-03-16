import { test, expect } from '@playwright/test';

test('has title and elements', async ({ page }) => {
    await page.goto('/');

    // Expect a title "to contain" a substring.
    await expect(page.locator('h1')).toHaveText('RightCost');

    // Verify the input is visible
    await expect(page.locator('textarea')).toBeVisible();

    // Verify the send button is visible
    await expect(page.locator('button[type="submit"]')).toBeVisible();
});

test('can send a message and receive response', async ({ page }) => {
    await page.goto('/');

    const textarea = page.locator('textarea');
    await textarea.fill('Hello, how are you?');

    const sendButton = page.locator('button[type="submit"]');
    await sendButton.click();

    // The chat window should eventually have a user bubble
    const userBubble = page.locator('.bubble-user').first();
    await expect(userBubble).toContainText('Hello, how are you?');

    // And an assistant bubble
    const assistantBubble = page.locator('.bubble-assistant').last();
    await assistantBubble.waitFor({ state: 'visible' });

    // The send button should be disabled while sending, wait for it to be enabled
    await expect(sendButton).toBeEnabled({ timeout: 60000 });

    // There should be some text in the assistant bubble now
    const text = await assistantBubble.innerText();
    expect(text.length).toBeGreaterThan(0);
});

test('can upload image and send', async ({ page }) => {
    await page.goto('/');

    const fileInput = page.locator('input[type="file"]');
    // Upload our tests fake image
    await fileInput.setInputFiles('test_image.jpg');

    const textarea = page.locator('textarea');
    await textarea.fill('Describe this test image');

    const sendButton = page.locator('button[type="submit"]');
    await sendButton.click();

    // The chat window should eventually have a user bubble with image meta
    const userBubble = page.locator('.bubble-user').last();
    await expect(userBubble).toContainText('Describe this test image');
    await expect(page.locator('.bubble-meta').last()).toContainText('test_image.jpg');

    // Assistant bubble
    const assistantBubble = page.locator('.bubble-assistant').last();
    await assistantBubble.waitFor({ state: 'visible' });

    // wait for response
    await expect(sendButton).toBeEnabled({ timeout: 60000 });
    const text = await assistantBubble.innerText();
    expect(text.length).toBeGreaterThan(0);
});
