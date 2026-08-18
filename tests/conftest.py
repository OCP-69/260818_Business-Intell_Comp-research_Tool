"""Fixtures - erzeugen eine Mini-Master-DB, damit keine echte Datei noetig ist."""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cintel.schema import ROW_KIND_COMPANY, ROW_KIND_PRODUCT, Taxonomy

REPO_ROOT = Path(__file__).resolve().parents[1]

# Header genau so verschraenkt wie in der echten v2.2: mehrzeilig und mit
# Trailing-Space. Damit testet die Suite die tolerante Header-Aufloesung.
REAL_HEADERS = [
    "Company_ID", "Last_Update", "Company", "Product_name", "Company & Product",
    "All_Key_Categories of this company", "Sub Category B",
    "Remarks Product/solution", "Sustainability Features",
    "R-Strategies\nAVOID (refuse, rethink, reduce)\nREUSE (Reuse, repair, "
    "refurbish, remanufacture, repurpose)\nRECYCLE (recycle, recover)",
    "R-Strategy: Rationale \nto categorization",
    "LCA Capabilities", "Regulatory Compliance", "Technology/Innovation",
    "Key customers", "Strengths", "Weaknesses", "USP", "Target Market",
    "Pricing Model", "Business Model", "URL", "Founding Year", "Stage/ Status",
    "Founding/ founding type", "Location", "# Employees", "Revenue (Year)",
    "LFL_Dimension", "Competitor_Tier", "Beachhead_Relevanz",
]


@pytest.fixture(scope="session")
def taxonomy() -> Taxonomy:
    return Taxonomy.load(REPO_ROOT / "config" / "taxonomy.yaml")


@pytest.fixture
def mini_master(tmp_path: Path) -> Path:
    """
    Master-DB mit den Mangelklassen der echten v2.2:
      - Company_ID als Float
      - Tier mit Bindestrich statt Halbgeviertstrich
      - Seitentitel im URL-Feld
      - mehrwertig verschmutztes URL-Feld
      - Drift-Variante in Sub Category B
      - leere, aber vorhandene Zeilen am Ende
    """
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Competitors_All-Master"
    sheet.append(REAL_HEADERS)

    def row(**kwargs) -> list:
        record = [None] * len(REAL_HEADERS)
        index = {
            "id": 0, "updated": 1, "company": 2, "product": 3, "kind": 4,
            "keycats": 5, "sub": 6, "remarks": 7, "url": 21, "founded": 22,
            "stage": 23, "location": 25, "tier": 29, "beachhead": 30,
        }
        for key, value in kwargs.items():
            record[index[key]] = value
        return record

    # Firma 1: saubere Company-Zeile + 1 Produkt
    sheet.append(row(id=1.0, company="Makersite", kind=ROW_KIND_COMPANY,
                     url="https://makersite.io/ | Berlin, Germany",
                     keycats="5. Sustainability & Compliance (LCA/DPP)",
                     tier="Tier 1 - Direkt", beachhead="Cross-cutting",
                     founded="2018", location="Stuttgart"))
    sheet.append(row(id=1.0, company="Makersite", product="Makersite Platform",
                     kind=ROW_KIND_PRODUCT, sub="LCA Software & Platforms",
                     url="https://makersite.io/platform", tier="Tier 1 - Direkt"))
    # Firma 2: Company-Zeile ohne Produkte, Seitentitel im URL-Feld
    sheet.append(row(id=2.0, company="Autodesk", kind=ROW_KIND_COMPANY,
                     url="Was ist PLM? | Product Lifecycle Management | Autodesk",
                     sub="8. Other / Cross-cutting", tier="Tier 2 - Nachbar",
                     beachhead="Cross-cutting"))
    # Firma 3: nur Produktzeile, kaputte Kodierung
    sheet.append(row(id=3.0, company="Physna", product="Physna Core",
                     kind=ROW_KIND_PRODUCT, remarks="N/A (nicht Ã¶ffentlich)",
                     url="https://physna.com", tier="Tier 3 - Beobachten",
                     beachhead="Cross-cutting"))
    # Leerzeilen wie in der echten Datei
    for _ in range(5):
        sheet.append([None] * len(REAL_HEADERS))

    legend = workbook.create_sheet("Legend_Categories")
    legend.append(["Key Category", "Connected Sub-Categories"])
    legend.append(["5. Sustainability & Compliance (LCA/DPP)",
                   "LCA Software & Platforms; Carbon & Eco-Design"])

    target = tmp_path / "Competitive_Intel_Master_DB_v2.2.xlsx"
    workbook.save(target)
    return target
