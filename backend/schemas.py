from typing import List, Literal, Optional

from pydantic import BaseModel


Role = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    role: Role
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    image_id: Optional[str] = None


class ChatResponse(BaseModel):
    message: ChatMessage


class AnalyzeBillLineItem(BaseModel):
    description: str
    cpt_code: Optional[str] = None
    charged_amount: float
    fair_price: Optional[float] = None
    markup_ratio: Optional[float] = None
    price_source: str = "not_found"


class AnalyzeBillIssue(BaseModel):
    type: str
    severity: str
    item: str
    explanation: str
    charged: Optional[float] = None
    fair_price: Optional[float] = None


class AnalyzeBillSummary(BaseModel):
    total_charged: float
    total_fair_estimate: float
    potential_savings: float
    savings_percentage: float


class AnalyzeBillResponse(BaseModel):
    summary: AnalyzeBillSummary
    line_items: List[AnalyzeBillLineItem]
    issues: List[AnalyzeBillIssue]
    dispute_letter: str
    phone_script: str
