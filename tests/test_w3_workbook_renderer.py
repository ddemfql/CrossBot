from io import BytesIO
import unittest
from zipfile import ZipFile

from openpyxl import load_workbook

from core.models import DocumentType, TradeData, TradeLine
from documents.workbook_renderer import TEMPLATE_VERSION, XLSXRenderer, template_sha256


def sample_trade():
    return TradeData(order_no="SYN-EXPORT-1", seller="Synthetic Seller", buyer="Synthetic Buyer",
        destination_country="DE", currency="USD", incoterm="FCA Shanghai",
        payment_terms="50 percent before shipment", transport_mode="sea", revision=3,
        line_items=[TradeLine("SYN-1", "Synthetic part", "2.5", "10.10", "pcs", 1, "4.2", "3.7"),
                    TradeLine("SYN-2", "Synthetic accessory", "1", "2", "set", 1, "1.1", "0.8")])


class WorkbookRendererTests(unittest.TestCase):
    def test_invoice_export_uses_a_styled_blank_template_and_trade_data(self):
        output = XLSXRenderer().render(DocumentType.COMMERCIAL_INVOICE, sample_trade())
        workbook = load_workbook(BytesIO(output.content), data_only=True)
        sheet = workbook.active
        self.assertEqual(sheet["A1"].value, "COMMERCIAL INVOICE / 商业发票")
        self.assertIn("DRAFT", sheet["A2"].value)
        self.assertEqual(sheet["B14"].value, "Synthetic part")
        self.assertEqual(sheet["J14"].value, "25.250")
        self.assertEqual(sheet["J16"].value, "27.250")
        self.assertEqual(sheet["A4"].value, "订单号")
        self.assertTrue(sheet["A1"].fill.fgColor.rgb.endswith("294B45"))
        self.assertEqual(output.extension, "xlsx")
        self.assertEqual(output.template_version, TEMPLATE_VERSION)
        self.assertEqual(output.template_sha256, template_sha256(DocumentType.COMMERCIAL_INVOICE))

    def test_packing_list_exports_package_and_weight_totals(self):
        output = XLSXRenderer().render(DocumentType.PACKING_LIST, sample_trade())
        sheet = load_workbook(BytesIO(output.content), data_only=True).active
        self.assertEqual(sheet["H16"].value, 2)
        self.assertEqual(sheet["I16"].value, "5.3")
        self.assertEqual(sheet["J16"].value, "4.5")
        self.assertEqual(output.media_type,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def test_same_inputs_produce_byte_identical_workbooks(self):
        renderer = XLSXRenderer()
        first = renderer.render(DocumentType.PROFORMA_INVOICE, sample_trade()).content
        second = renderer.render(DocumentType.PROFORMA_INVOICE, sample_trade()).content
        self.assertEqual(first, second)
        with ZipFile(BytesIO(first)) as package:
            self.assertTrue(all(item.date_time == (2000, 1, 1, 0, 0, 0)
                                for item in package.infolist()))


if __name__ == "__main__":
    unittest.main()

