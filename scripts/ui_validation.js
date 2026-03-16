const fs = require('fs')
const { chromium } = require('../frontend/node_modules/playwright')

const UI_URL = process.env.UI_URL || 'http://127.0.0.1:5173'
const CLAIMS_PATH = process.env.CLAIMS_PATH || '/Users/nishan/projects/fair/uploads/claims.pdf'
const OUT_PATH = process.env.OUT_PATH || '/tmp/rightcost-validation.json'

const PRICING_CASES = [
  { id: 1, prompt: 'What is the cheapest TB test with insurance?' },
  { id: 2, prompt: 'What is the cheapest TB test for me?' },
  { id: 3, prompt: 'What is the cheapest TB test with Aetna?' },
  { id: 4, prompt: 'What is the cheapest TB test?' },
  { id: 5, prompt: 'What is best price for TB test in San Jose?' },
  { id: 6, prompt: 'what are other hospitals who do TB test' },
]

const BILL_CASES = [
  { id: 7, prompt: 'Summarize this medical bill and list the charges you can identify.' },
  { id: 8, prompt: 'Does this bill look good?' },
  { id: 9, prompt: 'Tell me problems in this bill or inconsistencies.' },
  { id: 10, prompt: 'What should I question or verify on this bill?' },
  { id: 11, prompt: 'What is my total responsibility on this bill?' },
]

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function hasFailureText(text) {
  return /agent failed|tool execution error|need more steps|please retry|api failed/i.test(text)
}

function hasInternalErrorText(text) {
  return /traceback|filenotfounderror|exception|internal error|ocr failed/i.test(text)
}

function detectVisibleStreaming(lengths) {
  const growthSteps = lengths.filter((value, index) => index > 0 && value > lengths[index - 1])
  return growthSteps.length >= 2
}

async function waitForResponse(page, previousAssistantCount, timeoutMs) {
  const start = Date.now()
  const lengths = []
  let stableSince = null

  while (Date.now() - start < timeoutMs) {
    const assistantTexts = await page.locator('.bubble-assistant').allTextContents()
    const currentCount = assistantTexts.length
    const latestText = currentCount > previousAssistantCount ? assistantTexts[currentCount - 1].trim() : ''
    const sendLabel = await page.locator('.send-button').textContent().catch(() => '')
    const hasConnectionError = (await page.locator('text=Connection error while streaming response.').count().catch(() => 0)) > 0
    const isDoneState = String(sendLabel || '').includes('Send')

    lengths.push(latestText.length)

    if (hasConnectionError && latestText.length === 0) {
      return {
        ok: false,
        text: '',
        error: 'connection_error',
        elapsedMs: Date.now() - start,
        lengths,
      }
    }

    if (currentCount > previousAssistantCount && isDoneState) {
      if (stableSince === null) {
        stableSince = Date.now()
      }
      if (Date.now() - stableSince >= 1200) {
        return {
          ok: true,
          text: latestText,
          elapsedMs: Date.now() - start,
          lengths,
        }
      }
    } else {
      stableSince = null
    }

    await sleep(250)
  }

  const assistantTexts = await page.locator('.bubble-assistant').allTextContents()
  const currentCount = assistantTexts.length
  const latestText = currentCount > previousAssistantCount ? assistantTexts[currentCount - 1].trim() : ''

  return {
    ok: false,
    text: latestText,
    error: 'timeout',
    elapsedMs: Date.now() - start,
    lengths,
  }
}

function evaluatePricing(id, text) {
  const lower = text.toLowerCase()
  const failures = []

  if (!text.trim()) failures.push('empty assistant output')
  if (hasFailureText(text)) failures.push('generic failure text')
  if (hasInternalErrorText(text)) failures.push('raw internal error text')

  if (id === 1) {
    const asksInsurance = /insurance|insurer|plan/.test(lower) && (/what|which|depends|assum/.test(lower))
    if (!asksInsurance) failures.push('did not ask insurance follow-up or explain insurer assumptions')
  }

  if (id === 2) {
    const asksInsurance = /insurance|insurer|plan/.test(lower)
    const asksLocation = /where|location|city|zip|state|area/.test(lower)
    if (!(asksInsurance || asksLocation)) failures.push('did not ask follow-up for insurance and/or location')
  }

  if (id === 3) {
    if (!/aetna/.test(lower)) failures.push('missing Aetna-specific answer')
    if (/cigna|blue shield|anthem|united|kaiser|alignment|valley health/.test(lower.replace(/aetna/g, ''))) {
      failures.push('payer substitution detected')
    }
  }

  if (id === 4) {
    if (!(/\$/.test(text) || /cheapest/.test(lower))) failures.push('did not provide direct cheapest result')
  }

  if (id === 5) {
    if (!/san jose/.test(lower)) failures.push('missing San Jose context')
  }

  if (id === 6) {
    if (!(/hospital/.test(lower) || /which city|what city|location/.test(lower))) {
      failures.push('did not list hospitals or ask city clarification')
    }
  }

  return failures
}

function evaluateBill(id, text) {
  const lower = text.toLowerCase()
  const failures = []

  if (!text.trim()) failures.push('empty assistant output')
  if (hasFailureText(text)) failures.push('generic failure text')
  if (hasInternalErrorText(text)) failures.push('raw internal/provider error text')

  if (id === 7) {
    const hasKnownCharges = /heplisav|boostrix|boostrix inj|scvmc|santa clara valley|student health/.test(lower)
    if (!(hasKnownCharges && /\$/.test(text))) failures.push('did not extract identifiable charges and amounts')
  }

  if (id === 8) {
    if (!(/good|looks|reasonable|cannot tell|concern|verify|unclear|not enough/.test(lower))) {
      failures.push('not judgment-oriented')
    }
    if (/identified charges from the bill|summary/.test(lower)) failures.push('repeated summary framing')
    if (!(/verify|unclear|concern|claim|eob|final bill|responsibility|missing/.test(lower))) {
      failures.push('missing caveats or concerns')
    }
  }

  if (id === 9) {
    if (!(/problem|inconsisten|unclear|missing|denied|claim summary|final bill|responsibility|verify/.test(lower))) {
      failures.push('did not identify concrete issues or ambiguities')
    }
    if (/identified charges from the bill/.test(lower)) failures.push('only repeated charge list')
  }

  if (id === 10) {
    if (!(/question|verify|ask|confirm|check|billing/.test(lower))) {
      failures.push('missing actionable follow-up checks')
    }
  }

  if (id === 11) {
    if (!(/responsib|owe|your share|total/.test(lower))) failures.push('did not address patient responsibility')
    if (!(/unclear|missing|not fully shown|based on|appears/.test(lower))) {
      failures.push('did not clearly flag uncertainty')
    }
  }

  return failures
}

async function runPrompt(page, prompt, timeoutMs) {
  await page.locator('textarea').fill(prompt)
  const before = await page.locator('.bubble-assistant').count()
  await page.locator('button.send-button').click()
  const response = await waitForResponse(page, before, timeoutMs)
  const attachmentStatus = await page.locator('.attachment-status').textContent().catch(() => null)
  const bodyText = await page.locator('body').textContent().catch(() => '')
  const connectionError = /Connection error while streaming response\./.test(bodyText)
  return { ...response, attachmentStatus, connectionError }
}

async function main() {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } })
  const results = []

  function flush(results) {
    fs.writeFileSync(OUT_PATH, JSON.stringify({ generatedAt: new Date().toISOString(), results }, null, 2))
  }

  for (const testCase of PRICING_CASES) {
    await page.goto(UI_URL, { waitUntil: 'networkidle' })
    const response = await runPrompt(page, testCase.prompt, 30000)
    const failures = response.ok ? evaluatePricing(testCase.id, response.text) : [response.error || 'unknown error']
    if (response.connectionError && !failures.includes('connection_error')) failures.push('connection_error')
    results.push({
      id: testCase.id,
      category: 'pricing',
      prompt: testCase.prompt,
      ok: failures.length === 0,
      failures,
      responseText: response.text,
      elapsedMs: response.elapsedMs,
      streamLengths: response.lengths,
      streamedVisibly: detectVisibleStreaming(response.lengths),
      attachmentStatus: null,
    })
    flush(results)
    console.log(`completed pricing case ${testCase.id}`)
  }

  await page.goto(UI_URL, { waitUntil: 'networkidle' })
  await page.locator('input[type=\"file\"]').setInputFiles(CLAIMS_PATH)

  for (const testCase of BILL_CASES) {
    const response = await runPrompt(page, testCase.prompt, 40000)
    const failures = response.ok ? evaluateBill(testCase.id, response.text) : [response.error || 'unknown error']
    if (response.connectionError && !failures.includes('connection_error')) failures.push('connection_error')
    results.push({
      id: testCase.id,
      category: 'bill',
      prompt: testCase.prompt,
      ok: failures.length === 0,
      failures,
      responseText: response.text,
      elapsedMs: response.elapsedMs,
      streamLengths: response.lengths,
      streamedVisibly: detectVisibleStreaming(response.lengths),
      attachmentStatus: response.attachmentStatus,
    })
    flush(results)
    console.log(`completed bill case ${testCase.id}`)
  }

  await browser.close()
  flush(results)

  const passCount = results.filter((result) => result.ok).length
  console.log(JSON.stringify({ pass: passCount, total: results.length, outPath: OUT_PATH }, null, 2))
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
