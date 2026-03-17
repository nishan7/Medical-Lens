import { expect, test, type Page } from '@playwright/test'

async function mockAnalyzeBill(page: Page) {
  await page.route('**/analyze-bill', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        summary: {
          total_charged: 12000,
          total_fair_estimate: 3800,
          potential_savings: 8200,
          savings_percentage: 68.3,
        },
        line_items: [
          {
            description: 'Venipuncture (blood draw)',
            cpt_code: '36415',
            charged_amount: 380,
            fair_price: 12,
            markup_ratio: 31.7,
            price_source: 'hospital_pricing_data:cpt:discounted_cash',
          },
        ],
        issues: [
          {
            type: 'OVERCHARGE',
            severity: 'HIGH',
            item: 'Venipuncture (blood draw)',
            charged: 380,
            fair_price: 12,
            explanation: 'This line item is priced far above the lowest available reference price.',
          },
        ],
        dispute_letter: 'Dear Billing Department,\nPlease review this charge.',
        phone_script: 'Ask for a supervisor and cite the pricing mismatch.',
      }),
    })
  })
}

test('analyze mode loads an example and renders mocked results', async ({ page }) => {
  await mockAnalyzeBill(page)

  await page.goto('/')
  await page.getByRole('button', { name: 'Analyze Mode' }).click()
  await page.getByRole('combobox').selectOption('ER Visit ($12,000)')
  await page.getByRole('button', { name: 'Analyze My Bill' }).click()

  await expect(page.getByText('Potential Savings')).toBeVisible()
  await expect(page.getByText('$8,200')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Dispute Letter' })).toBeVisible()

  await page.getByRole('button', { name: 'Issues' }).click()
  await expect(page.locator('.analyzer-issue-item')).toContainText('Venipuncture (blood draw)')

  await page.getByRole('button', { name: 'Dispute Letter' }).click()
  await expect(page.getByRole('button', { name: 'Download Letter' })).toBeVisible()
  await expect(page.getByText('Dear Billing Department,')).toBeVisible()
})

test('analyze mode can submit an uploaded file without bill text', async ({ page }) => {
  await mockAnalyzeBill(page)

  await page.goto('/')
  await page.getByRole('button', { name: 'Analyze Mode' }).click()
  await page.locator('input[type="file"]').setInputFiles('test_image.jpg')

  await expect(page.getByText('OCR input selected')).toBeVisible()
  await page.getByRole('button', { name: 'Analyze My Bill' }).click()

  await expect(page.getByText('Potential Savings')).toBeVisible()
  await expect(page.getByText('$8,200')).toBeVisible()
})
