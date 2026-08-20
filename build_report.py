"""Analyse the whole universe and write the PDF report plus CSV outputs to reports/."""

from pathlib import Path

from fsi.reporting import main

if __name__ == "__main__":
    main(Path(__file__).parent)
