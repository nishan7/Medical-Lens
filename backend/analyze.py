import asyncio
import json
import logging
import mimetypes
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from config import settings
import llm
import ocr
from schemas import AnalyzeBillResponse
from tools import hospital_cheapest_by_name, hospital_search_by_code


router = APIRouter()
logger = logging.getLogger(__name__)

PARSE_SYSTEM = """You are a medical billing expert. Extract structured line items from this bill.
For each billable line item return an object with:
- description
- cpt_code (infer only when strongly implied; otherwise null)
- charged_amount

Skip header rows like date/provider and skip overall totals.
Return ONLY a valid JSON array. No markdown and no explanation."""

ANALYZE_SYSTEM = """You are a medical billing auditor. Analyze these line items for billing issues.

Check for:
1. OVERCHARGE: charged significantly above fair price (markup > 3x)
2. UNBUNDLING: services that may have been split apart unnecessarily
3. DUPLICATE: same service billed more than once
4. UPCODING: complexity appears higher than the description supports

Return ONLY valid JSON with this shape:
{
  "issues": [
    {
      "type": "OVERCHARGE|UNBUNDLING|DUPLICATE|UPCODING",
      "severity": "HIGH|MEDIUM|LOW",
      "item": "description",
      "charged": 0,
      "fair_price": 0,
      "explanation": "plain English explanation a patient can understand"
    }
  ],
  "total_charged": 0,
  "total_fair_estimate": 0,
  "potential_savings": 0,
  "savings_percentage": 0
}"""

DISPUTE_SYSTEM = """You are a patient advocate. Write a professional dispute letter and phone script.

The letter should:
- Address the hospital billing department
- Reference specific overcharges with fair price comparisons
- Cite the CMS Hospital Price Transparency Rule and the No Surprises Act when relevant
- Request a specific adjusted total
- Set a 30-day response deadline
- Mention escalation to the state attorney general if needed

After the letter, write:
---PHONE SCRIPT---
Then provide a short phone script with:
- opening line
- key points to raise
- what to say if they push back
- how to request a supervisor"""

JSON_ARRAY_FIX_SYSTEM = "Repair the user's content into a valid JSON array. Return ONLY the JSON array."
JSON_OBJECT_FIX_SYSTEM = "Repair the user's content into a valid JSON object. Return ONLY the JSON object."

CONTENT_TYPE_SUFFIX_OVERRIDES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

AMOUNT_RE = re.compile(r"\$?\s*([0-9][0-9,]*(?:\.\d{1,2})?)")
CODE_RE = re.compile(r"\b([A-Z]\d{4}|\d{5})\b")


def _allowed_ocr_types() -> set[str]:
    return {item.strip().lower() for item in settings.ocr_allowed_types.split(",") if item.strip()}


def _resolve_upload_suffix(filename: str | None, content_type: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix:
        return suffix

    normalized_type = (content_type or "").lower()
    if normalized_type in CONTENT_TYPE_SUFFIX_OVERRIDES:
        return CONTENT_TYPE_SUFFIX_OVERRIDES[normalized_type]

    guessed = mimetypes.guess_extension(normalized_type)
    if guessed:
        return guessed

    return ".bin"


def _strip_json_wrappers(raw: str) -> str:
    text = str(raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1].strip()
    return text


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number:
            return None
        return number
    text = str(value).strip()
    if not text:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", text)
    if cleaned in {"", "-", ".", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _normalize_cpt_code(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _normalize_line_items(items: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if not isinstance(items, list):
        return normalized

    for raw in items:
        if not isinstance(raw, dict):
            continue
        description = str(raw.get("description") or raw.get("item") or "").strip()
        charged_amount = _to_float(
            raw.get("charged_amount")
            or raw.get("charged")
            or raw.get("amount")
            or raw.get("billed_amount")
        )
        if not description or charged_amount is None or charged_amount <= 0:
            continue

        normalized.append(
            {
                "description": description,
                "cpt_code": _normalize_cpt_code(raw.get("cpt_code") or raw.get("code")),
                "charged_amount": round(charged_amount, 2),
            }
        )

    return normalized


def _fallback_parse_bill_text(bill_text: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for line in str(bill_text or "").splitlines():
        cleaned = " ".join(line.strip().split())
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered.startswith("date:") or lowered.startswith("provider:") or lowered.startswith("total"):
            continue

        matches = list(AMOUNT_RE.finditer(cleaned))
        if not matches:
            continue

        amount_match = matches[-1]
        description = cleaned[: amount_match.start()].strip(" :-")
        charged_amount = _to_float(amount_match.group(1))
        if not description or charged_amount is None or charged_amount <= 0:
            continue

        code_match = CODE_RE.search(description)
        items.append(
            {
                "description": description,
                "cpt_code": code_match.group(1) if code_match else None,
                "charged_amount": round(charged_amount, 2),
            }
        )
    return items


async def _llm_text(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    max_tokens: int,
) -> str:
    return await asyncio.to_thread(
        llm.complete_text,
        system_prompt,
        user_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )


async def _load_json_with_repair(raw: str, *, expect_array: bool) -> Any:
    cleaned = _strip_json_wrappers(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        repair_system = JSON_ARRAY_FIX_SYSTEM if expect_array else JSON_OBJECT_FIX_SYSTEM
        repaired = await _llm_text(
            repair_system,
            cleaned,
            temperature=0.0,
            max_tokens=2048,
        )
        return json.loads(_strip_json_wrappers(repaired))


async def parse_bill(bill_text: str) -> List[Dict[str, Any]]:
    fallback_items = _fallback_parse_bill_text(bill_text)

    try:
        response = await _llm_text(
            PARSE_SYSTEM,
            f"BILL:\n{bill_text}",
            temperature=0.1,
            max_tokens=2048,
        )
        parsed = await _load_json_with_repair(response, expect_array=True)
        normalized = _normalize_line_items(parsed)
        if normalized:
            return normalized
    except Exception as exc:
        logger.warning("LLM bill parsing failed, using fallback parser: %s", exc)

    return fallback_items


def _lowest_price_from_rows(rows: List[Dict[str, Any]]) -> tuple[Optional[float], str]:
    best_price: Optional[float] = None
    best_source = "not_found"

    for row in rows:
        discounted_cash = _to_float(row.get("standard_charge|discounted_cash"))
        if discounted_cash is not None and (best_price is None or discounted_cash < best_price):
            best_price = discounted_cash
            best_source = "discounted_cash"

        for key, value in row.items():
            if isinstance(key, str) and key.endswith("|negotiated_dollar"):
                negotiated = _to_float(value)
                if negotiated is not None and (best_price is None or negotiated < best_price):
                    best_price = negotiated
                    best_source = "negotiated_rate"

    return (round(best_price, 2), best_source) if best_price is not None else (None, "not_found")


async def enrich_with_prices(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for item in items:
        working = dict(item)
        fair_price: Optional[float] = None
        price_source = "not_found"

        cpt_code = str(working.get("cpt_code") or "").strip()
        if cpt_code:
            for code_type in ("CPT", "HCPCS"):
                rows = await asyncio.to_thread(
                    hospital_search_by_code,
                    code_type,
                    cpt_code,
                    20,
                )
                fair_price, source_kind = _lowest_price_from_rows(rows)
                if fair_price is not None:
                    price_source = f"hospital_pricing_data:{code_type.lower()}:{source_kind}"
                    break

        if fair_price is None:
            summary = await asyncio.to_thread(
                hospital_cheapest_by_name,
                str(working.get("description") or ""),
                None,
                None,
                50,
            )
            negotiated = _to_float((summary.get("cheapest_negotiated") or {}).get("price"))
            discounted_cash = _to_float((summary.get("cheapest_self_pay") or {}).get("price"))
            candidates = [price for price in (negotiated, discounted_cash) if price is not None]
            if candidates:
                fair_price = round(min(candidates), 2)
                price_source = "hospital_pricing_data:description"

        charged_amount = _to_float(working.get("charged_amount")) or 0.0
        markup_ratio: Optional[float] = None
        if fair_price is not None and fair_price > 0 and charged_amount > 0:
            markup_ratio = round(charged_amount / fair_price, 1)

        working["charged_amount"] = round(charged_amount, 2)
        working["fair_price"] = fair_price
        working["markup_ratio"] = markup_ratio
        working["price_source"] = price_source
        enriched.append(working)

    return enriched


def _summary_from_items(items: List[Dict[str, Any]]) -> Dict[str, float]:
    total_charged = round(sum((_to_float(item.get("charged_amount")) or 0.0) for item in items), 2)
    total_fair = round(
        sum(
            (
                _to_float(item.get("fair_price"))
                if _to_float(item.get("fair_price")) is not None
                else _to_float(item.get("charged_amount")) or 0.0
            )
            for item in items
        ),
        2,
    )
    potential_savings = round(max(total_charged - total_fair, 0.0), 2)
    savings_percentage = round((potential_savings / total_charged) * 100, 1) if total_charged > 0 else 0.0
    return {
        "total_charged": total_charged,
        "total_fair_estimate": total_fair,
        "potential_savings": potential_savings,
        "savings_percentage": savings_percentage,
    }


def _coalesce_number(value: Any, fallback: float) -> float:
    number = _to_float(value)
    return fallback if number is None else number


def _fallback_analysis(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    seen_descriptions: Dict[str, int] = {}

    for item in items:
        description = str(item.get("description") or "").strip()
        normalized_description = re.sub(r"\s+", " ", description.lower())
        seen_descriptions[normalized_description] = seen_descriptions.get(normalized_description, 0) + 1

        markup_ratio = _to_float(item.get("markup_ratio"))
        if markup_ratio is not None and markup_ratio > 3.0:
            issues.append(
                {
                    "type": "OVERCHARGE",
                    "severity": "HIGH" if markup_ratio >= 5 else "MEDIUM",
                    "item": description,
                    "charged": _to_float(item.get("charged_amount")),
                    "fair_price": _to_float(item.get("fair_price")),
                    "explanation": (
                        f"This charge appears high relative to the lowest comparable hospital price found "
                        f"({markup_ratio}x the reference price)."
                    ),
                }
            )

    for item in items:
        description = str(item.get("description") or "").strip()
        normalized_description = re.sub(r"\s+", " ", description.lower())
        if seen_descriptions.get(normalized_description, 0) > 1:
            issues.append(
                {
                    "type": "DUPLICATE",
                    "severity": "MEDIUM",
                    "item": description,
                    "charged": _to_float(item.get("charged_amount")),
                    "fair_price": _to_float(item.get("fair_price")),
                    "explanation": "This service appears more than once and may need manual review for duplicate billing.",
                }
            )
            seen_descriptions[normalized_description] = 0

    summary = _summary_from_items(items)
    return {
        "issues": issues,
        **summary,
    }


def _normalize_issues(issues: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if not isinstance(issues, list):
        return normalized

    for issue in issues:
        if not isinstance(issue, dict):
            continue
        issue_type = str(issue.get("type") or "").strip().upper() or "OVERCHARGE"
        severity = str(issue.get("severity") or "").strip().upper() or "LOW"
        item = str(issue.get("item") or issue.get("description") or "").strip()
        explanation = str(issue.get("explanation") or "").strip()
        if not item or not explanation:
            continue
        normalized.append(
            {
                "type": issue_type,
                "severity": severity,
                "item": item,
                "charged": _to_float(issue.get("charged")),
                "fair_price": _to_float(issue.get("fair_price")),
                "explanation": explanation,
            }
        )
    return normalized


async def analyze_issues(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    fallback = _fallback_analysis(items)

    try:
        response = await _llm_text(
            ANALYZE_SYSTEM,
            f"Line items with fair prices:\n{json.dumps(items, ensure_ascii=True, indent=2)}",
            temperature=0.1,
            max_tokens=2048,
        )
        parsed = await _load_json_with_repair(response, expect_array=False)
        if not isinstance(parsed, dict):
            raise ValueError("Analysis response was not an object")

        summary = {
            "total_charged": _to_float(parsed.get("total_charged")),
            "total_fair_estimate": _to_float(parsed.get("total_fair_estimate")),
            "potential_savings": _to_float(parsed.get("potential_savings")),
            "savings_percentage": _to_float(parsed.get("savings_percentage")),
        }
        for key, fallback_value in _summary_from_items(items).items():
            value = summary.get(key)
            summary[key] = fallback_value if value is None else round(value, 2 if key != "savings_percentage" else 1)

        return {
            "issues": _normalize_issues(parsed.get("issues")) or fallback["issues"],
            **summary,
        }
    except Exception as exc:
        logger.warning("LLM issue analysis failed, using fallback analysis: %s", exc)
        return fallback


def _fallback_dispute_package(analysis: Dict[str, Any]) -> Dict[str, str]:
    summary = analysis.get("summary") or {}
    issues = analysis.get("issues") or []

    issue_lines = []
    for issue in issues:
        item = str(issue.get("item") or "Unspecified item")
        charged = _to_float(issue.get("charged"))
        fair_price = _to_float(issue.get("fair_price"))
        if charged is not None and fair_price is not None:
            issue_lines.append(f"- {item}: billed ${charged:,.2f} vs fair estimate ${fair_price:,.2f}")
        else:
            issue_lines.append(f"- {item}: billing issue requires review")

    issue_section = "\n".join(issue_lines) if issue_lines else "- Please review the enclosed line-item discrepancies."
    adjusted_total = _to_float(summary.get("total_fair_estimate")) or 0.0
    potential_savings = _to_float(summary.get("potential_savings")) or 0.0

    letter = (
        "Dear Billing Department,\n\n"
        "I am requesting a formal review of the charges on my recent medical bill. "
        "After comparing the billed amounts against publicly available hospital pricing data, "
        "several charges appear inconsistent with reasonable reference prices.\n\n"
        f"{issue_section}\n\n"
        "Under the CMS Hospital Price Transparency Rule, I am requesting an itemized review and correction "
        "of these charges. Where applicable, please also confirm whether the protections of the No Surprises Act "
        "have been applied appropriately.\n\n"
        f"I am requesting an adjusted total of approximately ${adjusted_total:,.2f}, which reflects a potential "
        f"reduction of about ${potential_savings:,.2f}. Please respond within 30 days with either a corrected bill "
        "or a written explanation for each disputed line item.\n\n"
        "If needed, I reserve the right to escalate this matter to state consumer protection authorities and the "
        "state attorney general.\n\n"
        "Sincerely,\n"
        "[Your Name]"
    )

    phone_script = (
        "Opening line:\n"
        "Hi, I am calling to dispute several charges on my bill and request a supervisor review.\n\n"
        "Key points:\n"
        "- I compared the billed amounts against published hospital pricing data.\n"
        "- Several line items appear materially above reasonable reference prices.\n"
        "- I want an itemized explanation and a corrected balance.\n\n"
        "If they push back:\n"
        "Please note that I am requesting a documented review under hospital price transparency requirements.\n\n"
        "Supervisor request:\n"
        "If you cannot adjust this, please transfer me to a billing supervisor or patient financial advocate."
    )

    return {
        "letter": letter,
        "phone_script": phone_script,
    }


async def generate_dispute_package(analysis: Dict[str, Any]) -> Dict[str, str]:
    try:
        response = await _llm_text(
            DISPUTE_SYSTEM,
            f"Analysis:\n{json.dumps(analysis, ensure_ascii=True, indent=2)}",
            temperature=0.3,
            max_tokens=3000,
        )
        parts = response.split("---PHONE SCRIPT---", 1)
        letter = parts[0].strip()
        phone_script = parts[1].strip() if len(parts) > 1 else ""
        if letter:
            return {
                "letter": letter,
                "phone_script": phone_script or _fallback_dispute_package(analysis)["phone_script"],
            }
    except Exception as exc:
        logger.warning("LLM dispute generation failed, using fallback letter: %s", exc)

    return _fallback_dispute_package(analysis)


async def _extract_text_from_upload(file: UploadFile) -> str:
    content_type = (file.content_type or "").lower()
    allowed_types = _allowed_ocr_types()
    if content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {content_type or 'unknown'}")

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    suffix = _resolve_upload_suffix(file.filename, content_type)
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(payload)
            temp_path = Path(handle.name)

        result = await asyncio.to_thread(
            ocr.extract_bill_text,
            temp_path,
            api_key=settings.nvidia_api_key,
            max_pdf_pages=max(1, int(settings.ocr_max_pdf_pages)),
            timeout_seconds=max(1.0, float(settings.ocr_timeout_seconds)),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("OCR extraction failed during /analyze-bill for %s", file.filename)
        raise HTTPException(status_code=400, detail=f"OCR failed. Please paste your bill text instead. ({exc})") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    text = str(result.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="OCR produced no readable text. Please paste your bill text instead.")
    return text


async def _get_bill_text_from_request(request: Request, bill_text: Optional[str]) -> Optional[str]:
    if bill_text and bill_text.strip():
        return bill_text.strip()

    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body.") from exc
        if isinstance(payload, dict):
            value = str(payload.get("bill_text") or "").strip()
            return value or None

    return None


@router.post("/analyze-bill", response_model=AnalyzeBillResponse)
async def analyze_bill(
    request: Request,
    bill_text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
) -> Dict[str, Any]:
    text = await _get_bill_text_from_request(request, bill_text)
    if file is not None:
        text = await _extract_text_from_upload(file)

    if not text:
        raise HTTPException(status_code=400, detail="Provide either bill_text or a file upload.")

    items = await parse_bill(text)
    if not items:
        raise HTTPException(
            status_code=400,
            detail="Could not extract billable line items. Please provide a clearer bill or paste itemized text.",
        )

    line_items = await enrich_with_prices(items)
    summary = _summary_from_items(line_items)
    issue_analysis = await analyze_issues(line_items)
    analysis_payload = {
        "summary": summary,
        "line_items": line_items,
        "issues": issue_analysis.get("issues") or [],
    }
    analysis_payload["summary"].update(
        {
            "total_charged": _coalesce_number(issue_analysis.get("total_charged"), summary["total_charged"]),
            "total_fair_estimate": _coalesce_number(
                issue_analysis.get("total_fair_estimate"),
                summary["total_fair_estimate"],
            ),
            "potential_savings": _coalesce_number(
                issue_analysis.get("potential_savings"),
                summary["potential_savings"],
            ),
            "savings_percentage": _coalesce_number(
                issue_analysis.get("savings_percentage"),
                summary["savings_percentage"],
            ),
        }
    )

    dispute = await generate_dispute_package(analysis_payload)
    return {
        "summary": analysis_payload["summary"],
        "line_items": line_items,
        "issues": analysis_payload["issues"],
        "dispute_letter": dispute["letter"],
        "phone_script": dispute["phone_script"],
    }
