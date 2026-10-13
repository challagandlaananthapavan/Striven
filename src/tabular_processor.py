from __future__ import annotations

from pathlib import Path
import pandas as pd


# Convert dataframe rows into structured text units.
def dataframe_to_units(
    df: pd.DataFrame,
    source: str,
    sheet: str | None = None
) -> list[dict]:

    units = []
    columns = [str(c).strip() for c in df.columns]

    for zero_idx, row in df.iterrows():
        pairs = []

        for col, value in zip(columns, row.tolist()):
            if pd.isna(value):
                continue

            value_text = str(value).strip()

            if value_text:
                pairs.append(f"{col}: {value_text}")

        if not pairs:
            continue

        text = " | ".join(pairs)
        row_num = int(zero_idx) + 2

        units.append({
            "text": text,
            "source": source,
            "source_type": "excel" if sheet else "csv",
            "sheet": sheet,
            "row_start": row_num,
            "row_end": row_num,
        })

    return units


# Read Excel sheets and convert their rows into units.
def process_excel(path: Path) -> list[dict]:

    units = []
    sheets = pd.read_excel(path, sheet_name=None)

    for sheet_name, df in sheets.items():
        units.extend(
            dataframe_to_units(
                df,
                path.name,
                str(sheet_name)
            )
        )

    return units


# Read CSV data and convert its rows into units.
def process_csv(path: Path) -> list[dict]:

    try:
        df = pd.read_csv(path)

    except UnicodeDecodeError:
        df = pd.read_csv(
            path,
            encoding="latin-1"
        )

    return dataframe_to_units(
        df,
        path.name
    )