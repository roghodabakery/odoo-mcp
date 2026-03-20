"""Invoice structuring MCP tool.

NOTE: Tesseract OCR has been removed. Invoice images are processed by a
Vision Model upstream. This module only handles structuring the extracted
text into a typed JSON object via an LLM call.
"""
import json
import logging
import os
from typing import Any

import httpx
from fastmcp import Context

logger = logging.getLogger(__name__)


STRUCTURE_PROMPT = """\
You are an invoice parser for Indonesian businesses.
Extract ALL data from the invoice text below and return ONLY a valid JSON object.
No explanation, no markdown, no code fences. Pure JSON only.

Required structure:
{
  "supplier": { "name": "", "tax_id": "", "address": "", "phone": "", "email": "" },
  "invoice":  { "number": "", "date": "YYYY-MM-DD", "due_date": "YYYY-MM-DD" },
  "lines": [
    {
      "description": "",
      "quantity": 0,
      "unit": "",
      "unit_price": 0,
      "tax_percent": 0,
      "subtotal": 0
    }
  ],
  "totals":   { "subtotal": 0, "tax": 0, "total": 0 },
  "currency": "IDR"
}

Rules:
- All monetary values as plain numbers (no Rp, no commas, no dots as thousands separator)
- Dates as YYYY-MM-DD; use null if not found
- "DPP" = subtotal before tax, "PPN" = VAT (usually 11%)
- If any field is not found leave it as null

INVOICE TEXT:
"""


class StructuringError(Exception):
    """Raised when LLM structuring fails."""


async def _call_openrouter(text: str, timeout: int = 30) -> dict[str, Any]:
    """
    Send invoice text to OpenRouter and return structured data.

    Args:
        text:    Raw text extracted from the invoice (by Vision Model or any source).
        timeout: HTTP timeout in seconds.

    Returns:
        Structured invoice dict.

    Raises:
        StructuringError: On missing API key, network error, or bad JSON response.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise StructuringError("OPENROUTER_API_KEY environment variable is not set")

    model = os.environ.get(
        "STRUCTURING_MODEL",
        "deepseek/deepseek-chat-v3-0324:free",
    )

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a JSON extraction assistant. "
                    "Respond with ONLY a valid JSON object. "
                    "No explanation, no markdown, no code fences. "
                    "Start with { and end with }."
                ),
            },
            {
                "role": "user",
                "content": STRUCTURE_PROMPT + text,
            },
        ],
        "max_tokens": 1500,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
    except httpx.TimeoutException:
        raise StructuringError(f"OpenRouter request timed out after {timeout}s")
    except httpx.HTTPStatusError as exc:
        raise StructuringError(
            f"OpenRouter returned HTTP {exc.response.status_code}: {exc.response.text}"
        )
    except httpx.RequestError as exc:
        raise StructuringError(f"OpenRouter request failed: {exc}")

    body = response.json()

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise StructuringError(f"Unexpected OpenRouter response shape: {exc}") from exc

    if not content:
        raise StructuringError("OpenRouter returned an empty message content")

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise StructuringError(
            f"OpenRouter response is not valid JSON: {exc}\nContent: {content!r}"
        ) from exc


def register_tools(mcp):
    """Register invoice structuring tools with the MCP server instance."""

    @mcp.tool(annotations={"readOnlyHint": True}, timeout=45.0)
    async def structure_invoice(
        invoice_text: str,
        ctx: Context = None,
    ) -> dict[str, Any]:
        """
        Convert raw invoice text into a structured JSON object using an LLM.

        The invoice text is typically produced by a Vision Model that has already
        read the invoice image. This tool sends the text to an OpenRouter model
        (default: deepseek-chat-v3-0324:free) and returns typed, normalised data
        ready to be passed to find_or_flag_supplier / find_or_flag_product_tool /
        create_purchase_order.

        Args:
            invoice_text: Raw text extracted from the invoice.
            ctx:          MCP context for progress logging.

        Returns:
            Structured invoice dict:
            {
              "supplier": { "name", "tax_id", "address", "phone", "email" },
              "invoice":  { "number", "date", "due_date" },
              "lines":    [{ "description", "quantity", "unit",
                             "unit_price", "tax_percent", "subtotal" }],
              "totals":   { "subtotal", "tax", "total" },
              "currency": "IDR"
            }

        Raises:
            StructuringError: If the LLM call fails or returns unparseable JSON.
        """
        if not invoice_text or not invoice_text.strip():
            raise ValueError("invoice_text must not be empty")

        if ctx:
            await ctx.info("Structuring invoice text with LLM…")

        try:
            result = await _call_openrouter(invoice_text)
        except StructuringError:
            raise
        except Exception as exc:
            logger.error("Unexpected error in structure_invoice: %s", exc, exc_info=True)
            raise StructuringError(f"Invoice structuring failed: {exc}") from exc

        if ctx:
            lines = len((result.get("lines") or []))
            await ctx.info(f"Invoice structured — {lines} line(s) found")

        return result
