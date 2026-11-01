"""Real, reproducible Chinese PDF worksheets using the same checked XLSX data."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from core.errors import CrossBotError, ErrorCode


def _font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    name = "CrossBotCJK"
    if name in pdfmetrics.getRegisteredFontNames():
        return name
    paths = [Path("C:/Windows/Fonts/simsun.ttc"), Path("/mnt/c/Windows/Fonts/simsun.ttc"),
             Path("/usr/share/fonts/truetype/arphic/uming.ttc")]
    for path in paths:
        if path.is_file():
            try:
                pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=0))
                return name
            except Exception:
                continue
    # Standard PDF CJK font: readers resolve its glyphs when an embeddable
    # local CJK font is unavailable. No proprietary font is copied into git.
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light"


def render_pdf(xlsx_content, title, *, audience="internal"):
    """Render the standard worksheet and audit sheets, never simulate a PDF."""
    from openpyxl import load_workbook
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle, PageBreak
    wb = load_workbook(BytesIO(xlsx_content), data_only=False)
    stream = BytesIO()
    font = _font()
    style = ParagraphStyle("body", fontName=font, fontSize=8, leading=11,
                           wordWrap="CJK", splitLongWords=True)
    heading = ParagraphStyle("title", parent=style, fontSize=14, leading=19, spaceAfter=10)

    def p(value):
        return Paragraph(escape(str(value if value is not None else "")).replace("\n", "<br/>"), style)

    page_width, page_height = landscape(A4)
    width = page_width - 64
    story = []
    for index, sheet in enumerate(wb):
        if index and not (audience == "customer" and sheet.title == "明细合计"):
            story.append(PageBreak())
        story.append(Paragraph(escape(title + " / " + sheet.title), heading))
        rows = [list(row) for row in sheet.iter_rows(values_only=True)]
        # All standard sheets are audited; omit trailing empty columns in each
        # sheet while preserving meaningful empty cells inside a product row.
        count = max((max((i + 1 for i, v in enumerate(row) if v is not None), default=1)
                     for row in rows), default=1)
        if sheet.title == "文件":
            from .catalog import ROW_FIELDS
            # Locate the main sheet's explicit product header after scalar pairs.
            split = next((i for i, row in enumerate(rows) if i > 2 and
                          sum(v is not None for v in row) > 2), len(rows))
            blocks = [rows[:split], rows[split:]]
        else:
            blocks = [rows]
        for block in blocks:
            if not block:
                continue
            columns = max((max((i + 1 for i, v in enumerate(row) if v is not None), default=1)
                           for row in block), default=1)
            # Group the 13-column provenance into readable source descriptions.
            if columns > 8:
                headers = block[0]
                data = [[p(headers[0]), p(headers[1]), p("来源与核对记录")]]
                for row in block[1:]:
                    description = "\n".join(str(headers[col] or col + 1) + "：" + str(value)
                        for col, value in enumerate(row) if col >= 2 and value is not None)
                    data.append([p(row[0]), p(row[1]), p(description)])
                widths = [width * .22, width * .33, width * .45]
                repeats = 1
            else:
                data = [[p(row[col] if col < len(row) else "") for col in range(columns)] for row in block]
                widths = [width / columns] * columns
                repeats = 1
            table = LongTable(data, colWidths=widths, repeatRows=repeats, splitByRow=1,
                              splitInRow=1, hAlign="LEFT")
            table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), font),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), .3, colors.HexColor("#CCD6D1")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E4EDE9")),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5)]))
            story.extend([table, Spacer(1, 10)])

    def page(canvas, document):
        canvas.setFont(font, 8)
        canvas.drawString(32, 18, title)
        canvas.drawRightString(page_width - 32, 18, "第 %d 页" % document.page)

    try:
        document = SimpleDocTemplate(stream, pagesize=landscape(A4), leftMargin=32,
            rightMargin=32, topMargin=30, bottomMargin=32, title=title, author="CrossBot")
        def canvas_factory(*args, **kwargs):
            kwargs["invariant"] = 1
            return Canvas(*args, **kwargs)
        document.build(story, onFirstPage=page, onLaterPages=page, canvasmaker=canvas_factory)
        return stream.getvalue()
    except Exception as exc:
        raise CrossBotError(ErrorCode.CONFIGURATION, "PDF 排版失败，请检查过长内容后重试。") from exc
    finally:
        wb.close()

