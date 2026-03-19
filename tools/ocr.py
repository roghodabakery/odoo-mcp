"""OCR and invoice structuring MCP tools."""
import json
import logging
import os
from typing import Optional

import cv2
import numpy as np
import pytesseract
import requests
from fastmcp import Context

from utils.normalizer import (
    decode_base64_file,
    InvalidInputError,
    normalize_ocr_text,
    OCRError,
    preprocess_for_ocr,
    TesseractError,
)

logger = logging.getLogger(__name__)

# MCP instance will be imported from main server module
# This allows tools to be registered properly
mcp = None


STRUCTURE_PROMPT = """You are an invoice parser for Indonesian businesses.
Extract ALL data from the raw OCR text below and return ONLY a valid JSON object.
No explanation, no markdown, no code fences. Pure JSON only.

Required structure:
{
  "supplier": { "name": "", "tax_id": "", "address": "", "phone": "", "email": "" },
  "invoice": { "number": "", "date": "YYYY-MM-DD", "due_date": "YYYY-MM-DD" },
  "lines": [
    { "description": "", "quantity": 0, "unit": "", "unit_price": 0, "tax_percent": 0, "subtotal": 0 }
  ],
  "totals": { "subtotal": 0, "tax": 0, "total": 0 },
  "currency": "IDR"
}

Rules:
- All monetary values as plain numbers (no Rp, no commas)
- Dates as YYYY-MM-DD
- If field not found, use null
- "DPP" = subtotal before tax, "PPN" = VAT (usually 11%)

RAW OCR TEXT:
"""


class OpenRouterError(OCRError):
    """Raised when OpenRouter API call fails."""
    pass


def structure_invoice_call(raw_text: str, timeout: int = 30) -> dict:
    """
    Send normalized OCR text to OpenRouter DeepSeek for JSON extraction.

    Args:
        raw_text: Normalized OCR text from invoice
        timeout: API timeout in seconds

    Returns:
        Structured invoice data as dictionary

    Raises:
        InvalidInputError: If raw_text is empty
        OpenRouterError: If API call fails or returns invalid JSON
    """
    if not raw_text or not isinstance(raw_text, str):
        raise InvalidInputError("raw_text must be a non-empty string")

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise OpenRouterError("OPENROUTER_API_KEY environment variable not set")

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": os.environ.get("STRUCTURING_MODEL", "openrouter/free"),
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a JSON extraction assistant. You MUST respond with ONLY a valid JSON object. No explanation, no markdown, no code fences. Start your response with { and end with }."
                    },
                    {
                        "role": "user",
                        "content": STRUCTURE_PROMPT + raw_text
                    }
                ],
                "max_tokens": 1500,
            },
            timeout=timeout
        )

        response.raise_for_status()

        result = response.json()

        if "choices" not in result or not result["choices"]:
            raise OpenRouterError("OpenRouter returned empty choices")

        message_content = result["choices"][0].get("message", {}).get("content")

        if not message_content:
            raise OpenRouterError("OpenRouter returned empty message content")

        # Parse the JSON response
        structured_data = json.loads(message_content)

        logger.info("Successfully structured invoice data")
        return structured_data

    except requests.exceptions.Timeout:
        raise OpenRouterError(f"OpenRouter API timeout after {timeout}s")
    except requests.exceptions.RequestException as e:
        raise OpenRouterError(f"OpenRouter API request failed: {e}")
    except json.JSONDecodeError as e:
        raise OpenRouterError(f"Failed to parse OpenRouter response as JSON: {e}")
    except (KeyError, IndexError) as e:
        raise OpenRouterError(f"Unexpected OpenRouter response structure: {e}")


def register_tools(mcp_instance):
    """
    Register OCR tools with the MCP server instance.

    Args:
        mcp_instance: FastMCP server instance
    """
    global mcp
    mcp = mcp_instance

    @mcp.tool(annotations={"readOnlyHint": True}, timeout=60.0)
    async def ocr_invoice(
        file_base64: str,
        file_type: str = "pdf",
        ctx: Context = None
    ) -> dict:
        """
        Run Tesseract OCR on an invoice file (PDF or image).

        Args:
            file_base64: Base64-encoded file content (PDF, PNG, JPG supported)
            file_type: Type of file - "pdf", "png", "jpg", "jpeg" (default: "pdf")
            ctx: MCP context for logging and progress reporting

        Returns:
            Dictionary with:
            - raw_text: Extracted and normalized text
            - pages: Number of pages processed
            - ocr_confidence: "high", "medium", or "low"
            - avg_confidence_pct: Average confidence percentage

        Raises:
            InvalidInputError: If base64 or file format is invalid
            TesseractError: If OCR processing fails
        """
        try:
            # Decode base64 file
            if ctx:
                ctx.info(f"Decoding {file_type.upper()} file...")

            raw_bytes = decode_base64_file(file_base64)

            # Convert to images based on file type
            images = []
            file_type_lower = file_type.lower().strip()

            if file_type_lower == "pdf":
                if ctx:
                    ctx.info("Converting PDF to images at 300 DPI...")

                from pdf2image import convert_from_bytes
                pil_images = convert_from_bytes(raw_bytes, dpi=300)
                images = [np.array(img) for img in pil_images]
            else:
                # Handle image files
                nparr = np.frombuffer(raw_bytes, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                if img is None:
                    raise InvalidInputError(
                        f"Failed to decode image. Unsupported format: {file_type}"
                    )
                images = [img]

            if not images:
                raise InvalidInputError("No images extracted from file")

            if ctx:
                ctx.info(f"Processing {len(images)} page(s)...")

            # Process each page
            pages = []
            confidences = []
            tesseract_config = "--psm 4 --oem 1"

            for i, img in enumerate(images):
                if ctx:
                    ctx.report_progress(i + 1, len(images))

                # Preprocess
                processed = preprocess_for_ocr(img)

                # Get confidence data
                try:
                    data = pytesseract.image_to_data(
                        processed,
                        lang="ind+eng",
                        config=tesseract_config,
                        output_type=pytesseract.Output.DICT
                    )

                    # Calculate average confidence (ignore -1 values)
                    valid_confs = [
                        int(c) for c in data["conf"]
                        if str(c).isdigit() and int(c) >= 0
                    ]
                    avg_conf = (
                        sum(valid_confs) / len(valid_confs)
                        if valid_confs else 0
                    )

                except Exception as e:
                    logger.warning(f"Failed to get confidence data: {e}")
                    avg_conf = 0

                # Extract text
                try:
                    text = pytesseract.image_to_string(
                        processed,
                        lang="ind+eng",
                        config=tesseract_config
                    )
                except pytesseract.TesseractError as e:
                    raise TesseractError(f"Tesseract failed on page {i + 1}: {e}")

                pages.append(text.strip())
                confidences.append(avg_conf)

            # Calculate overall confidence
            overall_conf = (
                sum(confidences) / len(confidences)
                if confidences else 0
            )

            # Determine confidence level
            if overall_conf >= 80:
                ocr_confidence = "high"
            elif overall_conf >= 50:
                ocr_confidence = "medium"
            else:
                ocr_confidence = "low"

            # Normalize text
            raw_text = normalize_ocr_text("\n--- PAGE BREAK ---\n".join(pages))

            # Warn if low confidence
            if ctx and ocr_confidence == "low":
                ctx.warning(
                    "OCR confidence is low — user should verify invoice fields manually."
                )

            result = {
                "raw_text": raw_text,
                "pages": len(images),
                "ocr_confidence": ocr_confidence,
                "avg_confidence_pct": round(overall_conf, 1)
            }

            if ctx:
                ctx.info(f"OCR completed with {ocr_confidence} confidence")

            return result

        except (InvalidInputError, TesseractError, OCRError):
            raise
        except Exception as e:
            logger.error(f"Unexpected OCR error: {e}", exc_info=True)
            raise OCRError(f"OCR processing failed: {e}")

    @mcp.tool(annotations={"readOnlyHint": True}, timeout=30.0)
    async def structure_invoice(raw_text: str, ctx: Context = None) -> dict:
        """
        Send normalized OCR text to LLM and return structured invoice JSON.

        Uses DeepSeek V3 via OpenRouter to extract structured data from
        raw invoice text. Returns supplier info, invoice details, line items,
        and totals.

        Args:
            raw_text: Normalized OCR text from ocr_invoice tool
            ctx: MCP context for logging

        Returns:
            Structured invoice data with keys:
            - supplier: {name, tax_id, address, phone, email}
            - invoice: {number, date, due_date}
            - lines: [{description, quantity, unit, unit_price, tax_percent, subtotal}]
            - totals: {subtotal, tax, total}
            - currency: "IDR"

        Raises:
            InvalidInputError: If raw_text is empty
            OpenRouterError: If API call fails
        """
        try:
            if ctx:
                ctx.info("Structuring invoice data with LLM...")

            result = structure_invoice_call(raw_text)

            if ctx:
                ctx.info("Invoice structured successfully")

            return result

        except (InvalidInputError, OpenRouterError):
            raise
        except Exception as e:
            logger.error(f"Unexpected structuring error: {e}", exc_info=True)
            raise OpenRouterError(f"Invoice structuring failed: {e}")


# For standalone testing
if __name__ == "__main__":
    import sys

    # Test structure_invoice_call with sample text
    sample_text = """
    PT EXAMPLE SUPPLIER
    Jl. Sudirman No. 123
    NPWP: 01.234.567.8-012.345

    INVOICE: INV-2024-001
    Date: 2024-01-15

    Item A     10 pcs   Rp 50000   Rp 500000
    Item B     5 pcs    Rp 30000   Rp 150000

    Subtotal: Rp 650000
    PPN 11%: Rp 71500
    Total: Rp 721500
    """

    try:
        result = structure_invoice_call(sample_text)
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
