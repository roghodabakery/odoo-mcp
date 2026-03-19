"""OCR preprocessing and text normalization utilities."""
import base64
import re
import logging
from typing import Optional

import cv2
import numpy as np
import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image

logger = logging.getLogger(__name__)


class OCRError(Exception):
    """Base exception for OCR-related errors."""
    pass


class InvalidInputError(OCRError):
    """Raised when input data is invalid."""
    pass


class TesseractError(OCRError):
    """Raised when Tesseract OCR fails."""
    pass


def preprocess_for_ocr(img_array: np.ndarray) -> np.ndarray:
    """
    Preprocess image array for optimal OCR results.

    Steps:
    1. Convert to grayscale if needed
    2. Upscale to minimum 1800px width (300 DPI equivalent)
    3. Apply adaptive threshold for uneven lighting
    4. Denoise with median blur

    Args:
        img_array: Input image as numpy array (BGR or grayscale)

    Returns:
        Preprocessed image ready for OCR

    Raises:
        InvalidInputError: If img_array is None or invalid
    """
    if img_array is None or not isinstance(img_array, np.ndarray):
        raise InvalidInputError("Invalid image array: must be a numpy array")

    if img_array.size == 0:
        raise InvalidInputError("Invalid image array: empty array")

    try:
        # 1. Convert to grayscale if color image
        if len(img_array.shape) == 3:
            gray = cv2.cvtColor(img_array, cv2.COLOR_BGR2GRAY)
        else:
            gray = img_array.copy()

        # 2. Upscale to minimum 1800px width (300 DPI equivalent)
        h, w = gray.shape
        if w < 1800:
            scale = 1800 / w
            gray = cv2.resize(
                gray, None,
                fx=scale, fy=scale,
                interpolation=cv2.INTER_CUBIC
            )
            logger.debug(f"Upscaled image from {w}px to {int(w * scale)}px")

        # 3. Adaptive threshold — handles uneven lighting/shadows
        thresh = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2
        )

        # 4. Denoise
        denoised = cv2.medianBlur(thresh, 3)

        return denoised

    except cv2.error as e:
        raise OCRError(f"OpenCV preprocessing failed: {e}")


def run_tesseract(file_bytes: bytes, file_type: str = "pdf") -> str:
    """
    Run Tesseract OCR on file bytes (PDF or image).

    Args:
        file_bytes: Raw file bytes (PDF, PNG, JPG, etc.)
        file_type: Type of file - "pdf", "png", "jpg", "jpeg", etc.

    Returns:
        Extracted text with page breaks separated by "--- PAGE BREAK ---"

    Raises:
        InvalidInputError: If file_bytes is empty or invalid
        TesseractError: If Tesseract OCR fails
    """
    if not file_bytes:
        raise InvalidInputError("File bytes cannot be empty")

    if not isinstance(file_bytes, bytes):
        raise InvalidInputError("file_bytes must be bytes type")

    file_type = file_type.lower().strip()

    try:
        images = []

        if file_type == "pdf":
            # Convert PDF to images at 300 DPI
            logger.debug("Converting PDF to images at 300 DPI")
            pil_images = convert_from_bytes(file_bytes, dpi=300)
            images = [np.array(img) for img in pil_images]
        else:
            # Decode image file (png, jpg, jpeg, etc.)
            nparr = np.frombuffer(file_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if img is None:
                raise InvalidInputError(
                    f"Failed to decode image. Unsupported format: {file_type}"
                )
            images = [img]

        if not images:
            raise InvalidInputError("No images extracted from file")

        pages = []
        tesseract_config = "--psm 4 --oem 1"
        # psm 4 = single column (best for most invoices)
        # oem 1 = LSTM only (most accurate)

        for i, img in enumerate(images):
            logger.debug(f"Processing page {i + 1}/{len(images)}")

            # Preprocess image
            processed = preprocess_for_ocr(img)

            # Run Tesseract
            try:
                text = pytesseract.image_to_string(
                    processed,
                    lang="ind+eng",
                    config=tesseract_config
                )
                pages.append(text.strip())
            except pytesseract.TesseractError as e:
                raise TesseractError(f"Tesseract failed on page {i + 1}: {e}")

        logger.info(f"OCR completed: {len(pages)} page(s) processed")
        return "\n--- PAGE BREAK ---\n".join(pages)

    except TesseractError:
        raise
    except InvalidInputError:
        raise
    except Exception as e:
        raise TesseractError(f"Unexpected error during OCR: {e}")


def normalize_ocr_text(raw: str) -> str:
    """
    Normalize OCR text by fixing common recognition errors.

    Fixes:
    - Letter/digit confusion (l/I -> 1, O -> 0) inside numbers
    - Extra spaces inside numbers
    - Currency prefix (Rp)

    Args:
        raw: Raw OCR text

    Returns:
        Normalized text

    Raises:
        InvalidInputError: If raw is not a string
    """
    if not isinstance(raw, str):
        raise InvalidInputError("Input must be a string")

    if not raw:
        return raw

    try:
        text = raw

        # Fix common OCR digit/letter confusion inside number sequences
        # l or I between digits -> 1
        text = re.sub(r'(?<=\d)[lI](?=\d)', '1', text)
        # O between digits -> 0
        text = re.sub(r'(?<=\d)O(?=\d)', '0', text)

        # Collapse extra spaces inside numbers (run twice for chains)
        text = re.sub(r'(\d) (\d)', r'\1\2', text)
        text = re.sub(r'(\d) (\d)', r'\1\2', text)

        # Strip Rp currency prefix (with optional dot and spaces)
        text = re.sub(r'Rp\.?\s*', '', text)

        # Normalize whitespace (multiple spaces/newlines -> single space)
        text = re.sub(r'[ \t]+', ' ', text)

        return text.strip()

    except Exception as e:
        logger.warning(f"Text normalization failed, returning original: {e}")
        return raw


def decode_base64_file(file_base64: str) -> bytes:
    """
    Decode base64-encoded file to bytes.

    Args:
        file_base64: Base64-encoded file content

    Returns:
        Decoded file bytes

    Raises:
        InvalidInputError: If base64 string is invalid
    """
    if not file_base64:
        raise InvalidInputError("Base64 string cannot be empty")

    if not isinstance(file_base64, str):
        raise InvalidInputError("file_base64 must be a string")

    try:
        # Handle data URL prefix (e.g., "data:application/pdf;base64,")
        if "," in file_base64:
            file_base64 = file_base64.split(",", 1)[1]

        # Add padding if needed
        padding = 4 - len(file_base64) % 4
        if padding != 4:
            file_base64 += "=" * padding

        return base64.b64decode(file_base64)

    except Exception as e:
        raise InvalidInputError(f"Invalid base64 encoding: {e}")
