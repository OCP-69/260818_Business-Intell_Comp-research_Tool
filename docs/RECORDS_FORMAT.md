# records.json — Übergabeformat Agent → `cintel ingest`

Der Recherche-Agent liefert seine Ergebnisse als eine JSON-Datei und spielt
sie mit `py -m cintel ingest records.json --db <master.xlsx>` ein. Die CLI
validiert strikt, kanonisiert Vokabulare, dedupliziert gegen den Bestand und
schreibt eine **neue** DB-Version (fill-only, Original bleibt unangetastet).

## Struktur

```json
{
  "run": {"mode": "refresh-tier1", "date": "2026-09-01", "job": "auftrag_x.json"},
  "companies": [
    {
      "company": "Tset",
      "confidence": 0.8,
      "sources": ["https://www.tset.com", "https://..."],
      "company_row": {
        "url": "https://www.tset.com",
        "location": "Vienna, Austria",
        "founding_year": 2018,
        "stage": "Series A",
        "employees": "51-200",
        "revenue": "",
        "founding_type": "Series A",
        "key_categories": "7. Supply Chain & Operations",
        "strengths": "...", "weaknesses": "...", "usp": "...",
        "target_market": "...", "pricing_model": "...", "business_model": "B2B SaaS",
        "technology": "...", "key_customers": "...",
        "lfl_dimension": "RFQ/CPQ", "competitor_tier": "Tier 1 – Direkt",
        "beachhead": "Machinery"
      },
      "products": [
        {
          "product_name": "Tset Platform",
          "sub_category": "Cost Engineering & Value Analysis",
          "remarks": "...", "sustainability": "...", "lca": "...",
          "compliance": "...", "usp": "...", "pricing_model": "...",
          "url": "https://www.tset.com/platform",
          "confidence": 0.8
        }
      ]
    }
  ]
}
```

## Regeln

- **Zeilenmodell:** `company_row` wird zur Zeile `Company & Product =
  "Company information"`, jedes Element in `products` zu einer `"Product"`-
  Zeile derselben Company_ID. Firmenweite Felder (founding_year, stage,
  employees, revenue, location, key_categories, founding_type) gehören NUR in
  `company_row`.
- **Feldnamen** sind die internen Namen aus `cintel/schema.py` (COLUMNS).
  Unbekannte Feldnamen werden ignoriert und im Report vermerkt.
- **Leere statt "N/A":** Nicht Belegbares weglassen oder `""`. Werte, die mit
  "N/A" beginnen, werden beim Ingest verworfen (N/A-Politik v3).
- **Vokabulare** (key_categories, sub_category, competitor_tier, beachhead)
  werden gegen `config/taxonomy.yaml` kanonisiert; nicht auflösbare Werte
  werden verworfen und im Report gelistet.
- **confidence** (0–1) je Firma, optional je Produkt. Unter der Schwelle
  (Default 0.35) wird nichts übernommen.
- **sources**: mindestens eine abrufbare URL je Firma; landet in
  `sources.csv` des Laufs.
- **Dubletten**: bekannte Firmen (Domain-/Namens-Match) werden ergänzt
  (nur leere Felder), nicht neu angelegt. Kuratierte Werte gewinnen immer.
