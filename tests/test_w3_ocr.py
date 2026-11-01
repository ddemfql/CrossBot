import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from documents.ocr import OCRBackend, OCRInputError, RecognizedLine


def synthetic_text_image() -> bytes:
    image = Image.new("RGB", (1000, 240), "white")
    draw = ImageDraw.Draw(image)
    candidates = (Path("C:/Windows/Fonts/arial.ttf"),
                  Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
    available = next((path for path in candidates if path.is_file()), None)
    font = ImageFont.truetype(str(available), 48) if available else ImageFont.load_default(size=48)
    draw.text((36, 70), "ORDER NUMBER 12345", font=font, fill="black")
    result = BytesIO()
    image.save(result, format="PNG")
    return result.getvalue()


def pdf_with_page(content: bytes, *, text_layer: bool = False) -> bytes:
    if text_layer:
        resources = b"<< /Font << /F1 5 0 R >> >>"
        stream = b"BT /F1 18 Tf 48 90 Td (Selectable text already exists) Tj ET"
        media = b"[0 0 612 144]"
    else:
        from PIL import Image as PILImage
        image = PILImage.open(BytesIO(content))
        width, height = image.size
        resources = b"<< /XObject << /Im0 5 0 R >> >>"
        stream = f"q {width} 0 0 {height} 0 0 cm /Im0 Do Q".encode()
        media = f"[0 0 {width} {height}]".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox " + media
        + b" /Resources " + resources + b" /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream",
    ]
    if text_layer:
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    else:
        from PIL import Image as PILImage
        image = PILImage.open(BytesIO(content)).convert("RGB")
        jpeg = BytesIO()
        image.save(jpeg, format="JPEG", quality=90)
        raw = jpeg.getvalue()
        objects.append(b"<< /Type /XObject /Subtype /Image /Width " + str(image.width).encode()
            + b" /Height " + str(image.height).encode()
            + b" /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length "
            + str(len(raw)).encode() + b" >>\nstream\n" + raw + b"\nendstream")
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Root 1 0 R /Size {len(offsets)} >>\nstartxref\n{xref}\n%%EOF".encode())
    return bytes(output)


class OCRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = OCRBackend()
        if not cls.backend.available:
            raise unittest.SkipTest("Optional RapidOCR and PDFium dependencies are not installed")

    def test_synthetic_english_image_returns_confidence_and_bounds(self):
        lines = self.backend.recognize_image(synthetic_text_image())
        text = " ".join(line.text.upper() for line in lines)
        self.assertIn("ORDER", text)
        self.assertIn("NUMBER", text)
        self.assertTrue(all(line.confidence is None or 0 <= line.confidence <= 1 for line in lines))
        self.assertTrue(all(line.bbox[2] > line.bbox[0] and line.bbox[3] > line.bbox[1]
                            for line in lines))
        self.assertTrue(all(line.page_number is None for line in lines))

    def test_scanned_pdf_keeps_page_number(self):
        result = self.backend.recognize_pdf(pdf_with_page(synthetic_text_image()))
        self.assertTrue(result.lines)
        self.assertEqual({line.page_number for line in result.lines}, {1})
        self.assertEqual(result.skipped_text_pages, ())

    def test_pdf_with_text_layer_is_not_reocrd_or_duplicated(self):
        result = self.backend.recognize_pdf(pdf_with_page(b"", text_layer=True))
        self.assertEqual(result.lines, ())
        self.assertEqual(result.skipped_text_pages, (1,))
        self.assertIsNone(self.backend._engine)

    def test_limits_reject_oversize_and_invalid_images(self):
        with self.assertRaises(OCRInputError):
            self.backend.recognize_image(b"x" * (20 * 1024 * 1024 + 1))
        with self.assertRaises(OCRInputError):
            self.backend.recognize_image(b"not an image")
        with self.assertRaises(OCRInputError):
            self.backend.recognize_pdf(pdf_with_page(synthetic_text_image()), max_pages=101)

    def test_result_contract_is_immutable(self):
        item = RecognizedLine("sample", 0.9, (1, 2, 3, 4), 1)
        with self.assertRaises(AttributeError):
            item.text = "changed"


if __name__ == "__main__":
    unittest.main()

