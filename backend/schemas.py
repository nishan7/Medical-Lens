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
