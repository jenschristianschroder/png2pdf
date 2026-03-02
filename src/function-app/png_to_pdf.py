"""PNG-to-PDF conversion logic (bytes-in / bytes-out).

This module is the cloud-adapted version of the original png2pdf.py CLI script.
It operates entirely on in-memory byte streams so it works inside an Azure
Function without touching the filesystem.
"""

import io

from PIL import Image
from reportlab.pdfgen import canvas as pdf_canvas


def png_bytes_to_pdf_bytes(png_data: bytes) -> bytes:
    """Convert raw PNG bytes into a PDF whose page matches the image dimensions.

    Args:
        png_data: The raw bytes of a PNG image.

    Returns:
        The raw bytes of the generated PDF document.

    Raises:
        ValueError: If the input is empty or not a valid image.
    """
    if not png_data:
        raise ValueError("Empty PNG data")

    img = Image.open(io.BytesIO(png_data))
    width_px, height_px = img.size
    dpi_x, dpi_y = img.info.get("dpi", (72, 72))

    # Convert pixel dimensions to PDF points (1 pt = 1/72 in)
    page_w = width_px / dpi_x * 72
    page_h = height_px / dpi_y * 72

    pdf_buf = io.BytesIO()
    c = pdf_canvas.Canvas(pdf_buf, pagesize=(page_w, page_h))

    # reportlab can read directly from a file-like object via ImageReader
    from reportlab.lib.utils import ImageReader

    c.drawImage(ImageReader(io.BytesIO(png_data)), 0, 0, width=page_w, height=page_h)
    c.showPage()
    c.save()

    return pdf_buf.getvalue()
