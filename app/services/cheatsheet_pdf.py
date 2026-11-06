from datetime import timedelta
from pathlib import Path

from fpdf import FPDF

from app.services.class_tools import utc_now

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"


class CheatSheetPDF(FPDF):
    def footer(self):
        self.set_y(-11)
        self.set_x(self.l_margin)
        self.set_font("DejaVu", size=8)
        self.set_text_color(120, 120, 128)
        self.cell(0, 8, "Лига Помощников · одна страница к уроку", align="C")


def build_cheatsheet_pdf(subjects: list[dict], class_title: str = "Класс") -> bytes:
    pdf = CheatSheetPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False, margin=14)
    pdf.set_margins(14, 14, 14)
    pdf.add_page()
    pdf.add_font("DejaVu", "", str(FONT_DIR / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
    width = pdf.epw
    date_label = (utc_now() + timedelta(hours=3)).strftime("%d.%m.%Y")

    pdf.set_font("DejaVu", "B", 16)
    pdf.set_text_color(44, 45, 46)
    pdf.cell(width, 8, "Шпаргалка класса", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", size=10)
    pdf.set_text_color(90, 92, 110)
    pdf.cell(width, 5, f"{class_title} · к уроку {date_label} · эталоны учителя", new_x="LMARGIN", new_y="NEXT")
    y = pdf.get_y() + 2
    pdf.set_draw_color(45, 114, 217)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.set_y(y + 4)

    if not subjects:
        pdf.set_font("DejaVu", size=11)
        pdf.set_text_color(44, 45, 46)
        pdf.multi_cell(width, 6, "Пока нет эталонов. Учитель отмечает лучшие разборы в кабинете.", new_x="LMARGIN", new_y="NEXT")
        return bytes(pdf.output())

    bottom = pdf.h - 16
    skipped = 0
    for group in subjects:
        items = group.get("items") or []
        if not items:
            continue
        if pdf.get_y() > bottom - 20:
            skipped += len(items)
            continue
        pdf.set_font("DejaVu", "B", 12)
        pdf.set_text_color(45, 114, 217)
        pdf.cell(width, 7, str(group.get("subject") or "Тема"), new_x="LMARGIN", new_y="NEXT")
        for item in items:
            if pdf.get_y() > bottom - 18:
                skipped += 1
                continue
            pdf.set_font("DejaVu", "B", 9.5)
            pdf.set_text_color(44, 45, 46)
            pdf.multi_cell(width, 4.6, _clip(item.get("text") or "", 220), new_x="LMARGIN", new_y="NEXT")
            note = (item.get("exemplar_note") or "").strip()
            if note:
                pdf.set_font("DejaVu", size=9)
                pdf.multi_cell(width, 4.3, _clip(note, 320), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("DejaVu", size=8)
            pdf.set_text_color(130, 136, 150)
            helper = item.get("helper_name") or "класс"
            author = item.get("author_name") or ""
            pdf.cell(width, 4, f"Разбор: {helper} · спросил {author}", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1.2)
    if skipped and pdf.get_y() < bottom:
        pdf.set_font("DejaVu", size=8)
        pdf.set_text_color(130, 136, 150)
        pdf.cell(width, 5, f"На лист не вошло ещё {skipped}.", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").replace("\n", " ").replace("²", "2").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
