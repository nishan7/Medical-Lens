import { useEffect, useRef, useState } from 'react'

import { getApiBaseUrl } from '../lib/api'

const EXAMPLES: Record<string, string> = {
  'ER Visit ($12,000)': `Date: 2026-01-15 | Provider: General Hospital
Emergency Department Visit Level 5    $4,200
CT Abdomen/Pelvis with Contrast        $3,800
Complete Blood Count (CBC)              $450
Comprehensive Metabolic Panel           $580
Chest X-Ray, 2 Views                   $890
IV Infusion, first hour                $1,200
Venipuncture (blood draw)              $380
Facility Fee                           $500
TOTAL                                  $12,000`,

  'Lab Work ($2,400)': `Date: 2026-03-01 | Provider: Quest Diagnostics
Comprehensive Metabolic Panel    $380
Complete Blood Count             $285
Lipid Panel                      $340
Hemoglobin A1C                   $290
Thyroid Panel (TSH, T3, T4)      $520
Urinalysis                       $185
Venipuncture                     $180
Specimen handling fee            $220
TOTAL                            $2,400`,

  'Outpatient Surgery ($8,500)': `Date: 2026-02-10 | Provider: Surgical Center
Knee Arthroscopy with Meniscectomy   $4,500
Anesthesia, knee procedure           $2,200
Pre-operative blood panel            $680
Post-operative office visit          $620
Surgical supplies                    $500
TOTAL                                $8,500`,
}

type AnalysisSummary = {
  total_charged: number
  total_fair_estimate: number
  potential_savings: number
  savings_percentage: number
}

type AnalysisLineItem = {
  description: string
  cpt_code?: string | null
  charged_amount: number
  fair_price?: number | null
  markup_ratio?: number | null
  price_source: string
}

type AnalysisIssue = {
  type: string
  severity: string
  item: string
  explanation: string
  charged?: number | null
  fair_price?: number | null
}

type AnalysisResult = {
  summary: AnalysisSummary
  line_items: AnalysisLineItem[]
  issues: AnalysisIssue[]
  dispute_letter: string
  phone_script: string
}

type AnalyzerTab = 'summary' | 'issues' | 'letter' | 'phone'

const STEP_MESSAGES = [
  'Parsing line items...',
  'Looking up fair prices...',
  'Analyzing for billing issues...',
  'Generating dispute package...',
]

function formatCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return '-'
  }
  return `$${value.toLocaleString()}`
}

export function BillAnalyzer() {
  const [selectedExample, setSelectedExample] = useState('')
  const [billText, setBillText] = useState('')
  const [attachedFile, setAttachedFile] = useState<File | null>(null)
  const [loading, setLoading] = useState(false)
  const [stepMessage, setStepMessage] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [activeTab, setActiveTab] = useState<AnalyzerTab>('summary')
  const intervalRef = useRef<number | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    return () => {
      if (intervalRef.current !== null) {
        window.clearInterval(intervalRef.current)
      }
    }
  }, [])

  function clearAttachedFile() {
    setAttachedFile(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  function buildStepMessages(hasFile: boolean): string[] {
    if (hasFile) {
      return [
        'Extracting text from the attached bill...',
        'Parsing line items...',
        'Looking up fair prices...',
        'Generating dispute package...',
      ]
    }
    return STEP_MESSAGES
  }

  async function analyzeBill() {
    if (!billText.trim() && !attachedFile) {
      return
    }

    setLoading(true)
    setError(null)
    setResult(null)
    setActiveTab('summary')
    const messages = buildStepMessages(Boolean(attachedFile))
    setStepMessage(messages[0])

    let nextStepIndex = 0
    if (intervalRef.current !== null) {
      window.clearInterval(intervalRef.current)
    }
    intervalRef.current = window.setInterval(() => {
      nextStepIndex = Math.min(nextStepIndex + 1, messages.length - 1)
      setStepMessage(messages[nextStepIndex])
    }, 1800)

    try {
      const formData = new FormData()
      if (billText.trim()) {
        formData.append('bill_text', billText)
      }
      if (attachedFile) {
        formData.append('file', attachedFile)
      }

      const response = await fetch(`${getApiBaseUrl()}/analyze-bill`, {
        method: 'POST',
        body: formData,
      })

      if (!response.ok) {
        let detail = `Server error: ${response.status}`
        try {
          const payload = await response.json()
          detail = payload?.detail || detail
        } catch {
          // keep default detail
        }
        throw new Error(detail)
      }

      const payload = (await response.json()) as AnalysisResult
      setResult(payload)
      setStepMessage('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Analysis failed.')
    } finally {
      if (intervalRef.current !== null) {
        window.clearInterval(intervalRef.current)
        intervalRef.current = null
      }
      setLoading(false)
    }
  }

  function downloadLetter() {
    if (!result) {
      return
    }

    const blob = new Blob([result.dispute_letter], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'medical-lens-dispute-letter.txt'
    document.body.appendChild(anchor)
    anchor.click()
    document.body.removeChild(anchor)
    URL.revokeObjectURL(url)
  }

  return (
    <section className="analyzer-shell">
      <div className="analyzer-header">
        <h2>Autonomous Bill Analyzer</h2>
        <p>
          Load a demo example, paste an itemized bill, or upload an image/PDF. The agent runs
          OCR when needed, then handles parsing, pricing, issue detection, and dispute generation
          in one pass.
        </p>
      </div>

      <div className="analyzer-controls">
        <label className="analyzer-field">
          <span>Demo example</span>
          <select
            className="analyzer-select"
            value={selectedExample}
            onChange={(event) => {
              const value = event.target.value
              setSelectedExample(value)
              if (value) {
                setBillText(EXAMPLES[value])
                clearAttachedFile()
              }
            }}
          >
            <option value="">Choose an example</option>
            {Object.keys(EXAMPLES).map((label) => (
              <option key={label} value={label}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="attachment-row analyzer-attachment-row">
        <label className="file-button">
          <input
            ref={fileInputRef}
            className="file-input"
            type="file"
            accept="image/jpeg,image/png,image/webp,application/pdf"
            onChange={(event) => {
              const nextFile = event.target.files?.[0] || null
              setAttachedFile(nextFile)
            }}
          />
          {attachedFile ? 'Replace bill file' : 'Attach bill image/PDF'}
        </label>
        {attachedFile ? (
          <div className="attachment-chip">
            <span className="attachment-name">{attachedFile.name}</span>
            <span className="attachment-status">OCR input selected</span>
            <button type="button" className="chip-remove" onClick={clearAttachedFile}>
              Remove
            </button>
          </div>
        ) : null}
      </div>
      <p className="analyzer-help">
        {attachedFile
          ? 'The attached file will be analyzed first. Pasted text is optional while a file is attached.'
          : 'You can either paste bill text or attach a bill image/PDF for OCR analysis.'}
      </p>

      <label className="analyzer-field">
        <span>Bill text</span>
        <textarea
          className="analyzer-textarea"
          rows={11}
          placeholder="Paste an itemized medical bill here..."
          value={billText}
          onChange={(event) => setBillText(event.target.value)}
        />
      </label>

      <div className="analyzer-actions">
        <button
          className="analyzer-primary"
          type="button"
          disabled={loading || (!billText.trim() && !attachedFile)}
          onClick={analyzeBill}
        >
          {loading ? 'Running Analysis...' : 'Analyze My Bill'}
        </button>
        {stepMessage ? <div className="analyzer-step">{stepMessage}</div> : null}
      </div>

      {error ? <div className="analyzer-error">{error}</div> : null}

      {result ? (
        <div className="analyzer-results">
          <div className="analyzer-tabs" role="tablist" aria-label="Analysis results">
            <button
              className={`analyzer-tab ${activeTab === 'summary' ? 'is-active' : ''}`}
              type="button"
              onClick={() => setActiveTab('summary')}
            >
              Summary
            </button>
            <button
              className={`analyzer-tab ${activeTab === 'issues' ? 'is-active' : ''}`}
              type="button"
              onClick={() => setActiveTab('issues')}
            >
              Issues
            </button>
            <button
              className={`analyzer-tab ${activeTab === 'letter' ? 'is-active' : ''}`}
              type="button"
              onClick={() => setActiveTab('letter')}
            >
              Dispute Letter
            </button>
            <button
              className={`analyzer-tab ${activeTab === 'phone' ? 'is-active' : ''}`}
              type="button"
              onClick={() => setActiveTab('phone')}
            >
              Phone Script
            </button>
          </div>

          {activeTab === 'summary' ? (
            <div className="analyzer-panel">
              <div className="analyzer-summary-grid">
                <div className="analyzer-metric">
                  <span>Charged</span>
                  <strong>{formatCurrency(result.summary.total_charged)}</strong>
                </div>
                <div className="analyzer-metric">
                  <span>Fair Estimate</span>
                  <strong>{formatCurrency(result.summary.total_fair_estimate)}</strong>
                </div>
                <div className="analyzer-metric analyzer-metric-highlight">
                  <span>Potential Savings</span>
                  <strong>
                    {formatCurrency(result.summary.potential_savings)} ({result.summary.savings_percentage}%)
                  </strong>
                </div>
              </div>

              <div className="analyzer-table-wrap">
                <table className="analyzer-table">
                  <thead>
                    <tr>
                      <th>Item</th>
                      <th>CPT</th>
                      <th>Charged</th>
                      <th>Fair Price</th>
                      <th>Markup</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.line_items.map((item, index) => (
                      <tr
                        key={`${item.description}-${index}`}
                        className={item.markup_ratio && item.markup_ratio > 3 ? 'is-overpriced' : ''}
                      >
                        <td>{item.description}</td>
                        <td>{item.cpt_code || '-'}</td>
                        <td>{formatCurrency(item.charged_amount)}</td>
                        <td>{formatCurrency(item.fair_price)}</td>
                        <td>{item.markup_ratio ? `${item.markup_ratio}x` : '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}

          {activeTab === 'issues' ? (
            <div className="analyzer-panel analyzer-issues">
              {result.issues.length === 0 ? (
                <div className="analyzer-empty">No billing issues were detected in this run.</div>
              ) : (
                result.issues.map((issue, index) => (
                  <article
                    key={`${issue.type}-${issue.item}-${index}`}
                    className={`analyzer-issue severity-${issue.severity.toLowerCase()}`}
                  >
                    <div className="analyzer-issue-title">
                      <strong>{issue.type}</strong>
                      <span>{issue.severity}</span>
                    </div>
                    <div className="analyzer-issue-item">{issue.item}</div>
                    <div className="analyzer-issue-meta">
                      Charged {formatCurrency(issue.charged)} vs fair {formatCurrency(issue.fair_price)}
                    </div>
                    <p>{issue.explanation}</p>
                  </article>
                ))
              )}
            </div>
          ) : null}

          {activeTab === 'letter' ? (
            <div className="analyzer-panel">
              <div className="analyzer-doc-actions">
                <button className="analyzer-secondary" type="button" onClick={downloadLetter}>
                  Download Letter
                </button>
              </div>
              <pre className="analyzer-doc">{result.dispute_letter}</pre>
            </div>
          ) : null}

          {activeTab === 'phone' ? (
            <div className="analyzer-panel">
              <pre className="analyzer-doc">{result.phone_script}</pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
