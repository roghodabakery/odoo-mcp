"""
Data Contract Schemas for Odoo Buying Agent

This module defines Pydantic models for the three main JSON structures:
- invoice_json: Output of OCR + LLM structuring
- draft_plan: Output of Phase 1 (presented to user for review)
- approved_plan: User sends this to trigger Phase 2
"""

from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator, model_validator
import re


# ============================================================================
# ENUMS
# ============================================================================

class MatchStatus(str, Enum):
    """Product matching status values"""
    EXACT_MATCH = "exact_match"      # Score ≥ 92 — auto-use, no review
    FUZZY_MATCH = "fuzzy_match"      # Score 72–91 — show in draft for review
    UNCERTAIN_MATCH = "uncertain_match"  # Score 50–71 — show with low-confidence warning
    NO_MATCH = "no_match"            # Score < 50 — flag needs_creation: true


class SupplierStatus(str, Enum):
    """Supplier resolution status values"""
    FOUND = "found"                  # Supplier exact match
    FUZZY_MATCH = "fuzzy_match"      # Supplier fuzzy match
    NOT_FOUND = "not_found"          # Supplier not in Odoo


class OCRConfidence(str, Enum):
    """OCR processing confidence levels"""
    HIGH = "high"      # Clear printed invoice
    MEDIUM = "medium"  # Some noise
    LOW = "low"        # Handwritten/blurry — warn user to verify all fields manually


# ============================================================================
# INVOICE JSON MODELS (OCR + LLM Output)
# ============================================================================

class SupplierInfo(BaseModel):
    """Supplier information from invoice"""
    name: str
    tax_id: str  # NPWP in Indonesian context
    address: str
    phone: str
    email: Optional[str] = None


class InvoiceInfo(BaseModel):
    """Invoice header information"""
    number: str  # "No. Faktur" in Indonesian context
    date: str    # YYYY-MM-DD format
    due_date: str  # YYYY-MM-DD format

    @field_validator('date', 'due_date')
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        """Validate date is in YYYY-MM-DD format"""
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', v):
            raise ValueError(f'Date must be in YYYY-MM-DD format, got: {v}')
        # Additional validation: check if it's a valid date
        try:
            from datetime import datetime
            datetime.strptime(v, '%Y-%m-%d')
        except ValueError as e:
            raise ValueError(f'Invalid date: {v} - {e}')
        return v


class InvoiceLine(BaseModel):
    """Individual line item from invoice"""
    description: str
    quantity: int = Field(gt=0, description="Quantity must be positive")
    unit: str
    unit_price: float = Field(ge=0, description="Unit price must be non-negative")
    tax_percent: float = Field(ge=0, le=100, description="Tax percentage (0-100)")
    subtotal: float = Field(ge=0, description="Subtotal must be non-negative")

    @field_validator('unit_price', 'subtotal')
    @classmethod
    def validate_monetary_value(cls, v: Any) -> float:
        """Ensure monetary values are plain numbers without currency symbols"""
        if isinstance(v, str):
            # Check for currency symbols or commas
            if any(c in v for c in ['Rp', '$', '€', '£', '¥', ',']):
                raise ValueError(f'Monetary values must be plain numbers without currency symbols or commas, got: {v}')
            try:
                return float(v)
            except ValueError:
                raise ValueError(f'Invalid monetary value: {v}')
        return float(v)

    @model_validator(mode='after')
    def validate_subtotal(self) -> 'InvoiceLine':
        """Validate that subtotal equals quantity * unit_price"""
        expected_subtotal = self.quantity * self.unit_price
        # Allow small floating point differences
        if abs(self.subtotal - expected_subtotal) > 0.01:
            raise ValueError(
                f'Subtotal ({self.subtotal}) does not match quantity * unit_price ({expected_subtotal})'
            )
        return self


class InvoiceTotals(BaseModel):
    """Invoice total amounts"""
    subtotal: float = Field(ge=0, description="Subtotal before tax (DPP)")
    tax: float = Field(ge=0, description="Tax amount (PPN)")
    total: float = Field(ge=0, description="Total amount")

    @field_validator('subtotal', 'tax', 'total')
    @classmethod
    def validate_monetary_value(cls, v: Any) -> float:
        """Ensure monetary values are plain numbers without currency symbols"""
        if isinstance(v, str):
            if any(c in v for c in ['Rp', '$', '€', '£', '¥', ',']):
                raise ValueError(f'Monetary values must be plain numbers without currency symbols or commas, got: {v}')
            try:
                return float(v)
            except ValueError:
                raise ValueError(f'Invalid monetary value: {v}')
        return float(v)

    @model_validator(mode='after')
    def validate_totals(self) -> 'InvoiceTotals':
        """Validate that total equals subtotal + tax"""
        expected_total = self.subtotal + self.tax
        # Allow small floating point differences
        if abs(self.total - expected_total) > 0.01:
            raise ValueError(
                f'Total ({self.total}) does not match subtotal + tax ({expected_total})'
            )
        return self


class InvoiceJSON(BaseModel):
    """Complete invoice JSON structure from OCR + LLM processing"""
    supplier: SupplierInfo
    invoice: InvoiceInfo
    lines: list[InvoiceLine] = Field(min_length=1, description="Invoice must have at least one line item")
    totals: InvoiceTotals
    currency: str = Field(default="IDR", description="Currency code, defaults to IDR")

    @model_validator(mode='after')
    def validate_invoice_totals(self) -> 'InvoiceJSON':
        """Validate that invoice totals match sum of line items"""
        lines_subtotal = sum(line.subtotal for line in self.lines)
        lines_tax = sum(line.subtotal * line.tax_percent / 100 for line in self.lines)

        # Allow for rounding differences (up to 1 unit of currency)
        if abs(self.totals.subtotal - lines_subtotal) > 1:
            raise ValueError(
                f'Invoice subtotal ({self.totals.subtotal}) does not match sum of line subtotals ({lines_subtotal})'
            )

        if abs(self.totals.tax - lines_tax) > 1:
            raise ValueError(
                f'Invoice tax ({self.totals.tax}) does not match calculated tax from lines ({lines_tax})'
            )

        return self


# ============================================================================
# DRAFT PLAN MODELS (Phase 1 Output)
# ============================================================================

class SupplierResolution(BaseModel):
    """Supplier matching resolution"""
    invoice_name: str
    status: SupplierStatus
    odoo_id: Optional[int] = None
    odoo_name: Optional[str] = None
    action_needed: bool = False

    @model_validator(mode='after')
    def validate_supplier_resolution(self) -> 'SupplierResolution':
        """Validate supplier resolution consistency"""
        # If status is FOUND or FUZZY_MATCH, odoo_id should be present
        if self.status in [SupplierStatus.FOUND, SupplierStatus.FUZZY_MATCH]:
            if self.odoo_id is None:
                raise ValueError(f'odoo_id is required when supplier status is {self.status}')
            if self.odoo_name is None:
                raise ValueError(f'odoo_name is required when supplier status is {self.status}')

        # If status is NOT_FOUND, action_needed should be True
        if self.status == SupplierStatus.NOT_FOUND and not self.action_needed:
            raise ValueError('action_needed must be True when supplier status is not_found')

        return self


class LineResolution(BaseModel):
    """Individual line item resolution"""
    invoice_description: str
    quantity: int = Field(gt=0, description="Quantity must be positive")
    unit_price: float = Field(ge=0, description="Unit price must be non-negative")
    match_status: MatchStatus
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    suggested_name: Optional[str] = None
    score: Optional[float] = Field(None, ge=0, le=100, description="Match score (0-100)")
    action_needed: bool = False
    prompt: Optional[str] = None
    user_approved: Optional[bool] = None

    @field_validator('unit_price')
    @classmethod
    def validate_monetary_value(cls, v: Any) -> float:
        """Ensure monetary values are plain numbers without currency symbols"""
        if isinstance(v, str):
            if any(c in v for c in ['Rp', '$', '€', '£', '¥', ',']):
                raise ValueError(f'Monetary values must be plain numbers without currency symbols or commas, got: {v}')
            try:
                return float(v)
            except ValueError:
                raise ValueError(f'Invalid monetary value: {v}')
        return float(v)

    @model_validator(mode='after')
    def validate_line_resolution(self) -> 'LineResolution':
        """Validate line resolution consistency"""
        # EXACT_MATCH should have product_id and product_name
        if self.match_status == MatchStatus.EXACT_MATCH:
            if self.product_id is None or self.product_name is None:
                raise ValueError('product_id and product_name are required for exact_match')

        # NO_MATCH should have suggested_name and action_needed=True
        if self.match_status == MatchStatus.NO_MATCH:
            if self.suggested_name is None:
                raise ValueError('suggested_name is required for no_match')
            if not self.action_needed:
                raise ValueError('action_needed must be True for no_match')

        # FUZZY_MATCH and UNCERTAIN_MATCH should have prompt if action_needed
        if self.match_status in [MatchStatus.FUZZY_MATCH, MatchStatus.UNCERTAIN_MATCH]:
            if self.action_needed and self.prompt is None:
                raise ValueError(f'prompt is required when action_needed=True for {self.match_status}')

        return self


class DraftPlanSummary(BaseModel):
    """Summary statistics for draft plan"""
    total_lines: int = Field(ge=0, description="Total number of line items")
    auto_resolved: int = Field(ge=0, description="Number of auto-resolved lines")
    needs_review: int = Field(ge=0, description="Number of lines needing review")
    needs_creation: int = Field(ge=0, description="Number of new products to create")
    estimated_total: float = Field(ge=0, description="Estimated total amount")

    @field_validator('estimated_total')
    @classmethod
    def validate_monetary_value(cls, v: Any) -> float:
        """Ensure monetary values are plain numbers without currency symbols"""
        if isinstance(v, str):
            if any(c in v for c in ['Rp', '$', '€', '£', '¥', ',']):
                raise ValueError(f'Monetary values must be plain numbers without currency symbols or commas, got: {v}')
            try:
                return float(v)
            except ValueError:
                raise ValueError(f'Invalid monetary value: {v}')
        return float(v)

    @model_validator(mode='after')
    def validate_summary(self) -> 'DraftPlanSummary':
        """Validate summary consistency"""
        if self.auto_resolved + self.needs_review + self.needs_creation != self.total_lines:
            raise ValueError(
                f'Sum of auto_resolved ({self.auto_resolved}), needs_review ({self.needs_review}), '
                f'and needs_creation ({self.needs_creation}) must equal total_lines ({self.total_lines})'
            )
        return self


class DraftPlan(BaseModel):
    """Draft plan structure for user review"""
    plan_id: str
    invoice_ref: str
    invoice_date: str
    awaiting_approval: bool = True
    ocr_confidence: OCRConfidence
    supplier_resolution: SupplierResolution
    line_resolutions: list[LineResolution] = Field(min_length=1, description="Plan must have at least one line resolution")
    summary: DraftPlanSummary

    @field_validator('invoice_date')
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        """Validate date is in YYYY-MM-DD format"""
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', v):
            raise ValueError(f'Date must be in YYYY-MM-DD format, got: {v}')
        try:
            from datetime import datetime
            datetime.strptime(v, '%Y-%m-%d')
        except ValueError as e:
            raise ValueError(f'Invalid date: {v} - {e}')
        return v

    @model_validator(mode='after')
    def validate_plan_consistency(self) -> 'DraftPlan':
        """Validate that plan summary matches line resolutions"""
        total_lines = len(self.line_resolutions)
        auto_resolved = sum(1 for line in self.line_resolutions if not line.action_needed)
        needs_review = sum(
            1 for line in self.line_resolutions
            if line.action_needed and line.match_status != MatchStatus.NO_MATCH
        )
        needs_creation = sum(
            1 for line in self.line_resolutions
            if line.match_status == MatchStatus.NO_MATCH
        )
        estimated_total = sum(line.quantity * line.unit_price for line in self.line_resolutions)

        # Validate summary matches calculated values
        if self.summary.total_lines != total_lines:
            raise ValueError(f'Summary total_lines ({self.summary.total_lines}) does not match actual line count ({total_lines})')

        if self.summary.auto_resolved != auto_resolved:
            raise ValueError(f'Summary auto_resolved ({self.summary.auto_resolved}) does not match calculated value ({auto_resolved})')

        if self.summary.needs_review != needs_review:
            raise ValueError(f'Summary needs_review ({self.summary.needs_review}) does not match calculated value ({needs_review})')

        if self.summary.needs_creation != needs_creation:
            raise ValueError(f'Summary needs_creation ({self.summary.needs_creation}) does not match calculated value ({needs_creation})')

        # Allow small floating point differences for estimated_total
        if abs(self.summary.estimated_total - estimated_total) > 0.01:
            raise ValueError(f'Summary estimated_total ({self.summary.estimated_total}) does not match calculated value ({estimated_total})')

        return self


# ============================================================================
# APPROVED PLAN MODELS (Phase 2 Trigger)
# ============================================================================

class ApprovedLineResolution(LineResolution):
    """Line resolution with user approval - user_approved is required"""
    user_approved: bool = Field(description="User approval is required for approved plans")

    @model_validator(mode='after')
    def validate_approved_line(self) -> 'ApprovedLineResolution':
        """Validate that action_needed lines have been explicitly approved/disapproved"""
        if self.action_needed and self.user_approved is None:
            raise ValueError('Lines with action_needed=True must have user_approved explicitly set')
        return self


class ApprovedPlan(BaseModel):
    """Approved plan structure to trigger Phase 2 - user has reviewed and approved"""
    plan_id: str
    invoice_ref: str
    invoice_date: str
    awaiting_approval: bool = Field(default=False, description="Must be False for approved plans")
    ocr_confidence: OCRConfidence
    supplier_resolution: SupplierResolution
    line_resolutions: list[ApprovedLineResolution] = Field(min_length=1, description="Plan must have at least one line resolution")
    summary: DraftPlanSummary

    @field_validator('invoice_date')
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        """Validate date is in YYYY-MM-DD format"""
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', v):
            raise ValueError(f'Date must be in YYYY-MM-DD format, got: {v}')
        try:
            from datetime import datetime
            datetime.strptime(v, '%Y-%m-%d')
        except ValueError as e:
            raise ValueError(f'Invalid date: {v} - {e}')
        return v

    @model_validator(mode='after')
    def validate_approved_plan(self) -> 'ApprovedPlan':
        """Validate that plan is ready for Phase 2 execution"""
        # Ensure awaiting_approval is False
        if self.awaiting_approval:
            raise ValueError('Approved plan must have awaiting_approval=False')

        # Ensure all action_needed items have been reviewed
        action_needed_lines = [line for line in self.line_resolutions if line.action_needed]
        for line in action_needed_lines:
            if line.user_approved is None:
                raise ValueError(
                    f'Line "{line.invoice_description}" has action_needed=True but user_approved is not set'
                )

        return self


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def create_draft_plan(
    plan_id: str,
    invoice_ref: str,
    invoice_date: str,
    ocr_confidence: OCRConfidence,
    supplier_resolution: SupplierResolution,
    line_resolutions: list[LineResolution],
    summary: Optional[DraftPlanSummary] = None
) -> DraftPlan:
    """
    Builder function to create a draft plan.

    If summary is not provided, it will be automatically calculated from line_resolutions.

    Args:
        plan_id: Unique plan identifier
        invoice_ref: Invoice reference number
        invoice_date: Invoice date (YYYY-MM-DD)
        ocr_confidence: OCR confidence level
        supplier_resolution: Supplier matching resolution
        line_resolutions: List of line item resolutions
        summary: Optional summary (auto-calculated if not provided)

    Returns:
        DraftPlan instance

    Example:
        >>> supplier_res = SupplierResolution(
        ...     invoice_name="PT. Example",
        ...     status=SupplierStatus.FOUND,
        ...     odoo_id=42,
        ...     odoo_name="PT Example",
        ...     action_needed=False
        ... )
        >>> line_res = LineResolution(
        ...     invoice_description="Product A",
        ...     quantity=10,
        ...     unit_price=100.0,
        ...     match_status=MatchStatus.EXACT_MATCH,
        ...     product_id=7,
        ...     product_name="Product A",
        ...     score=96.0,
        ...     action_needed=False
        ... )
        >>> plan = create_draft_plan(
        ...     plan_id="plan_20250318_001",
        ...     invoice_ref="INV/2025/001234",
        ...     invoice_date="2025-03-15",
        ...     ocr_confidence=OCRConfidence.HIGH,
        ...     supplier_resolution=supplier_res,
        ...     line_resolutions=[line_res]
        ... )
    """
    # Calculate summary if not provided
    if summary is None:
        total_lines = len(line_resolutions)
        auto_resolved = sum(1 for line in line_resolutions if not line.action_needed)
        needs_review = sum(
            1 for line in line_resolutions
            if line.action_needed and line.match_status != MatchStatus.NO_MATCH
        )
        needs_creation = sum(
            1 for line in line_resolutions
            if line.match_status == MatchStatus.NO_MATCH
        )
        estimated_total = sum(line.quantity * line.unit_price for line in line_resolutions)

        summary = DraftPlanSummary(
            total_lines=total_lines,
            auto_resolved=auto_resolved,
            needs_review=needs_review,
            needs_creation=needs_creation,
            estimated_total=estimated_total
        )

    return DraftPlan(
        plan_id=plan_id,
        invoice_ref=invoice_ref,
        invoice_date=invoice_date,
        awaiting_approval=True,
        ocr_confidence=ocr_confidence,
        supplier_resolution=supplier_resolution,
        line_resolutions=line_resolutions,
        summary=summary
    )


def validate_approved_plan(plan_data: dict) -> ApprovedPlan:
    """
    Validator to ensure approved plan meets all requirements for Phase 2 execution.

    This function validates:
    1. awaiting_approval is False
    2. All line resolutions with action_needed=True have user_approved set
    3. All required fields are present and valid
    4. Data types and formats are correct

    Args:
        plan_data: Dictionary containing approved plan data

    Returns:
        ApprovedPlan instance

    Raises:
        ValueError: If validation fails
        ValidationError: If Pydantic validation fails

    Example:
        >>> plan_data = {
        ...     "plan_id": "plan_20250318_001",
        ...     "invoice_ref": "INV/2025/001234",
        ...     "invoice_date": "2025-03-15",
        ...     "awaiting_approval": False,
        ...     "ocr_confidence": "high",
        ...     "supplier_resolution": {...},
        ...     "line_resolutions": [...],
        ...     "summary": {...}
        ... }
        >>> approved_plan = validate_approved_plan(plan_data)
    """
    # Pre-validation checks
    if plan_data.get('awaiting_approval', True):
        raise ValueError("Approved plan must have awaiting_approval=False")

    # Check all line resolutions have user_approved set
    line_resolutions = plan_data.get('line_resolutions', [])
    for idx, line in enumerate(line_resolutions):
        if line.get('action_needed', False) and line.get('user_approved') is None:
            raise ValueError(
                f"Line resolution {idx} ('{line.get('invoice_description', 'unknown')}') "
                f"has action_needed=True but user_approved is not set"
            )

    # Use Pydantic model for full validation
    return ApprovedPlan(**plan_data)


def convert_draft_to_approved(
    draft_plan: DraftPlan,
    user_approvals: dict[int, bool]
) -> ApprovedPlan:
    """
    Convert a draft plan to an approved plan by applying user approvals.

    Args:
        draft_plan: The draft plan to convert
        user_approvals: Dictionary mapping line index to user approval (True/False)

    Returns:
        ApprovedPlan instance

    Example:
        >>> draft = create_draft_plan(...)
        >>> approvals = {0: True, 1: False, 2: True}
        >>> approved = convert_draft_to_approved(draft, approvals)
    """
    # Convert line resolutions to approved versions
    approved_lines = []
    for idx, line in enumerate(draft_plan.line_resolutions):
        line_dict = line.model_dump()

        # Apply user approval if provided, otherwise keep existing value
        if idx in user_approvals:
            line_dict['user_approved'] = user_approvals[idx]

        approved_lines.append(ApprovedLineResolution(**line_dict))

    # Create approved plan
    return ApprovedPlan(
        plan_id=draft_plan.plan_id,
        invoice_ref=draft_plan.invoice_ref,
        invoice_date=draft_plan.invoice_date,
        awaiting_approval=False,
        ocr_confidence=draft_plan.ocr_confidence,
        supplier_resolution=draft_plan.supplier_resolution,
        line_resolutions=approved_lines,
        summary=draft_plan.summary
    )
