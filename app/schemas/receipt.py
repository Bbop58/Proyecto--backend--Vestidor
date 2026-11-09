"""Shared v1 contract for canonical and provisional commercial notes."""
from datetime import datetime
from typing import Literal, Annotated
from uuid import UUID
from pydantic import BaseModel, Field, EmailStr

ReceiptKind = Literal['sale', 'reservation', 'wallet_topup', 'exchange']
Money = Annotated[str, Field(pattern=r'^-?\d+\.\d{2}$')]


class ReceiptParty(BaseModel):
    id: UUID | None = None
    name: str
    email: str | None = None
    address: str | None = None


class ReceiptItem(BaseModel):
    description: str
    sku: str | None = None
    size: str | None = None
    color: str | None = None
    quantity: int = Field(ge=1)
    unit_price: Money
    subtotal: Money
    section: str | None = None


class ReceiptDocument(BaseModel):
    version: Literal[1] = 1
    kind: ReceiptKind
    entity_id: UUID
    title: str
    number: str
    issued_at: datetime
    company: ReceiptParty
    branch: ReceiptParty | None = None
    customer: ReceiptParty | None = None
    status: str
    payment_status: str
    payment_method: str | None = None
    reference: str | None = None
    currency: Literal['BOB'] = 'BOB'
    items: list[ReceiptItem]
    subtotal: Money | None = None
    discount: Money | None = None
    shipping: Money | None = None
    total: Money
    received: Money | None = None
    change: Money | None = None
    expires_at: datetime | None = None
    provisional: bool = False
    notes: list[str] = Field(default_factory=list)


class ReceiptEmailRequest(BaseModel):
    recipient: EmailStr
    operation_id: UUID


class ReceiptEmailResult(BaseModel):
    operation_id: UUID
    status: Literal['PENDING','ACCEPTED','FAILED','UNKNOWN']
    recipient: str
    message: str
