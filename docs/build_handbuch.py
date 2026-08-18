"""
Erzeugt das Benutzerhandbuch als PDF.

    py docs/build_handbuch.py

Schreibt docs/cintel_Handbuch.pdf. Dieses Skript ist die Quelle des
Handbuchs - Aenderungen bitte hier vornehmen und das PDF neu erzeugen,
nicht das PDF direkt bearbeiten.
"""

from __future__ import annotations

import math
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    ListFlowable,
    ListItem,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)

OUT = Path(__file__).with_name("cintel_Handbuch.pdf")

INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#5b6470")
ACCENT = colors.HexColor("#1f4e79")
RULE = colors.HexColor("#c8d0d8")
CODE_BG = colors.HexColor("#f4f6f8")
CODE_BORDER = colors.HexColor("#d8dee5")
NOTE_BG = colors.HexColor("#eef4fa")
WARN_BG = colors.HexColor("#fdf3e7")
WARN_BORDER = colors.HexColor("#e0a458")
TABLE_HEAD = colors.HexColor("#1f4e79")
TABLE_ALT = colors.HexColor("#f7f9fb")

styles = getSampleStyleSheet()


def _style(name: str, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, parent=styles["Normal"], **kw)


S_TITLE = _style("t", fontName="Helvetica-Bold", fontSize=27, leading=33,
                 textColor=ACCENT, alignment=TA_CENTER, spaceAfter=8)
S_SUB = _style("st", fontName="Helvetica", fontSize=13, leading=19,
               textColor=MUTED, alignment=TA_CENTER)
S_H1 = _style("h1", fontName="Helvetica-Bold", fontSize=17, leading=22,
              textColor=ACCENT, spaceBefore=2, spaceAfter=9)
S_H2 = _style("h2", fontName="Helvetica-Bold", fontSize=12.5, leading=17,
              textColor=INK, spaceBefore=13, spaceAfter=5)
S_H3 = _style("h3", fontName="Helvetica-Bold", fontSize=10.5, leading=14,
              textColor=ACCENT, spaceBefore=9, spaceAfter=3)
S_BODY = _style("b", fontName="Helvetica", fontSize=9.7, leading=14.6,
                textColor=INK, alignment=TA_JUSTIFY, spaceAfter=6)
S_SMALL = _style("sm", fontName="Helvetica", fontSize=8.6, leading=12.4,
                 textColor=MUTED, spaceAfter=4)
S_CELL = _style("c", fontName="Helvetica", fontSize=8.6, leading=11.8)
S_CELLB = _style("cb", fontName="Helvetica-Bold", fontSize=8.6, leading=11.8,
                 textColor=colors.white)
S_TOC = _style("toc", fontName="Helvetica", fontSize=10, leading=16.5)
S_CODE = ParagraphStyle("code", fontName="Courier", fontSize=8.4, leading=11.6,
                        textColor=colors.HexColor("#12305a"))

CODE_WIDTH_PT = 165 * mm - 16
MAX_CODE_PT = 8.4
# Untergrenze der Lesbarkeit. Darunter wird nicht verkleinert - dann
# schlaegt der Bau lieber fehl.
MIN_CODE_PT = 5.4
# Courier ist dicktengleich: jedes Zeichen ist 0,6 x Schriftgroesse breit.
MAX_CODE_CHARS = int(CODE_WIDTH_PT / (MIN_CODE_PT * 0.6))


def h1(text: str) -> list:
    line = Table([[""]], colWidths=[165 * mm], rowHeights=[1.2])
    line.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT)]))
    return [Paragraph(text, S_H1), line, Spacer(1, 9)]


def p(text: str) -> Paragraph:
    return Paragraph(text, S_BODY)


def code(text: str) -> Table:
    """
    Befehlsblock mit Rahmen - so, wie er einzugeben ist.

    Die Schriftgroesse wird automatisch verkleinert, bis auch die laengste
    Zeile in den Rahmen passt. Befehle werden bewusst NICHT umbrochen: sie
    sollen am Stueck kopierbar bleiben.
    """
    body = text.strip("\n")
    longest = max((len(line) for line in body.splitlines()), default=1)

    # Groesse direkt ausrechnen statt sie in einer Schleife herunterzuzaehlen:
    # wiederholtes size -= 0.1 sammelt Rundungsfehler und rutschte dadurch
    # unter die Untergrenze, womit MAX_CODE_CHARS nicht mehr stimmte.
    ideal = CODE_WIDTH_PT / (longest * 0.6)
    size = min(MAX_CODE_PT, math.floor(ideal * 10) / 10)

    # Bauzeit-Sperre: passt die Zeile selbst bei kleinster Schrift nicht,
    # wuerde sie im PDF abgeschnitten - und ein abgeschnittener Befehl ist
    # schlimmer als gar keiner, weil er sich unbemerkt falsch kopieren
    # laesst. Dann lieber der Bauabbruch mit klarer Ansage.
    if size < MIN_CODE_PT:
        raise ValueError(
            f"Befehlszeile zu lang fuer die Seitenbreite: {longest} Zeichen, "
            f"hoechstens {MAX_CODE_CHARS} moeglich.\n"
            f"  {body.splitlines()[0][:90]}...\n"
            f"Bitte den Befehl kuerzen oder auf mehrere Zeilen aufteilen "
            f"(z.B. Pfad vorher einer Variablen zuweisen)."
        )

    style = ParagraphStyle(f"code{longest}", parent=S_CODE, fontSize=size,
                           leading=size * 1.38)
    tbl = Table([[Preformatted(body, style)]], colWidths=[165 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
        ("BOX", (0, 0), (-1, -1), 0.6, CODE_BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return tbl


def callout(title: str, text: str, *, warn: bool = False) -> Table:
    bg = WARN_BG if warn else NOTE_BG
    edge = WARN_BORDER if warn else ACCENT
    tbl = Table([[Paragraph(f"<b>{title}</b><br/>{text}", S_BODY)]],
                colWidths=[165 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LINEBEFORE", (0, 0), (0, -1), 3, edge),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return tbl


def table(rows: list[list[str]], widths: list[float]) -> Table:
    data = [[Paragraph(c, S_CELLB) for c in rows[0]]]
    data += [[Paragraph(c, S_CELL) for c in r] for r in rows[1:]]
    tbl = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEAD),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for i in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), TABLE_ALT))
    tbl.setStyle(TableStyle(style))
    return tbl


def steps(items: list[str]) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(t, S_BODY), leftIndent=16) for t in items],
        bulletType="1", bulletFontName="Helvetica-Bold",
        bulletFontSize=9.7, leftIndent=16, bulletColor=ACCENT,
    )


def bullets(items: list[str]) -> ListFlowable:
    # start="•" erzwingt den runden Punkt. Ohne die Angabe waehlt ReportLab
    # ein Zeichen, das Helvetica nicht fuehrt - im PDF erscheint dann ein
    # winziges Sternchen.
    return ListFlowable(
        [ListItem(Paragraph(t, S_BODY), leftIndent=14) for t in items],
        bulletType="bullet", start="•", bulletFontName="Helvetica",
        bulletFontSize=9, leftIndent=14, bulletColor=ACCENT,
    )


def on_page(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(22 * mm, 16 * mm, 188 * mm, 16 * mm)
    canvas.setFont("Helvetica", 7.6)
    canvas.setFillColor(MUTED)
    canvas.drawString(22 * mm, 11 * mm, "cintel - Handbuch")
    canvas.drawRightString(188 * mm, 11 * mm, f"Seite {canvas.getPageNumber() - 1}")
    canvas.restoreState()


def on_cover(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(ACCENT)
    canvas.rect(0, 272 * mm, 210 * mm, 25 * mm, stroke=0, fill=1)
    canvas.restoreState()


def build() -> Path:
    doc = BaseDocTemplate(
        str(OUT), pagesize=A4,
        leftMargin=22 * mm, rightMargin=23 * mm,
        topMargin=22 * mm, bottomMargin=22 * mm,
        title="cintel - Handbuch zum Competitive Intel Research Tool",
        author="LoopForgeLab", subject="Bedienungsanleitung",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="n")
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[frame], onPage=on_cover),
        PageTemplate(id="body", frames=[frame], onPage=on_page),
    ])
    doc.build(story())
    return OUT


REPO = "260818_Business Intell_Comp research_Tool"
PROJ = f"C:\\Users\\olafp\\Desktop\\Arbeitsordner\\{REPO}"
MASTER_H = ("H:\\Geteilte Ablagen\\LoopForgeLab\\A_Strategy\\02_Deliverables\\"
            "Business_Competitor_Intel\\Competitors_DB\\"
            "Competitive_Intel_Master_DB_v2.2.xlsx")


def story() -> list:
    s: list = []

    # =========================================================== Titel
    s += [
        Spacer(1, 42 * mm),
        Paragraph("Wettbewerbsrecherche<br/>mit <font face='Courier'>cintel</font>",
                  S_TITLE),
        Spacer(1, 6),
        Paragraph("Handbuch zum Competitive Intel Research Tool", S_SUB),
        Spacer(1, 24 * mm),
    ]
    meta_rows = [
        ["Was es tut", "Findet Unternehmen, liest ihre Webseiten und traegt "
                       "Unternehmens- und Produktdaten in die Competitive Intel "
                       "Master DB ein"],
        ["Fuer wen", "Alle im Team. Programmierkenntnisse sind nicht noetig, "
                     "Erfahrung mit der Tastatureingabe von Befehlen auch nicht - "
                     "das wird in Kapitel 2 von Grund auf erklaert"],
        ["Repository", "github.com/OCP-69/260818_Business-Intell_Comp-research_Tool"],
        ["Projektordner", PROJ],
        ["Stand", "18. August 2026"],
    ]
    S_MK = _style("mk", fontName="Helvetica-Bold", fontSize=8.6, leading=12.4,
                  textColor=ACCENT)
    meta = Table([[Paragraph(k, S_MK), Paragraph(v, S_CELL)] for k, v in meta_rows],
                 colWidths=[34 * mm, 131 * mm])
    meta.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
        ("LINEABOVE", (0, 0), (-1, 0), 1.0, ACCENT),
        ("LINEBELOW", (0, -1), (-1, -1), 1.0, ACCENT),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]))
    s += [meta, Spacer(1, 16 * mm)]
    s += [Paragraph("Dieses Handbuch setzt keinerlei Vorwissen voraus. Jeder Befehl "
                    "ist vollstaendig abgedruckt und kann eins zu eins kopiert "
                    "werden.", S_SUB)]
    s += [NextPageTemplate("body"), PageBreak()]

    # =========================================================== Inhalt
    s += h1("Inhalt")
    for num, title, what in [
        ("1", "Was dieses Tool ueberhaupt macht", "Das Grundprinzip in fuenf Minuten"),
        ("2", "Das Terminal - wo Sie die Befehle eintippen",
         "Von Grund auf, mit erster Uebung"),
        ("3", "Wo alles liegt und was sich womit abgleicht",
         "Drei Orte, zwei Abgleichverfahren"),
        ("4", "Wo Sie mit der Recherche anfangen", "Der richtige erste Schritt"),
        ("5", "Voraussetzungen", "Was einmalig eingerichtet sein muss"),
        ("6", "Das Tool starten", "Schritt fuer Schritt zum ersten Lauf"),
        ("7", "Die Ergebnisse verstehen", "Welche Datei was enthaelt"),
        ("8", "Qualitaet und Verhalten anpassen", "Wenn Ihnen etwas nicht gefaellt"),
        ("9", "Spalten ergaenzen", "Die Master-Tabelle erweitern"),
        ("10", "Laeufe automatisieren", "Profile und Zeitsteuerung"),
        ("11", "Aenderungen ueber GitHub einbringen", "Branch, Commit, Pull Request"),
        ("12", "Fehlermeldungen nachschlagen", "Was die Meldungen bedeuten"),
        ("13", "Befehlsuebersicht", "Alles auf einen Blick"),
    ]:
        s.append(Paragraph(
            f'<font color="#1f4e79"><b>{num}.</b></font>&nbsp;&nbsp;<b>{title}</b>'
            f'<font color="#5b6470"> &mdash; {what}</font>', S_TOC))
    s += [Spacer(1, 10), callout(
        "Wenn Sie noch nie einen Befehl eingetippt haben",
        "Lesen Sie Kapitel 2 vollstaendig und machen Sie die kleine Uebung am Ende. "
        "Danach sind alle uebrigen Kapitel bedienbar. Ueberspringen Sie Kapitel 2 "
        "nicht - dort steht, in welches Fenster die Befehle gehoeren.")]
    s.append(PageBreak())

    # ============================================================== 1
    s += h1("1. Was dieses Tool ueberhaupt macht")
    s += [p(
        "Es gibt eine zentrale Excel-Datei, die <b>Competitive Intel Master DB</b>. "
        "Darin steht, welche Unternehmen im Markt fuer uns relevant sind, was sie "
        "anbieten und wie wir sie einschaetzen. Diese Datei von Hand zu pflegen ist "
        "muehsam: man muesste jede Firmenwebseite einzeln aufrufen, die Produkte "
        "heraussuchen und alles abtippen.")]
    s += [p(
        "Genau das erledigt <b>cintel</b>. Sie sagen dem Tool, welche Firmen oder "
        "welches Marktsegment Sie interessieren. Das Tool ruft die Webseiten auf, "
        "liest sie, sortiert die Informationen und schreibt sie in eine neue "
        "Fassung der Excel-Datei.")]

    s += [Paragraph("So sieht das Ergebnis in der Tabelle aus", S_H2)]
    s += [p("Jede Firma bekommt <b>eine Zeile fuer das Unternehmen selbst</b> und "
            "darunter <b>je eine Zeile pro Produkt</b>. Alle Zeilen einer Firma "
            "teilen sich dieselbe Company_ID:")]
    s += [table([
        ["Company_ID", "Company &amp; Product", "Company", "Product_name",
         "Was in der Zeile steht"],
        ["42", "Company information", "Autodesk", "<i>(leer)</i>",
         "Gruendungsjahr, Mitarbeiterzahl, Standort, Umsatz"],
        ["42", "Product", "Autodesk", "Revit",
         "Kategorie, USP, Preismodell, Nachhaltigkeit"],
        ["42", "Product", "Autodesk", "Fusion 360", "dasselbe, aber fuer dieses Produkt"],
    ], [20 * mm, 30 * mm, 22 * mm, 25 * mm, 68 * mm])]

    s += [Paragraph("Die fuenf Arbeitsschritte des Tools", S_H2)]
    s += [p("Das Tool arbeitet immer in derselben Reihenfolge. Sie muessen das nicht "
            "steuern - es hilft nur beim Verstehen der Bildschirmausgabe:")]
    s += [table([
        ["Schritt", "Was passiert"],
        ["1. Finden", "Welche Firmen bearbeitet werden - entweder bekannte Firmen "
                      "mit Luecken oder neue Firmen aus einer Websuche"],
        ["2. Abgleichen", "Ist die Firma schon in der Tabelle? Verglichen wird ueber "
                          "die Internetadresse und den Firmennamen"],
        ["3. Webseite lesen", "Startseite aufrufen, dann Produkt- und Loesungsseiten. "
                              "Hier wird auch geprueft, ob die Firma echt ist"],
        ["4. Auswerten", "Aus dem Seitentext werden die Tabellenfelder gefuellt"],
        ["5. Eintragen", "Die Zeilen kommen in eine neue Fassung der Excel-Datei"],
    ], [30 * mm, 135 * mm])]

    s += [Spacer(1, 8), callout(
        "Die wichtigste Sicherung",
        "In Schritt 3 prueft das Tool, ob die Webseite wirklich erreichbar ist und "
        "ob der Firmenname darauf vorkommt. Beim Aufbau lieferte die Websuche eine "
        "Firma mit falscher Internetadresse - ohne diese Pruefung waere eine "
        "Karteileiche in der Tabelle gelandet. Firmen, die durchfallen, werden mit "
        "Begruendung in einer eigenen Datei aufgelistet.")]
    s += [Spacer(1, 8), callout(
        "Ihre Originaldatei wird nie veraendert",
        "Das Tool liest die Master-Datei nur. Geschrieben wird immer eine <b>neue "
        "Datei mit hoeherer Versionsnummer</b> in einem Ausgabeordner auf Ihrem "
        "Rechner. Sie koennen also nichts kaputtmachen.")]
    s.append(PageBreak())

    # ============================================================== 2
    s += h1("2. Das Terminal - wo Sie die Befehle eintippen")
    s += [p(
        "Alle Befehle in diesem Handbuch werden in ein bestimmtes Fenster getippt. "
        "Dieses Fenster heisst <b>Terminal</b>. Wenn Sie damit noch nie gearbeitet "
        "haben: kein Problem, dieses Kapitel erklaert es von Grund auf.")]

    s += [Paragraph("2.1 Was ein Terminal ist", S_H2)]
    s += [p(
        "Normalerweise bedienen Sie den Computer mit der Maus: klicken, ziehen, "
        "Menues aufklappen. Ein Terminal ist der andere Weg - Sie <b>schreiben "
        "dem Computer auf, was er tun soll</b>, und druecken die Eingabetaste.")]
    s += [p(
        "Stellen Sie sich den Unterschied so vor: Mit der Maus zeigen Sie auf Dinge "
        "im Regal. Im Terminal schreiben Sie einen Bestellzettel. Der Zettel ist "
        "unhandlicher, aber Sie koennen darauf Dinge bestellen, fuer die es gar "
        "keinen Knopf gibt - und Sie koennen denselben Zettel jederzeit wieder "
        "verwenden. Genau das brauchen wir hier.")]
    s += [Spacer(1, 4), callout(
        "Es ist ein ganz normales Fenster",
        "Das Terminal laesst sich verschieben, vergroessern und mit dem X oben "
        "rechts schliessen wie jedes andere Fenster. Es sieht nur ungewohnt aus, "
        "weil es fast nur aus Text besteht.")]

    s += [Paragraph("2.2 Welches Terminal - und wie Sie es oeffnen", S_H2)]
    s += [p("Windows bringt mehrere solcher Fenster mit. Wir nutzen durchgaengig "
            "<b>Windows PowerShell</b>. Bitte kein anderes - die Skripte in "
            "Kapitel 10 laufen nur dort.")]
    s += [p("So oeffnen Sie es:")]
    s += [steps([
        "Druecken Sie die <b>Windows-Taste</b> auf der Tastatur (die mit dem "
        "Fenster-Symbol, unten links neben der Leertaste). Das Startmenue geht auf, "
        "der Schreibcursor steht im Suchfeld.",
        "Tippen Sie <b>powershell</b>. Sie muessen nirgends hinklicken - die Suche "
        "laeuft mit.",
        "In der Trefferliste erscheint oben <b>Windows PowerShell</b>. Druecken Sie "
        "die <b>Eingabetaste</b> oder klicken Sie den Treffer an.",
    ])]
    s += [Spacer(1, 4), p("Es oeffnet sich ein Fenster mit dunklem oder hellem "
                          "Hintergrund. Darin steht eine Zeile wie diese, mit einem "
                          "blinkenden Strich am Ende:")]
    s += [code("PS C:\\Users\\olafp>")]
    s += [p("Diese Zeile heisst <b>Eingabeaufforderung</b>. Sie bedeutet: <i>Ich bin "
            "bereit, sag mir was.</i> Der blinkende Strich zeigt, wo Ihre Eingabe "
            "erscheint. Alles, was Sie jetzt tippen, landet hinter dem "
            "Groesser-als-Zeichen.")]

    s += [Paragraph("2.3 Der wichtigste Begriff: das aktuelle Verzeichnis", S_H2)]
    s += [p(
        "In dem Text <font face='Courier'>PS C:\\Users\\olafp&gt;</font> steckt eine "
        "Information, die spaeter ueber Erfolg und Misserfolg entscheidet: "
        "<b>C:\\Users\\olafp</b> ist der Ordner, in dem das Terminal gerade "
        "<i>steht</i>.")]
    s += [p(
        "Das ist so, als stuenden Sie in einem Aktenschrank vor einer bestimmten "
        "Schublade. Sagen Sie <i>\"gib mir die Mappe Mueller\"</i>, wird in genau "
        "dieser Schublade gesucht - nicht im ganzen Schrank. Steht das Terminal im "
        "falschen Ordner, findet es unsere Befehle nicht, obwohl alles korrekt "
        "installiert ist.")]
    s += [Spacer(1, 4), callout(
        "Die haeufigste Fehlerursache ueberhaupt",
        "Fast alle Meldungen der Art <i>\"kann nicht gefunden werden\"</i> kommen "
        "daher, dass das Terminal im falschen Ordner steht. Pruefen Sie das immer "
        "zuerst.", warn=True)]

    s += [Paragraph("2.4 In den Projektordner wechseln", S_H2)]
    s += [p("Der Befehl zum Wechseln heisst <font face='Courier'>cd</font> - kurz "
            "fuer <i>change directory</i>, Verzeichnis wechseln. Dahinter kommt der "
            "Zielordner in Anfuehrungszeichen:")]
    s += [code(f'cd "{PROJ}"')]
    s += [Spacer(1, 4)]
    s += [bullets([
        "<b>Warum die Anfuehrungszeichen?</b> Der Pfad enthaelt Leerzeichen "
        "(\"Business Intell\"). Ohne Anfuehrungszeichen haelt das Terminal jedes "
        "Leerzeichen fuer das Ende des Pfades und meldet einen Fehler. Kopieren Sie "
        "die Zeile deshalb immer mitsamt beiden Anfuehrungszeichen.",
        "<b>Gross- und Kleinschreibung</b> ist bei Ordnernamen egal.",
        "Hat es geklappt, aendert sich die Eingabeaufforderung - sie zeigt jetzt "
        "den neuen Ordner an. Es erscheint <b>keine</b> Erfolgsmeldung. Beim "
        "Terminal gilt: keine Nachricht ist eine gute Nachricht.",
    ])]
    s += [p("Danach sieht die Zeile so aus:")]
    s += [code(f"PS {PROJ}>")]

    s += [Paragraph("2.5 Text ins Terminal kopieren", S_H2)]
    s += [p("Sie muessen nichts abtippen. Aus diesem PDF heraus geht es so:")]
    s += [steps([
        "Den Befehl im PDF mit der Maus markieren und <b>Strg + C</b> druecken.",
        "In das PowerShell-Fenster klicken, damit es aktiv ist.",
        "<b>Strg + V</b> druecken. Alternativ genuegt ein <b>Rechtsklick</b> - in "
        "PowerShell fuegt der rechte Mausklick direkt ein.",
        "<b>Eingabetaste</b> druecken. Erst jetzt wird der Befehl ausgefuehrt.",
    ])]
    s += [Spacer(1, 4), callout(
        "Ein Befehl laeuft erst mit der Eingabetaste",
        "Solange Sie nicht Enter gedrueckt haben, koennen Sie den Text noch "
        "korrigieren oder mit der Ruecktaste ganz loeschen. Nichts ist passiert.")]

    s += [Paragraph("2.6 Woran Sie erkennen, dass ein Befehl fertig ist", S_H2)]
    s += [p("Waehrend ein Befehl laeuft, erscheinen Textzeilen, und Sie koennen "
            "nichts eintippen. <b>Fertig ist er, wenn die Eingabeaufforderung "
            "wieder erscheint</b> - also wieder eine Zeile mit "
            "<font face='Courier'>PS ...&gt;</font> und blinkendem Strich da ist.")]
    s += [p("Ein Recherchelauf braucht mehrere Minuten. Das Fenster sieht dabei "
            "zeitweise aus, als haenge es. Das ist normal. Lassen Sie es einfach "
            "stehen und warten Sie.")]
    s += [Spacer(1, 4), table([
        ["Situation", "Was zu tun ist"],
        ["Der Befehl laeuft und laeuft", "Warten. Ein Lauf ueber 5 Firmen dauert "
                                         "3 bis 5 Minuten"],
        ["Sie wollen abbrechen", "<b>Strg + C</b> druecken. Der Lauf stoppt sofort. "
                                 "Bereits geschriebene Dateien bleiben erhalten"],
        ["Sie sind fertig fuer heute", "Fenster mit dem X schliessen oder "
                                       "<font face='Courier'>exit</font> eintippen"],
        ["Sie haben sich vertippt", "Ruecktaste zum Loeschen, oder <b>Esc</b> loescht "
                                    "die ganze Zeile"],
    ], [42 * mm, 123 * mm])]

    s += [Paragraph("2.7 Erste Uebung", S_H2)]
    s += [p("Bevor Sie richtig loslegen, machen Sie diese drei Schritte einmal "
            "durch. Sie dauern zwei Minuten und geben Sicherheit.")]
    s += [Paragraph("Uebung 1 &mdash; Wo stehe ich gerade?", S_H3)]
    s += [code("pwd")]
    s += [p("Antwort ist der Ordner, in dem das Terminal steht. "
            "<font face='Courier'>pwd</font> steht fuer <i>print working "
            "directory</i>.")]
    s += [Paragraph("Uebung 2 &mdash; In den Projektordner wechseln", S_H3)]
    s += [code(f'cd "{PROJ}"')]
    s += [Paragraph("Uebung 3 &mdash; Was liegt hier?", S_H3)]
    s += [code("ls")]
    s += [p("Es erscheint eine Liste. Darin muessen unter anderem "
            "<font face='Courier'>cintel</font>, <font face='Courier'>config</font>, "
            "<font face='Courier'>docs</font> und "
            "<font face='Courier'>README.md</font> auftauchen. Sehen Sie diese "
            "Namen, sind Sie am richtigen Ort und alles Weitere funktioniert.")]
    s += [Spacer(1, 6), callout(
        "Sie muessen den cd-Befehl bei jedem neuen Fenster wiederholen",
        "Das Terminal merkt sich den Ordner nur, solange das Fenster offen ist. "
        "Oeffnen Sie es am naechsten Tag neu, steht es wieder in "
        "<font face='Courier'>C:\\Users\\olafp</font>. Der erste Befehl in einem "
        "frischen Fenster ist deshalb immer der <font face='Courier'>cd</font>-Befehl.")]
    s.append(PageBreak())

    # ============================================================== 3
    s += h1("3. Wo alles liegt und was sich womit abgleicht")
    s += [p("Es gibt <b>drei Orte</b>, an denen etwas zu diesem Projekt liegt. Wer "
            "sie durcheinanderbringt, sucht Dateien an der falschen Stelle. Zwei "
            "davon gleichen sich automatisch ab, einer nur auf Befehl.")]

    s += [Paragraph("3.1 Die drei Orte", S_H2)]
    s += [table([
        ["Ort", "Was dort liegt", "Abgleich"],
        ["<b>1. Laufwerk H:</b><br/><font face='Courier'>H:\\Geteilte Ablagen\\"
         "LoopForgeLab\\...</font>",
         "Die offizielle Master-Tabelle des Teams. Der gemeinsame Stand, den alle "
         "sehen",
         "<b>Automatisch.</b> H: ist Google Drive. Aenderungen erscheinen ohne Ihr "
         "Zutun bei allen Kollegen"],
        ["<b>2. Projektordner</b><br/><font face='Courier'>C:\\Users\\olafp\\"
         "Desktop\\Arbeitsordner\\...</font>",
         "Das Programm selbst und Ihre Laufergebnisse im Unterordner "
         "<font face='Courier'>data</font>",
         "<b>Nur auf Befehl.</b> Nichts verlaesst diesen Ordner, solange Sie es "
         "nicht anstossen"],
        ["<b>3. GitHub</b><br/><font face='Courier'>github.com/OCP-69/260818_...</font>",
         "Ausschliesslich der Programmcode und dieses Handbuch. <b>Keine</b> "
         "Wettbewerbsdaten",
         "<b>Nur auf Befehl.</b> Ueber die Git-Befehle aus Kapitel 11"],
    ], [46 * mm, 62 * mm, 57 * mm])]

    s += [Spacer(1, 8), Paragraph("3.2 Zwei verschiedene Abgleichverfahren", S_H2)]
    s += [p("Das ist der Punkt, an dem die meisten Missverstaendnisse entstehen. "
            "Die beiden Verfahren haben nichts miteinander zu tun:")]
    s += [table([
        ["", "Google Drive (Laufwerk H:)", "Git und GitHub"],
        ["Was wird abgeglichen", "Dateien - Excel, Dokumente, Bilder",
         "Programmcode und Handbuch"],
        ["Wer stoesst es an", "Niemand. Es laeuft im Hintergrund",
         "Sie selbst, mit einem Befehl"],
        ["Wie schnell", "Sekunden bis wenige Minuten",
         "Sofort, aber eben erst auf Befehl"],
        ["Woran erkennbar", "Ein Google-Drive-Symbol unten rechts in der Taskleiste",
         "<font face='Courier'>git status</font> zeigt es an"],
    ], [34 * mm, 66 * mm, 65 * mm])]

    s += [Spacer(1, 8), callout(
        "Der Projektordner wird NICHT automatisch mit GitHub abgeglichen",
        "Wenn Sie eine Datei im Projektordner aendern, passiert auf GitHub zunaechst "
        "gar nichts. Erst die Befehle aus Kapitel 11 laden sie hoch. Umgekehrt "
        "genauso: aendert ein Kollege etwas auf GitHub, merken Sie das erst, wenn "
        "Sie <font face='Courier'>git pull</font> ausfuehren.", warn=True)]

    s += [Spacer(1, 8), Paragraph("3.3 Wo arbeiten Sie eigentlich?", S_H2)]
    s += [p("<b>Immer im Projektordner auf C:.</b> Dort steht das Terminal, dort "
            "laufen die Befehle, dort entstehen die Ergebnisse. Auf H: greifen Sie "
            "nur lesend zu, und GitHub sehen Sie nur im Browser oder ueber "
            "Git-Befehle.")]
    s += [p("Der Weg einer Datei durch die drei Orte sieht so aus:")]
    s += [code("""H:\\ Master-Tabelle  ---lesen--->  Projektordner C:\\
                                        |
                                        v
                          data\\outputs\\...v2.3.xlsx   (Ergebnis)
                                        |
                       Sie pruefen es und kopieren es
                                        |
                                        v
                             H:\\ ...\\Competitors_DB\\   (Team sieht es)""")]

    s += [Spacer(1, 6), Paragraph("3.4 Ein typischer Arbeitsablauf", S_H2)]
    s += [steps([
        "Terminal oeffnen und in den Projektordner wechseln (Kapitel 2).",
        "Recherchelauf starten. Grundlage ist die Master-Tabelle - entweder direkt "
        "von H: oder die zuletzt bereinigte Fassung aus "
        "<font face='Courier'>data\\outputs</font>.",
        "Ergebnis pruefen: die neue Excel-Datei ansehen und den Bericht "
        "<font face='Courier'>report.md</font> lesen (Kapitel 7).",
        "<b>Erst wenn Sie zufrieden sind:</b> die neue Datei nach H: kopieren, "
        "damit das Team sie sieht. Das ist ein bewusster Schritt und passiert "
        "nicht von selbst.",
        "Haben Sie am Programm etwas geaendert, laden Sie das ueber Git nach "
        "GitHub hoch (Kapitel 11). Ergebnisdateien gehoeren <b>nicht</b> dorthin.",
    ])]

    s += [Spacer(1, 6), Paragraph("Ergebnis ins Team-Laufwerk uebernehmen", S_H3)]
    s += [p("Am einfachsten mit dem Explorer: beide Ordner nebeneinander oeffnen und "
            "die Datei hinueberziehen. Wer lieber tippt, merkt sich das Ziel zuerst "
            "unter einem kurzen Namen und kopiert dann - beide Zeilen nacheinander, "
            "jeweils mit Eingabetaste:")]
    s += [code('$ziel = "H:\\Geteilte Ablagen\\LoopForgeLab\\A_Strategy\\02_Deliverables\\Business_Competitor_Intel\\Competitors_DB"')]
    s += [code('copy "data\\outputs\\Competitive_Intel_Master_DB_v2.3.xlsx" $ziel')]
    s += [Spacer(1, 2), Paragraph(
        "Die erste Zeile legt nur den Zielpfad ab und gibt nichts aus. Erst die "
        "zweite kopiert. Der Name <font face='Courier'>$ziel</font> gilt, solange "
        "das Fenster offen bleibt.", S_SMALL)]

    s += [Spacer(1, 6), callout(
        "Warum die Ergebnisse nicht gleich auf H: geschrieben werden",
        "Ein Lauf koennte fehlerhafte Zeilen erzeugen. Landeten die sofort im "
        "Team-Laufwerk, saehen alle Kollegen sie sofort. Der Zwischenschritt gibt "
        "Ihnen die Gelegenheit zu pruefen, bevor etwas offiziell wird.")]
    s.append(PageBreak())

    # ============================================================== 4
    s += h1("4. Wo Sie mit der Recherche anfangen")
    s += [p("Es gibt zwei Betriebsarten. Die Wahl entscheidet, welche Firmen "
            "bearbeitet werden.")]

    s += [Paragraph("Betriebsart A: <font face='Courier'>gaps</font> - Luecken im "
                    "Bestand schliessen", S_H2)]
    s += [p("Das Tool nimmt Firmen, die <b>bereits in der Tabelle stehen</b>, bei "
            "denen aber Felder leer sind, und fuellt diese Luecken. Es kommen keine "
            "neuen Firmen dazu.")]
    s += [p("<b>Damit sollten Sie anfangen.</b> Der Grund: Sie kennen diese Firmen "
            "und koennen sofort beurteilen, ob das Tool vernuenftige Ergebnisse "
            "liefert. Bei unbekannten Firmen wuessten Sie nicht, ob ein Fehler "
            "vorliegt oder die Information einfach neu ist.")]

    s += [Paragraph("Betriebsart B: <font face='Courier'>new</font> - neue Firmen "
                    "entdecken", S_H2)]
    s += [p("Das Tool durchsucht das Internet nach Firmen in den Marktsegmenten, die "
            "Sie vorgeben, und legt fuer jede neue Firma einen kompletten Datensatz "
            "an. Nutzen Sie das erst, wenn Sie mit den Ergebnissen aus Betriebsart A "
            "zufrieden sind.")]

    s += [Spacer(1, 6), table([
        ["", "gaps (Standard)", "new"],
        ["Zweck", "Bekannte Firmen vervollstaendigen", "Unbekannte Firmen finden"],
        ["Websuche", "Nein", "Ja"],
        ["Neue Zeilen", "Nur neue Produkte", "Ganze Firmen mit allen Produkten"],
        ["Empfehlung", "Hier anfangen", "Erst danach"],
    ], [26 * mm, 72 * mm, 67 * mm])]

    s += [Spacer(1, 10), Paragraph("Ihr allererster Lauf", S_H2)]
    s += [p("Nehmen Sie <b>Betriebsart gaps mit fuenf Firmen</b>. Das dauert wenige "
            "Minuten, und Sie sehen am Ergebnis sofort, ob die Qualitaet stimmt. Der "
            "genaue Befehl steht in Kapitel 6.")]
    s += [Spacer(1, 8), callout(
        "Womit fuellt das Tool die Luecken?",
        "Welche Spalten als lueckenhaft gelten, steht in der Datei "
        "<font face='Courier'>config\\targets.yaml</font>. Voreingestellt sind die "
        "vier Spalten mit dem niedrigsten Fuellgrad: Product_name, "
        "All_Key_Categories, R-Strategies und Remarks. Wie Sie das aendern, steht in "
        "Kapitel 8.")]
    s.append(PageBreak())

    # ============================================================== 5
    s += h1("5. Voraussetzungen")
    s += [p("Diese Dinge muessen <b>einmalig</b> eingerichtet sein. Danach nie wieder.")]

    s += [Paragraph("5.1 Was auf dem Rechner sein muss", S_H2)]
    s += [p("Tippen Sie die Pruefbefehle nacheinander ins Terminal. Jeder muss eine "
            "Versionsnummer ausgeben.")]
    s += [table([
        ["Was", "Wozu", "Pruefbefehl"],
        ["Python 3.11 oder neuer", "Das Tool ist in Python geschrieben", "py --version"],
        ["Die claude-Anwendung", "Liest und sortiert die Webseiteninhalte",
         "claude --version"],
        ["Git", "Nur noetig fuer Kapitel 11", "git --version"],
        ["codex <i>(freiwillig)</i>", "Zweitmeinung zur Kontrolle", "codex --version"],
    ], [42 * mm, 68 * mm, 55 * mm])]
    s += [Spacer(1, 4), Paragraph(
        "Kommt stattdessen <i>\"Die Benennung ... wurde nicht als Name eines "
        "Cmdlet ... erkannt\"</i>, fehlt das Programm und muss installiert werden.",
        S_SMALL)]

    s += [Paragraph("5.2 Anmeldung - und warum es nichts extra kostet", S_H2)]
    s += [p("Das Tool nutzt Ihr vorhandenes <b>Claude-Abonnement</b>. Es braucht "
            "<b>keinen kostenpflichtigen Zugangsschluessel</b>, den man zusaetzlich "
            "kaufen muesste. Damit das funktioniert, muss die claude-Anwendung "
            "angemeldet sein.")]
    s += [p("Zum Pruefen oder Anmelden starten Sie sie einmal ohne Zusatz:")]
    s += [code("claude")]
    s += [p("Erscheint eine Eingabezeile, sind Sie angemeldet - mit "
            "<font face='Courier'>/exit</font> wieder heraus. Werden Sie nach einer "
            "Anmeldung gefragt, tippen Sie:")]
    s += [code("/login")]
    s += [p("und folgen den Anweisungen im Browser. Das ist einmalig noetig.")]

    s += [Paragraph("5.3 Programmbibliotheken installieren", S_H2)]
    s += [p("Terminal oeffnen, in den Projektordner wechseln, dann einmalig:")]
    s += [code("py -m pip install -r requirements.txt")]

    s += [Paragraph("5.4 Der Selbsttest", S_H2)]
    s += [p("Pruefen Sie zum Abschluss, ob alles bereit ist:")]
    s += [code("py -m cintel doctor")]
    s += [p("Erwartete Ausgabe:")]
    s += [code("""CINTEL DOCTOR
==============================================================
  [ok]   claude-CLI gefunden: C:\\Users\\olafp\\.local\\bin\\claude.EXE
  [ok]   codex-CLI gefunden (Cross-Check moeglich)
  [ok]   Taxonomie: config/taxonomy.yaml
  [ok]   Ziele: config/targets.yaml
  [--]   Master-DB: nicht angegeben (--master)
==============================================================
  Bereit.""")]
    s += [Spacer(1, 6), callout(
        "Steht dort \"Bereit.\", koennen Sie loslegen.",
        "Die Zeile mit <font face='Courier'>[--]</font> ist kein Fehler - Sie haben "
        "hier nur noch keine Tabelle angegeben. Steht bei "
        "<font face='Courier'>claude-CLI</font> dagegen "
        "<font face='Courier'>[FEHL]</font>, gehen Sie zurueck zu Abschnitt 5.2.")]
    s.append(PageBreak())

    # ============================================================== 6
    s += h1("6. Das Tool starten")

    s += [Paragraph("6.1 Welche Tabelle nehmen Sie als Grundlage?", S_H2)]
    s += [table([
        ["Datei", "Beschreibung"],
        ["<font face='Courier'>...Master_DB_v2.2.xlsx</font> auf H:\\",
         "Das Original im Team-Laufwerk. Enthaelt noch bekannte Fehler: kaputte "
         "Umlaute, Seitentitel statt Internetadressen, uneinheitliche Schreibweisen"],
        ["<font face='Courier'>data\\outputs\\...v2.2r.xlsx</font>",
         "<b>Empfohlen.</b> Dieselbe Tabelle, aber bereinigt: 1013 Korrekturen, "
         "danach keine Fehler mehr"],
    ], [58 * mm, 107 * mm])]

    s += [Paragraph("6.2 Der erste Lauf - fuenf Firmen", S_H2)]
    s += [p("Ein Befehl, eine Zeile. Alles kopieren, Eingabetaste:")]
    s += [code('py -m cintel run --master "data\\outputs\\Competitive_Intel_Master_DB_v2.2r.xlsx" --limit 5 --version 2.3')]

    s += [Spacer(1, 4), Paragraph("Was die Bestandteile bedeuten", S_H3)]
    s += [table([
        ["Bestandteil", "Bedeutung"],
        ["<font face='Courier'>py -m cintel</font>", "Starte das Programm cintel"],
        ["<font face='Courier'>run</font>", "Fuehre einen Recherchelauf aus"],
        ["<font face='Courier'>--master \"...\"</font>",
         "Welche Tabelle als Grundlage dient. Anfuehrungszeichen sind noetig, weil "
         "der Pfad Leerzeichen enthaelt"],
        ["<font face='Courier'>--limit 5</font>",
         "Hoechstens fuenf Firmen. Schuetzt vor einem versehentlich riesigen Lauf"],
        ["<font face='Courier'>--version 2.3</font>",
         "Wie die Ergebnisdatei heissen soll: ...<b>_v2.3</b>.xlsx"],
    ], [42 * mm, 123 * mm])]
    s += [Spacer(1, 3), Paragraph(
        "Die Bestandteile mit zwei Bindestrichen heissen <i>Schalter</i>. Ihre "
        "Reihenfolge ist beliebig.", S_SMALL)]

    s += [Spacer(1, 8), Paragraph("6.3 Was Sie auf dem Bildschirm sehen", S_H2)]
    s += [code("""Master-DB: 918 Zeilen, 375 Firmen
Modus 'gaps': 5 Firmen zur Anreicherung ausgewaehlt.
[1/5] 3D Spark - https://www.3dspark.de
    6 Seiten -> 1 Firmenzeile + 7 Produkte
[2/5] aPriori - https://www.apriori.com/blog/...
    12 Seiten -> 1 Firmenzeile + 7 Produkte
[3/5] Autodesk - https://www.autodesk.com/bim-360
    abgelehnt: Homepage nicht erreichbar (durch robots.txt untersagt)
[4/5] CAESES (Friendship Systems) - https://www.caeses.com
    1 Seiten -> 1 Firmenzeile + 1 Produkte
[5/5] Celus - https://celus.io
    10 Seiten -> 1 Firmenzeile + 2 Produkte

11 neue Zeilen, 1 bestehende Zeilen ergaenzt, 12 Felder gefuellt, 73 uebersprungen

Neue Version : data\\outputs\\Competitive_Intel_Master_DB_v2.3.xlsx
Lauf-Ordner  : data\\outputs\\run_20260818_230425""")]
    s += [Spacer(1, 5)]
    s += [bullets([
        "<b>abgelehnt</b> ist kein Programmfehler. Manche Firmen - etwa Autodesk, "
        "Cadence oder Aspen - verbieten in ihrer Datei <i>robots.txt</i> "
        "ausdruecklich das automatische Auslesen. Das respektieren wir. In einer "
        "Stichprobe von 60 Firmen betraf das 5.",
        "<b>uebersprungen</b> heisst: das Feld war schon gefuellt oder das Produkt "
        "stand bereits in der Tabelle. Auch das ist gewollt.",
        "Rechnen Sie mit etwa <b>30 bis 60 Sekunden pro Firma</b>.",
    ])]

    s += [Spacer(1, 8), Paragraph("6.4 Erst schauen, nichts schreiben", S_H2)]
    s += [p("Wenn Sie den Ablauf sehen wollen, ohne dass eine Excel-Datei entsteht, "
            "haengen Sie <font face='Courier'>--dry-run</font> an. Die "
            "Berichtsdateien werden trotzdem erzeugt:")]
    s += [code('py -m cintel run --master "data\\outputs\\Competitive_Intel_Master_DB_v2.2r.xlsx" --limit 5 --dry-run')]
    s += [Spacer(1, 8), callout(
        "Gleiche Versionsnummer ueberschreibt die Datei",
        "Starten Sie zweimal mit <font face='Courier'>--version 2.3</font>, wird die "
        "vorhandene Datei ersetzt. Vergeben Sie fuer jeden Lauf, den Sie behalten "
        "wollen, eine neue Nummer: 2.3, dann 2.4, dann 2.5.", warn=True)]
    s.append(PageBreak())

    # ============================================================== 7
    s += h1("7. Die Ergebnisse verstehen")
    s += [p("Alles landet in diesem Ordner:")]
    s += [code(f"{PROJ}\\data\\outputs\\")]
    s += [p("Diesen Ordner oeffnen Sie am schnellsten aus dem Terminal heraus - der "
            "Windows-Explorer geht auf:")]
    s += [code("explorer data\\outputs")]

    s += [Paragraph("7.1 Die Excel-Datei", S_H2)]
    s += [p("<font face='Courier'>Competitive_Intel_Master_DB_v2.3.xlsx</font> ist "
            "die vollstaendige Tabelle - alter Bestand <b>plus</b> die neuen Zeilen. "
            "Die neuen Zeilen stehen <b>ganz unten</b>. Mit <b>Strg + Ende</b> "
            "springen Sie in Excel sofort dorthin.")]

    s += [Paragraph("7.2 Der Lauf-Ordner", S_H2)]
    s += [p("Zu jedem Lauf gehoert ein Ordner "
            "<font face='Courier'>run_JJJJMMTT_HHMMSS</font> mit fuenf Dateien. "
            "<b>Diese Dateien sind Ihre Qualitaetskontrolle</b> - schauen Sie "
            "hinein, bevor Sie das Ergebnis weiterverwenden:")]
    s += [table([
        ["Datei", "Was drinsteht", "Wann Sie sie brauchen"],
        ["<b>report.md</b>", "Zusammenfassung: was gefuellt, was abgelehnt, was "
                             "uebersprungen wurde",
         "<b>Immer zuerst lesen.</b> Mit Notepad oder Word zu oeffnen"],
        ["<b>rejected.csv</b>", "Jede abgelehnte Firma mit Begruendung",
         "Wenn eine Firma fehlt, die Sie erwartet haben"],
        ["<b>new_rows.csv</b>", "Die neuen Zeilen einzeln",
         "Zum schnellen Durchsehen ohne Excel"],
        ["<b>sources.csv</b>", "Jede aufgerufene Seite mit Status",
         "Wenn Sie belegen wollen, woher eine Angabe stammt"],
        ["<b>plan.json</b>", "Technisches Protokoll", "Bei Rueckfragen an die Technik"],
    ], [32 * mm, 68 * mm, 65 * mm])]

    s += [Spacer(1, 8), Paragraph("7.3 Woran Sie gute Ergebnisse erkennen", S_H2)]
    s += [p("Oeffnen Sie die neue Excel-Datei und pruefen Sie an den untersten "
            "Zeilen diese fuenf Punkte:")]
    s += [steps([
        "<b>Sind die Produktnamen echt?</b> Es muessen benannte Produkte sein "
        "(\"Fusion 360\"), keine Werbefloskeln (\"Unsere Loesung\").",
        "<b>Stimmt die Company_ID?</b> Alle Zeilen einer Firma muessen dieselbe "
        "Nummer tragen wie die schon vorhandenen Zeilen dieser Firma.",
        "<b>Ist die Spalte URL eine echte Internetadresse?</b> Sie muss mit "
        "<font face='Courier'>https://</font> beginnen.",
        "<b>Sind die Kategorien aus der Liste?</b> In <i>Sub Category B</i> duerfen "
        "nur die 30 vorgesehenen Werte stehen, keine frei erfundenen.",
        "<b>Wurde nichts ueberschrieben?</b> Ihre Einschaetzungen in "
        "<i>Competitor_Tier</i> und <i>Beachhead_Relevanz</i> muessen unveraendert "
        "sein.",
    ])]
    s += [Spacer(1, 6), callout(
        "Punkt 5 garantiert das Tool von sich aus",
        "Bei bereits bekannten Firmen werden <b>ausschliesslich leere Felder</b> "
        "gefuellt. Ihre von Hand gepflegten Bewertungen bleiben in jedem Fall "
        "stehen - auch wenn die Auswertung etwas anderes vorschlaegt. Was aus diesem "
        "Grund nicht uebernommen wurde, listet <i>report.md</i> auf.")]

    s += [Spacer(1, 8), Paragraph("7.4 Die Tabelle jederzeit pruefen lassen", S_H2)]
    s += [p("Dieser Befehl durchsucht eine beliebige Fassung nach Fehlern - kaputte "
            "Umlaute, falsche Internetadressen, doppelte Zeilen, unbekannte "
            "Kategorien:")]
    s += [code('py -m cintel validate --master "data\\outputs\\Competitive_Intel_Master_DB_v2.3.xlsx"')]
    s += [p("Und dieser bereinigt gefundene Fehler in eine neue Datei. Mit "
            "<font face='Courier'>--dry-run</font> zeigt er zunaechst nur an, was er "
            "aendern wuerde:")]
    s += [code('py -m cintel repair --master "<pfad zur xlsx>" --dry-run')]
    s.append(PageBreak())

    # ============================================================== 8
    s += h1("8. Qualitaet und Verhalten anpassen")
    s += [p("Alle Stellschrauben liegen in Textdateien im Ordner "
            "<font face='Courier'>config</font>. Sie oeffnen sie mit einem normalen "
            "Texteditor - Rechtsklick auf die Datei, <i>Oeffnen mit</i>, "
            "<i>Editor</i>.")]
    s += [Spacer(1, 4), callout(
        "Zwei Regeln beim Bearbeiten dieser Dateien",
        "<b>1.</b> Nur Leerzeichen zum Einruecken verwenden, niemals die "
        "Tabulatortaste. <b>2.</b> Die Einrueckung genau so beibehalten, wie sie "
        "ist. Beides fuehrt sonst zu einer Fehlermeldung beim Start.", warn=True)]

    s += [Spacer(1, 8), Paragraph("8.1 \"Es bearbeitet die falschen Firmen\"", S_H2)]
    s += [p("Datei: <font face='Courier'>config\\targets.yaml</font>. Der Abschnitt "
            "<font face='Courier'>gaps</font> bestimmt die Auswahl:")]
    s += [code("""gaps:
  target_columns:
    - "Product_name"
    - "All_Key_Categories of this company"
    - "R-Strategies"
    - "Remarks Product/solution"
  only_incomplete: true
  tier_priority:
    - "Tier 1 - Direkt"
    - "Tier 2 - Nachbar"
    - "Tier 3 - Beobachten"
  require_url: true""")]
    s += [Spacer(1, 4), bullets([
        "<b>Andere Spalten fuellen:</b> Zeilen unter "
        "<font face='Courier'>target_columns</font> aendern. Die Spaltennamen "
        "muessen <b>exakt</b> so geschrieben sein wie in der Excel-Kopfzeile.",
        "<b>Nur bestimmte Wettbewerber:</b> Bei "
        "<font face='Courier'>tier_priority</font> die unerwuenschten Zeilen "
        "loeschen. Wer nur Tier 1 bearbeiten will, laesst nur diese eine Zeile stehen.",
    ])]

    s += [Spacer(1, 8), Paragraph("8.2 \"Die Ergebnisse sind zu ungenau\"", S_H2)]
    s += [table([
        ["Beobachtung", "Was Sie tun"],
        ["Zu wenige Produkte gefunden",
         "In <font face='Courier'>targets.yaml</font> "
         "<font face='Courier'>max_pages_per_company</font> von 12 auf 20 erhoehen. "
         "Das Tool liest dann mehr Unterseiten"],
        ["Ungenaue oder erfundene Angaben",
         "Auf das staerkere Modell wechseln: unter <font face='Courier'>llm:</font> "
         "den Eintrag <font face='Courier'>model: \"sonnet\"</font> auf "
         "<font face='Courier'>\"opus\"</font> setzen. Langsamer, aber gruendlicher"],
        ["Zu viele unsichere Angaben landen in der Tabelle",
         "Die Confidence-Schwelle anheben. Sie steht in "
         "<font face='Courier'>cintel\\merge.py</font> als "
         "<font face='Courier'>min_confidence: float = 0.35</font>. Wert 0.6 laesst "
         "nur gut belegte Angaben durch"],
        ["Zahlen sollen doppelt geprueft werden",
         "Beim Start <font face='Courier'>--cross-check codex</font> anhaengen. "
         "Gruendungsjahr, Mitarbeiterzahl und Standort werden dann unabhaengig "
         "nachgeprueft und Abweichungen gemeldet. Setzt voraus, dass "
         "<font face='Courier'>codex login</font> einmal ausgefuehrt wurde"],
        ["Gruendungsjahr und Mitarbeiterzahl bleiben leer",
         "Diese Angaben stehen auf der Ueber-uns-Seite. Das Tool ruft sie "
         "bevorzugt ab, aber nicht jede Firma veroeffentlicht sie. "
         "Finanzierungsrunde und Umsatz stehen fast nie auf der eigenen "
         "Website - die muessen von Hand ergaenzt werden"],
        ["Marktsegmente fuer Betriebsart <i>new</i> aendern",
         "Im Abschnitt <font face='Courier'>new:</font> die Eintraege unter "
         "<font face='Courier'>key_categories</font>, "
         "<font face='Courier'>sub_categories</font> und "
         "<font face='Courier'>regions</font> anpassen - oder gleich ein eigenes "
         "Profil anlegen, siehe Kapitel 10"],
    ], [50 * mm, 115 * mm])]

    s += [Spacer(1, 8), Paragraph("8.3 \"Es erfindet neue Kategorien\"", S_H2)]
    s += [p("Datei: <font face='Courier'>config\\taxonomy.yaml</font>. Sie enthaelt "
            "die <b>erlaubten Werte</b>. Das Tool darf nichts anderes schreiben - "
            "genau deshalb gibt es diese Datei. Frueher war die Tabelle auf 34 "
            "Hauptkategorien angewachsen statt der vorgesehenen 8.")]
    s += [p("Eine neue Sub-Kategorie fuegen Sie so hinzu - beachten Sie den "
            "Bindestrich und die Anfuehrungszeichen:")]
    s += [code("""sub_categories:
  - "3D Content & Visualization"
  - "AI & Surrogate Simulation"
  - "Meine neue Kategorie"          # <- neue Zeile""")]
    s += [p("Damit die neue Kategorie auch verwendet wird, tragen Sie sie zusaetzlich "
            "unter <font face='Courier'>legend:</font> bei der passenden "
            "Hauptkategorie ein.")]
    s += [Spacer(1, 4), callout(
        "Schreibvarianten umlenken statt neu anlegen",
        "Taucht in der Tabelle eine abweichende Schreibweise auf, die eigentlich "
        "einen vorhandenen Wert meint, tragen Sie sie unter "
        "<font face='Courier'>sub_category_aliases</font> ein. Beispiel: "
        "<font face='Courier'>\"AI &amp; ML Tools\": \"Engineering Copilots &amp; AI "
        "Assistants\"</font>. Der Befehl <i>repair</i> raeumt sie dann automatisch "
        "auf.")]
    s.append(PageBreak())

    # ============================================================== 9
    s += h1("9. Spalten ergaenzen")
    s += [p("Angenommen, die Tabelle soll eine neue Spalte <b>Funding_Total</b> "
            "bekommen. Dafuer sind vier Schritte noetig - in genau dieser "
            "Reihenfolge.")]
    s += [Spacer(1, 4), callout(
        "Warum das nicht mit Excel allein geht",
        "Das Tool kennt die 31 Spalten der Tabelle als festen Vertrag. Legen Sie "
        "eine Spalte nur in Excel an, wird sie nie gefuellt - und das Tool bricht "
        "beim naechsten Lauf mit einer Schema-Meldung ab.", warn=True)]

    s += [Spacer(1, 8), Paragraph("Schritt 1: Spalte in der Excel-Datei anlegen", S_H2)]
    s += [p("Neue Spalte <b>rechts an das Ende</b> setzen, also hinter "
            "<i>Beachhead_Relevanz</i>. In die Kopfzeile den Namen schreiben: "
            "<font face='Courier'>Funding_Total</font>.")]

    s += [Paragraph("Schritt 2: Spalte im Vertrag eintragen", S_H2)]
    s += [p("Datei <font face='Courier'>cintel\\schema.py</font> oeffnen. Ganz oben "
            "steht die Liste <font face='Courier'>COLUMNS</font>. Am Ende - vor der "
            "schliessenden eckigen Klammer - eine Zeile ergaenzen:")]
    s += [code("""    ("beachhead",     "Beachhead_Relevanz"),
    ("funding_total", "Funding_Total"),        # <- neue Zeile
]""")]
    s += [Spacer(1, 3), Paragraph(
        "Links der interne Kurzname in Kleinbuchstaben, rechts die Excel-Kopfzeile "
        "in <b>exakt</b> derselben Schreibweise. Beides in Anfuehrungszeichen, am "
        "Ende ein Komma.", S_SMALL)]

    s += [Paragraph("Schritt 3: Feld zur Auswertung hinzufuegen", S_H2)]
    s += [p("Datei <font face='Courier'>cintel\\extract.py</font>. In der Funktion "
            "<font face='Courier'>build_schema</font> steht der Block "
            "<font face='Courier'>company_schema</font>. Dort bei "
            "<font face='Courier'>\"properties\"</font> ergaenzen:")]
    s += [code('            "funding_total": {"type": "string"},')]
    s += [p("Danach, weiter unten in der Funktion "
            "<font face='Courier'>_to_records</font>, im Block "
            "<font face='Courier'>company_values</font>:")]
    s += [code('            "funding_total": _clean(info.get("funding_total")),')]

    s += [Paragraph("Schritt 4: Pruefen", S_H2)]
    s += [p("Die mitgelieferten Tests stellen sicher, dass Sie nichts zerbrochen "
            "haben. Sie muessen alle durchlaufen:")]
    s += [code("py -m pytest tests/ -q")]
    s += [p("Erwartete Ausgabe am Ende: <font face='Courier'>passed</font> - es darf "
            "kein <font face='Courier'>failed</font> auftauchen. Dann ein kleiner "
            "Testlauf mit zwei Firmen:")]
    s += [code('py -m cintel run --master "data\\outputs\\Competitive_Intel_Master_DB_v2.2r.xlsx" --limit 2 --dry-run')]

    s += [Spacer(1, 8), callout(
        "Erscheint eine Meldung ueber fehlende Pflichtspalten?",
        "Dann stimmt der Spaltenname in <font face='Courier'>schema.py</font> nicht "
        "genau mit der Excel-Kopfzeile ueberein. Achten Sie auf Gross- und "
        "Kleinschreibung, Unterstriche und versehentliche Leerzeichen am Ende.",
        warn=True)]
    s.append(PageBreak())

    # ============================================================== 10
    s += h1("10. Laeufe automatisieren")
    s += [p("Bisher haben Sie jeden Lauf von Hand gestartet und alle Einstellungen "
            "als Schalter mitgegeben. Fuer wiederkehrende Recherchen gibt es einen "
            "bequemeren Weg: <b>Profile</b>.")]

    s += [Paragraph("10.1 Was ein Profil ist", S_H2)]
    s += [p("Ein Profil ist ein gespeicherter Suchauftrag. Es haelt fest, welche "
            "Branche, welche Funktionsbereiche, welche Region und welchen Reifegrad "
            "Sie suchen. Statt eines langen Befehls mit vielen Schaltern rufen Sie "
            "das Profil dann nur beim Namen.")]
    s += [p("Welche Profile es gibt, zeigt dieser Befehl:")]
    s += [code("py -m cintel profiles")]
    s += [Spacer(1, 4), table([
        ["Profil", "Was es tut"],
        ["<b>bestand-luecken</b>", "Fuellt Luecken bei bekannten Firmen. Der "
                                   "Standardlauf fuer die laufende Pflege"],
        ["<b>tier1-tiefenpruefung</b>", "Nur direkte Wettbewerber, mehr Unterseiten, "
                                        "staerkeres Modell. Gruendlich und langsamer"],
        ["<b>lca-startups-dach</b>", "Sucht neue Anbieter fuer Oekobilanz und CO2 im "
                                     "deutschsprachigen Raum"],
        ["<b>engineering-ki-europa</b>", "Sucht neue KI-Assistenten fuer Konstruktion "
                                         "und Simulation in Europa"],
        ["<b>angebotsphase-rfq</b>", "Sucht Anbieter rund um Angebotskalkulation und "
                                     "Anfragebearbeitung"],
    ], [45 * mm, 120 * mm])]

    s += [Spacer(1, 6), Paragraph("10.2 Ein Profil starten", S_H2)]
    s += [code('py -m cintel run --master "data\\outputs\\Competitive_Intel_Master_DB_v2.2r.xlsx" --profile lca-startups-dach')]
    s += [p("Alles Weitere - Branche, Funktionsbereiche, Region, Reifegrad, "
            "Firmenzahl - steckt im Profil. Einzelne Angaben lassen sich trotzdem "
            "ueberschreiben; ein mitgegebener Schalter hat immer Vorrang:")]
    s += [code('py -m cintel run --master "<datei.xlsx>" --profile lca-startups-dach --limit 3 --dry-run')]

    s += [Spacer(1, 6), Paragraph("10.3 Ein eigenes Profil anlegen", S_H2)]
    s += [p("Datei <font face='Courier'>config\\profiles.yaml</font> oeffnen und "
            "einen neuen Abschnitt anhaengen. Dieses Beispiel sucht "
            "Medizintechnik-Anbieter in Europa:")]
    s += [code("""  medtech-europa:
    description: >
      Sucht Anbieter mit Schwerpunkt Medizintechnik in Europa.
    mode: new
    limit: 12
    key_categories:
      - "4. Lifecycle & Data Management (PLM/PDM)"
    sub_categories:
      - "PLM & PDM Platforms"
      - "MBSE & Systems Engineering"
    regions:
      - "Europe"
    stages:
      - "Series A"
      - "Series B"
    inclusion_criteria: >
      Software mit nachweisbarem Bezug zur Medizintechnik.
    exclusion_criteria: >
      Reine Beratung ohne eigenes Produkt.""")]
    s += [Spacer(1, 4), bullets([
        "Die Einrueckung muss genau stimmen: der Profilname zwei Leerzeichen "
        "eingerueckt, seine Eigenschaften vier.",
        "Alle Kategorienamen muessen <b>exakt</b> so geschrieben sein wie in "
        "<font face='Courier'>config\\taxonomy.yaml</font>.",
        "Die Sub-Kategorien muessen zu den gewaehlten Hauptkategorien passen. "
        "Welche wohin gehoeren, steht in <font face='Courier'>taxonomy.yaml</font> "
        "im Abschnitt <font face='Courier'>legend</font>.",
        "Pruefen Sie danach mit <font face='Courier'>py -m cintel profiles</font>. "
        "Stimmt etwas nicht, steht dort <i>FEHLER</i> mit Begruendung - noch bevor "
        "ein Lauf startet.",
    ])]
    s += [Spacer(1, 4), callout(
        "Warum die Zuordnung wichtig ist",
        "Passt eine Sub-Kategorie nicht zur Hauptkategorie, wuerde die Suche sie "
        "stillschweigend verwerfen und stattdessen ueber <i>alle</i> Sub-Kategorien "
        "laufen. Sie bekaemen Ergebnisse - nur eben zu etwas anderem als gedacht. "
        "Deshalb meldet die Profilpruefung diesen Fall als Fehler.")]

    s += [Spacer(1, 8), Paragraph("10.4 Zeitgesteuert laufen lassen", S_H2)]
    s += [p("Weil ein Profillauf immer derselbe kurze Befehl ist, kann Windows ihn "
            "selbsttaetig starten - etwa jeden Montagmorgen. Dafuer liegen zwei "
            "Skripte bereit.")]

    s += [Paragraph("Einen Zeitplan einrichten", S_H3)]
    s += [code(".\\scripts\\Register-CintelSchedule.ps1 -ProfileName bestand-luecken -Schedule Weekly -DayOfWeek Monday -Time 06:30")]
    s += [p("Das legt in der Windows-Aufgabenplanung einen Eintrag an. Ab jetzt "
            "laeuft die Recherche jeden Montag um 6:30 Uhr, und Sie finden das "
            "Ergebnis im gewohnten Ausgabeordner.")]
    s += [Spacer(1, 4), table([
        ["Schalter", "Bedeutung"],
        ["<font face='Courier'>-ProfileName</font>", "Welches Profil laufen soll"],
        ["<font face='Courier'>-Schedule</font>", "Daily, Weekly oder Monthly"],
        ["<font face='Courier'>-DayOfWeek</font>", "Nur bei Weekly, z.B. Monday"],
        ["<font face='Courier'>-Time</font>", "Uhrzeit im Format HH:mm"],
        ["<font face='Courier'>-Remove</font>", "Entfernt den Zeitplan wieder"],
    ], [42 * mm, 123 * mm])]

    s += [Spacer(1, 6), Paragraph("Sofort testen, ohne auf den Termin zu warten", S_H3)]
    s += [code('Start-ScheduledTask -TaskName "cintel - bestand-luecken"')]

    s += [Paragraph("Zeitplan wieder entfernen", S_H3)]
    s += [code(".\\scripts\\Register-CintelSchedule.ps1 -ProfileName bestand-luecken -Remove")]

    s += [Spacer(1, 8), Paragraph("10.5 Was Sie dabei wissen muessen", S_H2)]
    s += [Spacer(1, 2), callout(
        "Der Rechner muss laufen und Sie muessen angemeldet sein",
        "Die Aufgabe startet unter Ihrem Windows-Konto, weil die Anmeldung der "
        "claude-Anwendung daran haengt. Ist der Rechner aus oder sind Sie "
        "abgemeldet, faellt der Termin aus. Windows holt ihn beim naechsten "
        "Anmelden nach.", warn=True)]
    s += [Spacer(1, 6), p("Jeder automatische Lauf schreibt ein Protokoll. Dort "
                          "steht, was passiert ist - auch wenn Sie nicht dabei "
                          "waren:")]
    s += [code("explorer data\\logs")]
    s += [p("Das Runner-Skript prueft vor dem Start zwei Dinge und bricht mit "
            "verstaendlicher Meldung ab, wenn etwas fehlt: ob die Master-Tabelle "
            "erreichbar ist - bei Laufwerk H: kann Google Drive getrennt sein - und "
            "ob die claude-Anwendung vorhanden ist.")]
    s += [Spacer(1, 6), callout(
        "Falls ein Skript nicht starten will",
        "Meldet PowerShell, die Ausfuehrung von Skripten sei deaktiviert, wurde die "
        "Datei als \"aus dem Internet\" eingestuft. Einmalig freigeben mit: "
        "<font face='Courier'>Unblock-File .\\scripts\\*.ps1</font>")]
    s += [Spacer(1, 6), Paragraph("Ein sinnvoller Rhythmus", S_H3)]
    s += [table([
        ["Profil", "Vorschlag", "Warum"],
        ["bestand-luecken", "woechentlich, Montag frueh",
         "Haelt die vorhandenen Firmen aktuell"],
        ["lca-startups-dach", "monatlich",
         "Neue Startups tauchen nicht woechentlich auf"],
        ["engineering-ki-europa", "monatlich", "dasselbe"],
        ["tier1-tiefenpruefung", "von Hand, vor Quartalsberichten",
         "Dauert laenger und will begutachtet werden"],
    ], [42 * mm, 48 * mm, 75 * mm])]
    s += [Spacer(1, 6), callout(
        "Automatisch heisst nicht ungeprueft",
        "Auch ein zeitgesteuerter Lauf schreibt nur in den Ausgabeordner auf Ihrem "
        "Rechner. Ob das Ergebnis nach H: ins Team-Laufwerk wandert, entscheiden "
        "weiterhin Sie - nach einem Blick in <i>report.md</i>.")]
    s.append(PageBreak())

    # ============================================================== 11
    s += h1("11. Aenderungen ueber GitHub einbringen")
    s += [p("GitHub ist der gemeinsame Ablageort fuer den Programmcode. Er sorgt "
            "dafuer, dass jede Aenderung nachvollziehbar ist und rueckgaengig "
            "gemacht werden kann. Erinnerung aus Kapitel 3: <b>Der Abgleich "
            "passiert nicht von selbst</b> - Sie stossen ihn an.")]

    s += [Paragraph("11.1 Die vier Begriffe, die Sie brauchen", S_H2)]
    s += [table([
        ["Begriff", "Erklaerung in einem Satz"],
        ["<b>Branch</b> (Zweig)", "Eine Arbeitskopie, in der Sie etwas aendern, ohne "
                                  "die funktionierende Fassung anzufassen"],
        ["<b>Commit</b>", "Ein gespeicherter Zwischenstand mit Notiz, was Sie "
                          "geaendert haben"],
        ["<b>Push</b>", "Ihre Commits zu GitHub hochladen"],
        ["<b>Pull Request</b> (PR)", "Der Antrag: \"Bitte uebernehmt meine "
                                     "Aenderung in die Hauptfassung\". Hier wird "
                                     "geprueft und besprochen"],
    ], [38 * mm, 127 * mm])]

    s += [Spacer(1, 6), Paragraph("11.2 Der vollstaendige Ablauf", S_H2)]
    s += [p("Immer dieselben sechs Schritte. Alle Befehle im Projektordner eingeben.")]

    s += [Paragraph("Schritt 1 &mdash; Auf den aktuellen Stand gehen", S_H3)]
    s += [code("git checkout main\ngit pull")]

    s += [Paragraph("Schritt 2 &mdash; Eigenen Zweig anlegen", S_H3)]
    s += [code("git checkout -b feat/funding-spalte")]
    s += [Paragraph("Der Name ist frei waehlbar. Ueblich sind die Vorsilben "
                    "<font face='Courier'>feat/</font> fuer Neues und "
                    "<font face='Courier'>fix/</font> fuer Fehlerbehebungen.", S_SMALL)]

    s += [Paragraph("Schritt 3 &mdash; Aendern und pruefen", S_H3)]
    s += [p("Jetzt die Dateien bearbeiten. Danach <b>unbedingt</b> die Tests laufen "
            "lassen - vor dem Speichern, nicht danach:")]
    s += [code("py -m pytest tests/ -q")]

    s += [Paragraph("Schritt 4 &mdash; Zwischenstand speichern", S_H3)]
    s += [code('git add -A\ngit commit -m "feat: Spalte Funding_Total ergaenzt"')]
    s += [Paragraph("Die Notiz hinter <font face='Courier'>-m</font> beschreibt kurz, "
                    "<i>was</i> Sie geaendert haben. Uebliche Vorsilben: "
                    "<font face='Courier'>feat:</font> neue Funktion, "
                    "<font face='Courier'>fix:</font> Fehlerbehebung, "
                    "<font face='Courier'>docs:</font> Dokumentation, "
                    "<font face='Courier'>test:</font> Tests.", S_SMALL)]

    s += [Paragraph("Schritt 5 &mdash; Hochladen", S_H3)]
    s += [code("git push -u origin feat/funding-spalte")]

    s += [Paragraph("Schritt 6 &mdash; Pull Request eroeffnen", S_H3)]
    s += [code("gh pr create --fill")]
    s += [p("Der Befehl gibt eine Internetadresse aus. Oeffnen Sie sie - dort sehen "
            "Sie Ihre Aenderung und die automatische Pruefung.")]

    s += [Spacer(1, 6), Paragraph("11.3 Die automatische Pruefung", S_H2)]
    s += [p("Sobald der Pull Request offen ist, prueft GitHub den Code selbsttaetig "
            "auf drei Python-Versionen. Status abfragen:")]
    s += [code("gh pr checks")]
    s += [table([
        ["Anzeige", "Bedeutung"],
        ["<font face='Courier'>pass</font>", "Alles in Ordnung - der PR kann "
                                             "uebernommen werden"],
        ["<font face='Courier'>fail</font>", "Etwas ist kaputt. Auf die angezeigte "
                                             "Adresse klicken, Meldung lesen, "
                                             "korrigieren, dann Schritt 3 bis 5 "
                                             "wiederholen"],
        ["<font face='Courier'>pending</font>", "Laeuft noch. Einen Moment warten und "
                                                "erneut abfragen"],
    ], [32 * mm, 133 * mm])]

    s += [Spacer(1, 6), Paragraph("11.4 Uebernehmen und aufraeumen", S_H2)]
    s += [p("Wenn alle Pruefungen bestanden sind und der Inhalt passt:")]
    s += [code("gh pr merge --squash --delete-branch")]
    s += [p("Danach zurueck auf die Hauptfassung:")]
    s += [code("git checkout main\ngit pull")]

    s += [Spacer(1, 8), callout(
        "Diese Dateien duerfen nie nach GitHub",
        "Excel-Dateien, der Ordner <font face='Courier'>data</font> und die Datei "
        "<font face='Courier'>.env</font> sind ausgeschlossen - dort stehen "
        "Wettbewerbsdaten und Zugangsdaten. Die Sperre ist eingerichtet. Pruefen "
        "koennen Sie es vor dem Hochladen mit <font face='Courier'>git status</font>: "
        "taucht dort eine <font face='Courier'>.xlsx</font> auf, halten Sie an und "
        "fragen nach.", warn=True)]
    s.append(PageBreak())

    # ============================================================== 12
    s += h1("12. Fehlermeldungen nachschlagen")
    s += [table([
        ["Meldung auf dem Bildschirm", "Was zu tun ist"],
        ["<font face='Courier'>Die Benennung \"py\" wurde nicht ... erkannt</font>",
         "Python fehlt, oder das Terminal steht im falschen Ordner. Erst "
         "<font face='Courier'>py --version</font> pruefen, dann Kapitel 2.4"],
        ["<font face='Courier'>Not logged in &middot; Please run /login</font>",
         "Die claude-Anwendung ist nicht angemeldet. "
         "<font face='Courier'>claude</font> starten, "
         "<font face='Courier'>/login</font> eingeben"],
        ["<font face='Courier'>claude-CLI wurde nicht gefunden</font>",
         "Die Anwendung ist nicht installiert oder dem System nicht bekannt. Mit "
         "<font face='Courier'>claude --version</font> pruefen"],
        ["<font face='Courier'>Master-DB nicht gefunden</font>",
         "Der Pfad hinter <font face='Courier'>--master</font> stimmt nicht. Auf die "
         "Anfuehrungszeichen achten und pruefen, ob Laufwerk H: verbunden ist"],
        ["<font face='Courier'>Pflichtspalte(n) nicht gefunden</font>",
         "Die Excel-Datei hat nicht die erwarteten 31 Spalten. Wurde eine Spalte "
         "umbenannt oder geloescht? Siehe Kapitel 9"],
        ["<font face='Courier'>Profil '...' gibt es nicht</font>",
         "Tippfehler im Profilnamen. Liste anzeigen mit "
         "<font face='Courier'>py -m cintel profiles</font>"],
        ["<font face='Courier'>codex ist nicht angemeldet</font>",
         "Betrifft nur <font face='Courier'>--cross-check codex</font>. "
         "Einmalig <font face='Courier'>codex login</font> ausfuehren. Der "
         "Lauf selbst geht trotzdem vollstaendig durch - die Zweitpruefung "
         "entfaellt lediglich"],
        ["<font face='Courier'>Sub Category '...' passt zu keiner der "
         "gewaehlten Key Categories</font>",
         "In Ihrem Profil gehoert eine Sub-Kategorie nicht zur gewaehlten "
         "Hauptkategorie. Die Meldung nennt, wohin sie gehoert. Siehe "
         "Kapitel 10.3"],
        ["<font face='Courier'>durch robots.txt untersagt</font>",
         "Kein Fehler. Diese Firma verbietet automatisches Auslesen. Die Angaben "
         "muessen von Hand ergaenzt werden"],
        ["<font face='Courier'>Firmenname nicht auf der Seite gefunden</font>",
         "Die hinterlegte Adresse fuehrt zur falschen Seite. Die Spalte URL in der "
         "Tabelle pruefen und berichtigen"],
        ["<font face='Courier'>Homepage nicht erreichbar</font>",
         "Die Adresse existiert nicht mehr oder ist falsch geschrieben. Im Browser "
         "aufrufen und in der Tabelle korrigieren"],
        ["<font face='Courier'>0 neue Zeilen</font>",
         "Kein Fehler - es gab nichts zu ergaenzen. <i>report.md</i> im Lauf-Ordner "
         "nennt den Grund fuer jedes uebersprungene Feld"],
        ["<font face='Courier'>Die Ausfuehrung von Skripts ist deaktiviert</font>",
         "Betrifft die .ps1-Skripte aus Kapitel 10. Einmalig freigeben mit "
         "<font face='Courier'>Unblock-File .\\scripts\\*.ps1</font>"],
        ["Umlaute erscheinen als Kaestchen",
         "Vor dem Befehl einmalig <font face='Courier'>chcp 65001</font> eingeben. "
         "Betrifft nur die Anzeige, nicht die Daten"],
    ], [62 * mm, 103 * mm])]
    s.append(PageBreak())

    # ============================================================== 13
    s += h1("13. Befehlsuebersicht")
    s += [p("Alle Befehle werden im Projektordner eingegeben. Dorthin gelangen Sie "
            "mit:")]
    s += [code(f'cd "{PROJ}"')]

    s += [Spacer(1, 6), Paragraph("Recherche", S_H2)]
    s += [table([
        ["Zweck", "Befehl"],
        ["Voraussetzungen pruefen", "py -m cintel doctor"],
        ["Profile anzeigen", "py -m cintel profiles"],
        ["Erster Lauf, 5 Firmen",
         "py -m cintel run --master \"data\\outputs\\...v2.2r.xlsx\" --limit 5 "
         "--version 2.3"],
        ["Lauf mit Profil", "py -m cintel run --master \"&lt;datei.xlsx&gt;\" "
                            "--profile bestand-luecken"],
        ["Nur anschauen, nichts schreiben", "... --dry-run"],
        ["Neue Firmen suchen", "... --mode new --limit 10"],
        ["Mit Zweitpruefung", "... --cross-check codex"],
        ["Nur Webseiten laden", "... --crawl-only"],
        ["Ohne Internet, nur Zwischenspeicher", "... --offline"],
    ], [52 * mm, 113 * mm])]

    s += [Spacer(1, 8), Paragraph("Tabelle pruefen und bereinigen", S_H2)]
    s += [table([
        ["Zweck", "Befehl"],
        ["Auf Fehler pruefen", "py -m cintel validate --master \"&lt;datei.xlsx&gt;\""],
        ["Bereinigung anzeigen",
         "py -m cintel repair --master \"&lt;datei.xlsx&gt;\" --dry-run"],
        ["Bereinigung ausfuehren",
         "py -m cintel repair --master \"&lt;datei.xlsx&gt;\" --version 2.2r"],
        ["Struktur und Fuellgrade ansehen",
         "py scripts\\inspect_master_db.py \"&lt;datei.xlsx&gt;\""],
    ], [52 * mm, 113 * mm])]

    s += [Spacer(1, 8), Paragraph("Automatisierung", S_H2)]
    s += [table([
        ["Zweck", "Befehl"],
        ["Profillauf von Hand starten",
         ".\\scripts\\Run-CintelProfile.ps1 -ProfileName bestand-luecken"],
        ["Zeitplan einrichten",
         ".\\scripts\\Register-CintelSchedule.ps1 -ProfileName bestand-luecken "
         "-Schedule Weekly -DayOfWeek Monday -Time 06:30"],
        ["Zeitplan sofort testen",
         "Start-ScheduledTask -TaskName \"cintel - bestand-luecken\""],
        ["Zeitplan entfernen",
         ".\\scripts\\Register-CintelSchedule.ps1 -ProfileName bestand-luecken -Remove"],
        ["Protokolle ansehen", "explorer data\\logs"],
    ], [52 * mm, 113 * mm])]

    s += [Spacer(1, 8), Paragraph("Entwicklung und GitHub", S_H2)]
    s += [table([
        ["Zweck", "Befehl"],
        ["Tests ausfuehren", "py -m pytest tests/ -q"],
        ["Code pruefen", "py -m ruff check cintel tests scripts"],
        ["Neuen Zweig anlegen", "git checkout -b feat/mein-thema"],
        ["Was habe ich geaendert?", "git status"],
        ["Zwischenstand speichern", "git add -A  danach  git commit -m \"feat: ...\""],
        ["Hochladen", "git push -u origin feat/mein-thema"],
        ["Pull Request eroeffnen", "gh pr create --fill"],
        ["Pruefstatus ansehen", "gh pr checks"],
        ["Uebernehmen", "gh pr merge --squash --delete-branch"],
        ["Handbuch neu erzeugen", "py docs\\build_handbuch.py"],
    ], [52 * mm, 113 * mm])]

    s += [Spacer(1, 12), callout(
        "Im Zweifel gilt",
        "Sie koennen mit diesem Tool nichts zerstoeren. Die Master-Datei auf H:\\ "
        "wird ausschliesslich gelesen. Jedes Ergebnis ist eine neue Datei mit "
        "eigener Versionsnummer. Wenn ein Lauf misslingt, loeschen Sie die "
        "entstandene Datei und starten neu.")]

    return s


if __name__ == "__main__":
    path = build()
    print(f"Handbuch geschrieben: {path}  ({path.stat().st_size / 1024:.0f} KB)")
